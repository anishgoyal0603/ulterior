"""
Background job execution for audit runs.

A full funnel walk takes anywhere from a few seconds (local fixtures) to
a couple of minutes (a real multi-step checkout with network latency), so
this cannot run inside the HTTP request. This module uses a simple
in-process ThreadPoolExecutor -- deliberately, so it's something you can
run and verify with zero extra infrastructure (no Redis, no separate
worker process) while building and testing.

PRODUCTION UPGRADE PATH (documented, not implemented here): swap this for
Celery + Redis so jobs survive an API restart and can scale across
multiple worker machines. The interface (`submit_audit_job`) is written
so that swap only touches this file -- main.py and everything else calls
this same function regardless of what's behind it. See docker-compose.yml
for the Celery+Redis service definitions this would plug into.
"""

import asyncio
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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


_executor = ThreadPoolExecutor(max_workers=4, initializer=_prepare_worker_thread)


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

        for v in violations:
            # PRIVACY: everything derived from captured page content is
            # redacted before it touches the database. Numeric evidence
            # (prices, contrast ratios, pixel dimensions) survives intact —
            # that's the actual proof of a violation and carries no PII.
            db.add(ViolationRecord(
                job_id=job.id, pattern_code=v.pattern_code, pattern_name=v.pattern_name,
                step_name=v.step_name, confidence=v.confidence, layer=v.layer,
                explanation=redact(v.explanation),
                evidence_json=json.dumps(redact_evidence(v.evidence)),
                screenshot_path=trace.screenshots.get(v.step_name.split(" -> ")[0]),
            ))
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
                     use_llm: bool = False, auto_discover: bool = False) -> int:
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
    job = AuditJob(adapter_name=adapter_name or "self-serve", status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id
    db.close()

    _executor.submit(_run_job, job_id, adapter_name, target_urls, use_llm, auto_discover)
    return job_id
