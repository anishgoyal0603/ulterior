"""
Background job execution for audit runs.

A full funnel walk takes anywhere from a few seconds (local fixtures) to
a couple of minutes (a real multi-step checkout with network latency), so
this cannot run inside the HTTP request. This module uses a simple
in-process ThreadPoolExecutor -- deliberately, so it's something you can
run and verify with zero extra infrastructure (no Redis, no separate
worker process) while building and testing.

DURABLE JOBS (2.4). The database is the queue: every audit is stored with
everything needed to run it (request_json) before any thread touches it.
Threads only CLAIM a job with an atomic "pending -> running" update, so two
workers -- in one process or several -- can never run the same audit twice.
A running job writes a heartbeat; a job whose heartbeat stops (the server
restarted, the worker was killed) is put back in the queue, up to
JOB_MAX_ATTEMPTS, and then marked failed with a plain explanation. Pending
jobs left behind by a restart are picked up again by the sweep. No Redis,
no new service: PostgreSQL is already required in production.
"""

import asyncio
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
from typing import List, Optional

import os

from capture.funnel_walker import walk_funnel, DEFAULT_EVIDENCE_DIR
from capture.funnel_discovery import walk_discovered_funnel
from capture.adapters.base import SiteAdapter, FunnelStep
from detectors.pipeline import audit
from .db import SessionLocal
from .models import AuditJob, ViolationRecord
from .registry import ADAPTER_REGISTRY
from .privacy import redact, redact_evidence, redact_list
from .integrity import build_manifest
from . import config
from sqlalchemy import func
from detectors.coverage import assess_coverage
from .ssrf_guard import validate_target_url, UnsafeURLError

logger = logging.getLogger(__name__)

def _prepare_worker_thread():
    """Make sure a Playwright browser can actually be launched from this thread.

    WINDOWS ONLY, and it is the difference between the demo working and the
    demo failing on every Windows machine.

    Playwright's sync API starts its driver as a SUBPROCESS. Creating a
    subprocess from asyncio on Windows requires a Proactor event loop; a
    Selector loop raises NotImplementedError. Uvicorn installs
    WindowsSelectorEventLoopPolicy at startup, and that policy is
    process-wide -- so when Playwright calls asyncio.new_event_loop() in this
    worker thread it gets a Selector loop and cannot spawn its driver. The
    audit then fails with NotImplementedError on Windows while working
    perfectly on Linux and in CI, which is exactly the shape of bug that
    survives a green test suite.

    Setting the policy here rather than at import time is deliberate: uvicorn
    sets it during startup, after this module is imported, so an import-time
    call would simply be overwritten. By the time a worker thread runs, the
    server's own loop already exists and keeps running -- changing the policy
    only affects loops created afterwards, which is precisely Playwright's.
    """
    if sys.platform == "win32":
        policy = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
        if policy is not None and not isinstance(asyncio.get_event_loop_policy(), policy):
            asyncio.set_event_loop_policy(policy())


_executor = ThreadPoolExecutor(max_workers=config.AUDIT_WORKERS, initializer=_prepare_worker_thread)
_queued_lock = threading.Lock()
_queued: set = set()     # job ids handed to this process's executor and not yet finished


def _build_adhoc_adapter(target_urls: List[str]) -> SiteAdapter:
    """
    Self-serve mode: turn a list of user-pasted URLs into a SiteAdapter with
    no code required, one FunnelStep per URL. Every URL is SSRF-validated
    HERE, before the job is even queued -- fail fast, don't burn a worker
    slot on a request that was always going to be rejected.
    """
    steps = []
    for i, url in enumerate(target_urls, start=1):
        validate_target_url(url)  # raises UnsafeURLError, caught by the caller
        steps.append(FunnelStep(name=f"step_{i}", url=url))
    site_name = target_urls[0].split("/")[2] if target_urls else "self-serve audit"
    return SiteAdapter(site_name=site_name, funnel_steps=steps)


def _run_job(job_id: int, adapter_name: Optional[str], target_urls: Optional[List[str]],
             use_llm: bool = False, auto_discover: bool = False):
    db = SessionLocal()
    try:
        job = db.get(AuditJob, job_id)
        if job is None:
            return      # deleted before a worker reached it
        job.status = "running"
        db.commit()

        # Screenshots go in a directory of this job's own. They used to be
        # named from the adapter and step alone, and discovery's step names are
        # the fixed literals entry/product/cart/checkout -- so EVERY discovery
        # audit ever run wrote to the same five files. Job 1's stored evidence
        # path then displayed job 2's screenshots, and deleting job 2 removed
        # the file job 1 still pointed at. On Windows two concurrent audits of
        # one adapter also collided on the open file handle and aborted.
        evidence_dir = os.path.join(DEFAULT_EVIDENCE_DIR, f"job_{job_id}")

        if auto_discover and target_urls:
            # Discovery mode: one URL in, and the crawler finds its own way to
            # the checkout. The URL was already SSRF-validated in
            # submit_audit_job before this job row was created.
            trace = walk_discovered_funnel(target_urls[0], screenshot_dir=evidence_dir)
            site_name = trace.site_name
        else:
            if adapter_name:
                adapter = ADAPTER_REGISTRY[adapter_name]
            else:
                adapter = _build_adhoc_adapter(target_urls)
            trace = walk_funnel(adapter, screenshot_dir=evidence_dir)
            site_name = adapter.site_name

        violations = audit(trace, use_llm=use_llm)

        # Deleted while the browser was walking: write nothing, and remove
        # the screenshots it took -- otherwise findings would be inserted for
        # an audit that no longer exists (an FK error on PostgreSQL, orphan
        # rows on SQLite) and the files would never be cleaned up.
        try:
            db.refresh(job)
        except Exception:
            db.rollback()
            if db.get(AuditJob, job_id) is None:
                import shutil
                shutil.rmtree(evidence_dir, ignore_errors=True)
                logger.info("Audit job %s was deleted while running; its results were discarded", job_id)
                return
            raise

        # Cancelled while the browser was walking: stop here, before any
        # finding is written. The screenshots already taken stay on disk
        # (until the retention purge) in case the person wants to look.
        if job.cancel_requested:
            job.status = "cancelled"
            job.site_name = site_name
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = ("Cancelled before it finished. No findings were recorded; "
                                 "screenshots taken so far are kept until the retention period ends.")
            db.commit()
            return

        job.site_name = site_name
        if trace.discovery:
            # The notes quote button and link text straight off the audited
            # page ("Clicked 'Deliver to Rahul Sharma, +91 98765 43210' ...").
            # Every other captured string is redacted before it reaches the
            # database; this one was not, so a logged-in checkout leaked a name
            # and phone number into audit_jobs and back out through the API.
            safe_discovery = dict(trace.discovery)
            for field in ("notes", "stages_reached", "stages_not_found"):
                if isinstance(safe_discovery.get(field), list):
                    safe_discovery[field] = redact_list(safe_discovery[field])
            job.discovery_json = json.dumps(safe_discovery)
        job.status = "done"
        job.completed_at = datetime.now(timezone.utc)

        stored = []
        for v in violations:
            # PRIVACY: everything derived from captured page content is
            # redacted before it touches the database. Numeric evidence
            # (prices, contrast ratios, pixel dimensions) survives intact —
            # that's the actual proof of a violation and carries no PII.
            evidence_json = json.dumps(redact_evidence(v.evidence))
            record = ViolationRecord(
                job_id=job.id, pattern_code=v.pattern_code, pattern_name=v.pattern_name,
                step_name=v.step_name, confidence=v.confidence, layer=v.layer,
                explanation=redact(v.explanation),
                evidence_json=evidence_json,
                # "cart -> checkout": the finding shows on the LAST page named
                # (where the fee appears), so that is the screenshot it cites.
                screenshot_path=(trace.screenshots.get(v.step_name.split(" -> ")[-1].strip())
                                 or trace.screenshots.get(v.step_name.split(" -> ")[0].strip())),
            )
            db.add(record)
            stored.append({
                "pattern_code": record.pattern_code, "step_name": record.step_name,
                "layer": record.layer, "confidence": record.confidence,
                "explanation": record.explanation, "evidence": json.loads(evidence_json),
            })

        # Fingerprint the evidence exactly as stored, in the same commit as the
        # findings, so a report can later be checked against it.
        job.evidence_manifest_json = json.dumps(
            build_manifest(job.id, site_name, trace.screenshots, stored)
        )
        # What each of the 13 patterns could and could not be checked on.
        job.coverage_json = json.dumps(assess_coverage(trace, [s_["pattern_code"] for s_ in stored]))
        db.commit()
    except Exception as e:
        # SECURITY: never persist or return the raw exception/traceback to a
        # client. A DB connection failure raises an error whose text contains
        # the full connection string INCLUDING the password, and SQLAlchemy
        # traces embed it too. The full detail goes to the server log (where
        # an operator can see it); the client gets only the exception type.
        logger.exception("Audit job %s failed", job_id)

        # ROLL BACK FIRST, for two independent reasons.
        #
        # 1. If the exception came from the commit above, SQLAlchemy has
        #    deactivated the transaction and EVERY later statement on this
        #    session raises PendingRollbackError -- including the ones right
        #    here that try to record the failure. That second exception
        #    escaped into a Future nobody inspects, so the job sat at
        #    "running" forever and the dashboard polled it until the user gave
        #    up. A job that fails must be able to say so.
        #
        # 2. Violations are added one at a time before a single commit, so a
        #    failure part-way through left half of them pending in the session
        #    -- and the commit below would have flushed them alongside
        #    status="failed" and a completed_at that was already assigned. The
        #    user got a report marked failed that nonetheless carried four of
        #    its nine findings, which is a truncated compliance report
        #    presented as a real one.
        try:
            db.rollback()
        except Exception:
            logger.exception("Rollback failed for audit job %s", job_id)

        try:
            job = db.get(AuditJob, job_id)
            if job is not None:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                job.error_message = (
                    f"Audit failed ({type(e).__name__}). "
                    f"See server logs for details (job id {job_id})."
                )
                db.commit()
        except Exception:
            # Last resort: a fresh session, because this one may be unusable.
            logger.exception("Could not mark audit job %s failed on its own session", job_id)
            try:
                with SessionLocal() as recovery:
                    row = recovery.get(AuditJob, job_id)
                    if row is not None:
                        row.status = "failed"
                        row.error_message = (
                            f"Audit failed ({type(e).__name__}). "
                            f"See server logs for details (job id {job_id})."
                        )
                        recovery.commit()
            except Exception:
                logger.exception("Audit job %s could not be marked failed at all", job_id)
    finally:
        db.close()


def submit_audit_job(adapter_name: Optional[str] = None, target_urls: Optional[List[str]] = None,
                     use_llm: bool = False, auto_discover: bool = False,
                     authorization: Optional[dict] = None, org: Optional[str] = None,
                     idempotency_key: Optional[str] = None, enforce_limit: bool = True) -> int:
    if not adapter_name and not target_urls:
        raise ValueError("Provide either adapter_name or target_urls.")
    if adapter_name and target_urls:
        raise ValueError("Provide adapter_name OR target_urls, not both.")

    if adapter_name and adapter_name not in ADAPTER_REGISTRY:
        raise ValueError(f"Unknown adapter '{adapter_name}'. Available: {list(ADAPTER_REGISTRY)}")

    if target_urls:
        # Validate BEFORE queuing -- an unsafe URL should never even become
        # a job row, let alone reach a worker thread.
        for url in target_urls:
            validate_target_url(url)  # raises UnsafeURLError on anything unsafe

    db = SessionLocal()
    try:
        if idempotency_key:
            existing = (db.query(AuditJob)
                        .filter(AuditJob.idempotency_key == idempotency_key, AuditJob.org == org)
                        .first())
            if existing is not None:
                return existing.id     # the same request sent twice makes one audit

        active = (db.query(AuditJob)
                  .filter(AuditJob.status.in_(("pending", "running")),
                          (AuditJob.org == org) if org is not None else AuditJob.org.is_(None))
                  .count())
        if enforce_limit and active >= config.MAX_ACTIVE_AUDITS_PER_ORG:
            raise TooManyActiveAudits(
                f"{active} audits are already queued or running for this workspace "
                f"(limit {config.MAX_ACTIVE_AUDITS_PER_ORG}). Wait for one to finish."
            )

        job = AuditJob(
            adapter_name=adapter_name or "self-serve", status="pending", org=org,
            idempotency_key=idempotency_key, attempts=0,
            authorization_json=json.dumps(authorization) if authorization else None,
            request_json=json.dumps({"adapter_name": adapter_name, "target_urls": target_urls,
                                     "use_llm": bool(use_llm), "auto_discover": bool(auto_discover)}),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    finally:
        db.close()

    if config.RUN_AUDIT_WORKERS:
        dispatch(job_id)
    return job_id


class TooManyActiveAudits(RuntimeError):
    """One organisation already has its share of browsers busy."""


# ------------------------------------------------------------ durable jobs

def dispatch(job_id: int) -> bool:
    """Hand a pending job to this process's workers (at most once)."""
    with _queued_lock:
        if job_id in _queued:
            return False
        _queued.add(job_id)
    _executor.submit(_claim_and_run, job_id)
    return True


def claim(job_id: int) -> Optional[dict]:
    """Atomically move one job from pending to running. Returns its request,
    or None if someone else claimed it, it was cancelled, or it has no stored
    request (made before 2.4)."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        updated = (db.query(AuditJob)
                   .filter(AuditJob.id == job_id, AuditJob.status == "pending")
                   .update({AuditJob.status: "running", AuditJob.heartbeat_at: now,
                            AuditJob.attempts: func.coalesce(AuditJob.attempts, 0) + 1},
                           synchronize_session=False))
        db.commit()
        if updated != 1:
            return None
        job = db.get(AuditJob, job_id)
        return json.loads(job.request_json) if job.request_json else None


def _heartbeat(job_id: int, stop: threading.Event) -> None:
    while not stop.wait(config.JOB_HEARTBEAT_SECONDS):
        try:
            with SessionLocal() as db:
                db.query(AuditJob).filter(AuditJob.id == job_id, AuditJob.status == "running") \
                  .update({AuditJob.heartbeat_at: datetime.now(timezone.utc)}, synchronize_session=False)
                db.commit()
        except Exception:
            logger.exception("Heartbeat failed for audit job %s", job_id)


def _claim_and_run(job_id: int) -> None:
    try:
        request = claim(job_id)
        if request is None:
            return
        stop = threading.Event()
        beat = threading.Thread(target=_heartbeat, args=(job_id, stop), daemon=True,
                                name=f"heartbeat-{job_id}")
        beat.start()
        try:
            _run_job(job_id, request.get("adapter_name"), request.get("target_urls"),
                     request.get("use_llm", False), request.get("auto_discover", False))
        finally:
            stop.set()
    finally:
        with _queued_lock:
            _queued.discard(job_id)


def _as_aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def recover_jobs(dispatch_pending: bool = True) -> dict:
    """Put abandoned work back in the queue and start anything still waiting.

    Called at startup and every JOB_SWEEP_SECONDS. A running job whose
    heartbeat is older than JOB_STALE_SECONDS lost its worker: it is retried
    until JOB_MAX_ATTEMPTS, then failed with an explanation. Jobs made before
    2.4 have no stored request and cannot be resumed, so they are failed
    honestly instead of spinning forever."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=config.JOB_STALE_SECONDS)
    requeued, failed, started = 0, 0, 0
    with SessionLocal() as db:
        for job in db.query(AuditJob).filter(AuditJob.status == "running").all():
            last = _as_aware(job.heartbeat_at) or _as_aware(job.created_at)
            if last is None or last > stale_before:
                continue
            with _queued_lock:
                if job.id in _queued:
                    continue        # this process is still working on it
            if not job.request_json:
                job.status, job.completed_at = "failed", now
                job.error_message = "The server restarted while this audit was running. Please run it again."
                failed += 1
            elif job.cancel_requested:
                job.status, job.completed_at = "cancelled", now
                job.error_message = "Cancelled. The worker stopped before it finished."
            elif (job.attempts or 0) >= config.JOB_MAX_ATTEMPTS:
                job.status, job.completed_at = "failed", now
                job.error_message = (f"The audit worker stopped {job.attempts} times while running this "
                                     "audit, so it was not retried again. Please run it again later.")
                failed += 1
            else:
                job.status = "pending"
                requeued += 1
        db.commit()

        if dispatch_pending:
            pending = [j.id for j in db.query(AuditJob.id).filter(AuditJob.status == "pending")
                       .order_by(AuditJob.id).all()]
            no_request = [j.id for j in db.query(AuditJob)
                          .filter(AuditJob.status == "pending", AuditJob.request_json.is_(None),
                                  AuditJob.created_at < stale_before).all()]
            for jid in no_request:
                row = db.get(AuditJob, jid)
                row.status, row.completed_at = "failed", now
                row.error_message = "The server restarted before this audit started. Please run it again."
                failed += 1
            db.commit()
        else:
            pending = []
    for jid in pending:
        if dispatch(jid):
            started += 1
    if requeued or failed:
        logger.info("Job recovery: %s re-queued, %s failed", requeued, failed)
    return {"requeued": requeued, "failed": failed, "dispatched": started}


def request_cancel(db, job: AuditJob) -> str:
    """Cancel a pending job at once; ask a running one to stop at the next
    safe point. Returns the resulting status."""
    if job.status == "pending":
        job.status = "cancelled"
        job.cancel_requested = 1
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = "Cancelled before it started."
        db.commit()
        return "cancelled"
    if job.status == "running":
        job.cancel_requested = 1
        db.commit()
        return "cancelling"
    return job.status


def sweep_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(config.JOB_SWEEP_SECONDS):
        try:
            recover_jobs()
        except Exception:
            logger.exception("Job recovery sweep failed")
