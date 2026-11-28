import json
import threading
import logging
import os
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi import Path as PathParam, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func

from detectors.taxonomy import coverage_summary

from . import models, schemas, config
from .db import get_db, init_db
from .tasks import submit_audit_job, TooManyActiveAudits, request_cancel, recover_jobs, sweep_loop
from .ssrf_guard import UnsafeURLError
from .registry import ADAPTER_REGISTRY
from .middleware import (
    SecurityHeadersMiddleware,
    CorrelationIdMiddleware,
    RateLimitMiddleware,
)
from .auth import require_api_key, require_admin_key, require_caller, Caller, can_see
from .retention import purge_expired, delete_job_evidence
from .privacy import redact

logger = logging.getLogger(__name__)

# Fail fast: refuses to start if production configuration is unsafe
# (SQLite in prod, no TLS on the DB connection, wildcard/plain-HTTP CORS).
config.validate()


def _retention_loop(stop_event: "threading.Event") -> None:
    """Apply the retention policy periodically, not only at boot.

    purge_expired() was called once in the startup hook and nowhere else, so
    RETENTION_DAYS and MAX_STORED_AUDITS bounded NOTHING on a server that
    stays up -- which is the only kind of server the policy is for. A deployed
    instance ran for weeks accumulating rows and screenshot files that nothing
    ever removed.

    A daemon thread rather than a scheduler dependency: this is one call an
    hour, and the project deliberately runs with no extra infrastructure.
    """
    while not stop_event.wait(config.RETENTION_SWEEP_SECONDS):
        try:
            removed = purge_expired()
            if removed:
                logger.info("Retention purge removed %s expired audit(s)", removed)
        except Exception:
            logger.exception("Periodic retention purge failed")


_retention_stop = threading.Event()
_jobs_stop = threading.Event()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup before the yield, shutdown after it.

    This replaces @app.on_event("startup"/"shutdown"), which FastAPI has
    deprecated and will remove. The pair had a real weakness beyond the
    warning: two independent hooks can run in either order relative to other
    handlers, whereas a lifespan makes the shutdown the literal continuation
    of the startup -- the sweep thread is stopped by the same block that
    started it, and it cannot be started without that stop being registered.
    """
    init_db()
    logger.info("Ulterior by The Odyssey starting - %s", config.startup_report())

    # Say it loudly, at the only moment it can still be fixed calmly.
    broken = missing_static_assets()
    if broken:
        logger.error("=" * 70)
        logger.error("THE DEMO PAGES WILL NOT RENDER CORRECTLY. Files are missing:")
        for item in broken:
            logger.error("    %s", item)
        logger.error("")
        logger.error("Re-extract the project zip over this folder, or run:")
        logger.error("    git checkout -- demo dashboard fixtures")
        logger.error("=" * 70)
    try:
        removed = purge_expired()
        if removed:
            logger.info("Retention purge removed %s expired audit(s)", removed)
    except Exception:
        logger.exception("Retention purge failed at startup")

    threading.Thread(target=_retention_loop, args=(_retention_stop,),
                     name="retention-sweep", daemon=True).start()
    # Durable jobs: resume what a restart interrupted, then keep sweeping.
    if config.RUN_AUDIT_WORKERS:
        try:
            recover_jobs()
        except Exception:
            logger.exception("Job recovery failed at startup")
        _jobs_stop.clear()
        threading.Thread(target=sweep_loop, args=(_jobs_stop,), daemon=True,
                         name="job-recovery").start()
    try:
        yield
    finally:
        _retention_stop.set()
        _jobs_stop.set()
        # The audit thread pool is deliberately NOT shut down here: Python
        # waits for its threads at exit, so a running audit finishes (and
        # closes its browser) instead of leaving Chromium orphaned; a process
        # killed outright is covered by the heartbeat recovery on next start.


app = FastAPI(
    lifespan=lifespan,
    title="Ulterior by The Odyssey",
    description="Automated CCPA (India) Dark Patterns Guidelines, 2023 compliance auditor "
                "for e-commerce, travel, and other consumer-facing web funnels — SIH26199",
    version="2.4.0",
    debug=config.DEBUG,
    # Interactive API docs expose the full schema. Harmless here (no auth,
    # no user data in the schema) but disabled in production by default,
    # since they're a free reconnaissance aid.
    docs_url=None if config.IS_PRODUCTION else "/docs",
    redoc_url=None if config.IS_PRODUCTION else "/redoc",
    openapi_url=None if config.IS_PRODUCTION else "/openapi.json",
)

# Middleware order matters: correlation ID is added LAST so it runs FIRST,
# ensuring request.state.correlation_id exists for the rate limiter and the
# exception handlers below.
# Order matters: the LAST middleware added runs FIRST. The rate limiter is
# added before the security headers, so the headers wrap it -- a 429 carries
# the same CSP, nosniff, frame and HSTS headers as every other response.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    # The API is authenticated by header, so a cross-origin client must be
    # allowed to send it, or every preflight fails.
    allow_headers=["Content-Type", "X-Correlation-ID", "X-API-Key", "Authorization", "Idempotency-Key"],
)
app.add_middleware(CorrelationIdMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Catch-all. The client gets a generic message plus a correlation id; the
    full traceback goes to the server log only. Without this, FastAPI's
    default behaviour with debug=True would render a full stack trace --
    including file paths and local variables -- straight into the response.
    """
    cid = getattr(request.state, "correlation_id", "unknown")
    logger.exception("Unhandled error [correlation_id=%s] %s %s",
                     cid, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "correlation_id": cid,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Deliberate 4xx responses pass their message through (they contain only
    messages we author), but always carry a correlation id."""
    cid = getattr(request.state, "correlation_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "correlation_id": cid},
        headers=getattr(exc, "headers", None),
    )


def _to_violation_out(v: models.ViolationRecord) -> schemas.ViolationOut:
    return schemas.ViolationOut(
        id=v.id, pattern_code=v.pattern_code, pattern_name=v.pattern_name,
        step_name=v.step_name, confidence=v.confidence, layer=v.layer,
        explanation=v.explanation, evidence=json.loads(v.evidence_json or "{}"),
        has_screenshot=bool(v.screenshot_path),
    )


def _to_job_out(job: models.AuditJob) -> schemas.AuditJobOut:
    return schemas.AuditJobOut(
        id=job.id, site_name=job.site_name, adapter_name=job.adapter_name,
        status=job.status, created_at=job.created_at, completed_at=job.completed_at,
        error_message=job.error_message,
        discovery=json.loads(job.discovery_json) if job.discovery_json else None,
        authorization=json.loads(job.authorization_json) if job.authorization_json else None,
        evidence_manifest=json.loads(job.evidence_manifest_json) if job.evidence_manifest_json else None,
        coverage=json.loads(job.coverage_json) if job.coverage_json else None,
        review=json.loads(job.review_json) if job.review_json else None,
        attempts=job.attempts or 0,
        violations=[_to_violation_out(v) for v in job.violations],
    )


def _visible(query, caller: Caller):
    """Only the caller's organisation's audits (admins see all). Audits made
    before 2.4 have no organisation and belong to the default team."""
    if caller.admin:
        return query
    if caller.org == config.DEFAULT_ORG:
        return query.filter((models.AuditJob.org == caller.org) | models.AuditJob.org.is_(None)
                            | (models.AuditJob.org == config.DEMO_ORG))
    return query.filter(models.AuditJob.org == caller.org)


def _own_job(db: Session, job_id: int, caller: Caller) -> models.AuditJob:
    """The job, if it exists AND this caller may see it. Another
    organisation's audit answers exactly like a missing one (404), so ids
    cannot be probed."""
    job = db.get(models.AuditJob, job_id)
    if not job or not can_see(caller, job.org):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _stored_findings(job: models.AuditJob) -> list:
    """The findings as stored, in the shape app/integrity.py hashes."""
    return [{
        "pattern_code": v.pattern_code, "pattern_name": v.pattern_name, "step_name": v.step_name,
        "layer": v.layer, "confidence": v.confidence, "explanation": v.explanation,
        "evidence": json.loads(v.evidence_json or "{}"),
    } for v in job.violations]


def _finished_job(db: Session, job_id: int, caller: Caller) -> models.AuditJob:
    job = _own_job(db, job_id, caller)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="This audit has not finished yet.")
    return job


def _authorization_record(payload: schemas.AuditJobCreate, key: str) -> dict:
    """The permission record stored with the audit: what the person entered,
    when the server received it, and which API key sent it (as a short hash,
    never the key itself), so a report can say who vouched for the audit."""
    from datetime import datetime, timezone
    import hashlib
    a = payload.authorization
    return {
        "owner_name": a.owner_name,
        "organisation": a.organisation,
        "authority": a.authority,
        "reference": redact(a.reference),
        "confirmed": True,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recorded_by_key": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
    }


@app.get("/adapters")
def list_adapters(_key: str = Depends(require_api_key)):
    """Which sites can currently be audited. Add a new one in app/registry.py."""
    return {name: adapter.site_name for name, adapter in ADAPTER_REGISTRY.items()}


@app.post("/audits", response_model=schemas.AuditJobOut)
def create_audit(payload: schemas.AuditJobCreate, db: Session = Depends(get_db),
                 caller: Caller = Depends(require_caller),
                 idempotency_key: str = Header(default=None, alias="Idempotency-Key", max_length=80)):
    if payload.adapter_name and payload.adapter_name not in ADAPTER_REGISTRY:
        # Echo back only the caller's own input and the known-safe list of
        # adapter names -- never str(e) from an arbitrary exception, which
        # can carry internal paths or connection details.
        raise HTTPException(
            status_code=400,
            detail=f"Unknown adapter. Available: {sorted(ADAPTER_REGISTRY)}",
        )
    if payload.auto_discover and payload.target_urls and len(payload.target_urls) > 1:
        # Discovery starts from ONE page; silently dropping the rest would
        # audit less than the caller asked for without saying so.
        raise HTTPException(status_code=400, detail="auto_discover starts from one URL. "
                            "Send a single URL, or turn auto_discover off to audit each URL as a step.")
    if payload.target_urls and payload.authorization is None:
        # Ulterior audits a pasted site only with its owner's permission (or a
        # public test sandbox that invites automated testing). The permission
        # is recorded with the audit and printed on its report.
        raise HTTPException(
            status_code=400,
            detail="Auditing a pasted URL needs a permission record: who allowed it "
                   "(owner_name, organisation, authority) and confirmed: true.",
        )
    try:
        job_id = submit_audit_job(payload.adapter_name, payload.target_urls,
                                  use_llm=payload.use_llm,
                                  auto_discover=payload.auto_discover,
                                  authorization=_authorization_record(payload, caller.key)
                                  if payload.authorization else None,
                                  org=caller.org,
                                  idempotency_key=(idempotency_key or "").strip() or None)
    except UnsafeURLError as e:
        # Safe to echo -- ssrf_guard's messages never contain internal
        # paths or secrets, only the rejected URL/hostname and the reason.
        raise HTTPException(status_code=400, detail=str(e))
    except TooManyActiveAudits as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job = db.get(models.AuditJob, job_id)
    return _to_job_out(job)


@app.get("/audits/{job_id}", response_model=schemas.AuditJobOut)
def get_audit(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"), db: Session = Depends(get_db),
              caller: Caller = Depends(require_caller)):
    return _to_job_out(_own_job(db, job_id, caller))


@app.post("/audits/{job_id}/cancel")
def cancel_audit(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"),
                 db: Session = Depends(get_db), caller: Caller = Depends(require_caller)):
    """Stop an audit. A queued one is cancelled at once; a running one stops
    before its findings are written (screenshots taken so far are kept until
    the retention period ends)."""
    job = _own_job(db, job_id, caller)
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail=f"This audit is already {job.status}.")
    return {"id": job.id, "status": request_cancel(db, job)}


@app.post("/audits/{job_id}/review", response_model=schemas.AuditJobOut)
def review_audit(payload: schemas.ReviewIn,
                 job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"),
                 db: Session = Depends(get_db), caller: Caller = Depends(require_caller)):
    """Record that a person checked every finding against its screenshot
    before the report goes to anyone. Printed on the report."""
    from datetime import datetime, timezone
    job = _finished_job(db, job_id, caller)
    job.review_json = json.dumps({
        "reviewer": payload.reviewer,
        "outcome": payload.outcome,
        "note": redact(payload.note),
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    db.commit()
    return _to_job_out(job)


@app.get("/audits/{job_id}/verify-evidence")
def verify_evidence(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"),
                    db: Session = Depends(get_db), caller: Caller = Depends(require_caller)):
    """Recompute the SHA-256 of every screenshot and of the stored findings,
    and compare them with the fingerprints taken when the audit finished.

    intact: nothing changed. changed: something no longer matches.
    incomplete: nothing changed, but some screenshots were deleted (the
    retention purge removes old evidence)."""
    from capture.funnel_walker import DEFAULT_EVIDENCE_DIR
    from .integrity import verify_manifest
    job = _finished_job(db, job_id, caller)
    if not job.evidence_manifest_json:
        return {"status": "unavailable", "detail": "This audit was run before evidence fingerprints existed."}
    manifest = json.loads(job.evidence_manifest_json)
    return verify_manifest(manifest, os.path.join(DEFAULT_EVIDENCE_DIR, f"job_{job.id}"),
                           _stored_findings(job))


@app.get("/audits/{job_id}/compare")
def compare_audits(job_id: int = PathParam(..., ge=1, lt=2**63, description="The re-check audit"),
                   baseline: int = Query(..., ge=1, lt=2**63, description="The earlier audit"),
                   db: Session = Depends(get_db), caller: Caller = Depends(require_caller)):
    """A re-check after fixes: which observations from the earlier audit are
    gone (resolved), which remain, and which are new."""
    from .integrity import compare_findings
    if baseline == job_id:
        raise HTTPException(status_code=400, detail="Pick a different, earlier audit to compare with.")
    current = _finished_job(db, job_id, caller)
    earlier = _finished_job(db, baseline, caller)
    result = compare_findings(_stored_findings(earlier), _stored_findings(current))
    return {
        "current_id": current.id, "baseline_id": earlier.id,
        "same_site": (current.site_name or "") == (earlier.site_name or ""),
        **result,
    }


@app.get("/audits/{job_id}/report.pdf", include_in_schema=False)
def audit_report_pdf(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"),
                     baseline: int = Query(None, ge=1, lt=2**63,
                                           description="Optional earlier audit to compare with"),
                     db: Session = Depends(get_db), caller: Caller = Depends(require_caller)):
    """The audit as a PDF: what a pilot store's compliance officer or a
    regulator actually reads. Same evidence, tiers and limitations as the
    dashboard, with the legal framing printed on the first page.

    Behind the API key like every other audit read. Generated on request, not
    stored: the database already holds the findings, and a second copy on
    disk would outlive the retention purge."""
    import tempfile
    from report.generator import generate_audit_report

    job = _own_job(db, job_id, caller)
    if job.status != "done":
        raise HTTPException(status_code=409, detail="This audit has not finished, so there is no report yet.")
    violations = [_to_violation_out(v).model_dump() for v in job.violations]
    comparison = None
    if baseline is not None:
        from .integrity import compare_findings
        if baseline == job.id:
            raise HTTPException(status_code=400, detail="Pick a different, earlier audit to compare with.")
        earlier = _finished_job(db, baseline, caller)
        comparison = {"baseline_id": earlier.id,
                      **compare_findings(_stored_findings(earlier), _stored_findings(job))}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "report.pdf")
        generate_audit_report(
            job.site_name or job.adapter_name, violations, path,
            job_id=job.id,
            authorization=json.loads(job.authorization_json) if job.authorization_json else None,
            manifest=json.loads(job.evidence_manifest_json) if job.evidence_manifest_json else None,
            comparison=comparison,
            coverage=json.loads(job.coverage_json) if job.coverage_json else None,
            review=json.loads(job.review_json) if job.review_json else None,
        )
        with open(path, "rb") as fh:
            data = fh.read()
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="ulterior-audit-{job.id}.pdf"',
        "Cache-Control": "no-store",
    })


@app.get("/audits", response_model=List[schemas.AuditJobOut])
def list_audits(
    limit: int = Query(50, ge=1, le=200,
                       description="How many audits to return, newest first."),
    offset: int = Query(0, ge=0, description="How many to skip."),
    db: Session = Depends(get_db),
    caller: Caller = Depends(require_caller),
):
    """Newest first, paginated, only this organisation's audits.

    This used to be `.all()`, serialising EVERY audit ever run together with
    every violation and every evidence blob into one response. Four hundred
    audits already made it megabytes; a server left running for a term would
    eventually time out on its own audit list, and the dashboard calls this on
    every page load.
    """
    jobs = (_visible(db.query(models.AuditJob), caller)
            .order_by(models.AuditJob.created_at.desc())
            .offset(offset).limit(limit).all())
    return [_to_job_out(j) for j in jobs]


@app.get("/dashboard/summary")
def summary(db: Session = Depends(get_db),
            caller: Caller = Depends(require_caller)):
    total_jobs = _visible(db.query(func.count(models.AuditJob.id)), caller).scalar() or 0
    vq = db.query(func.count(models.ViolationRecord.id)).join(models.AuditJob)
    total_violations = _visible(vq, caller).scalar() or 0
    by_pattern = (
        _visible(db.query(models.ViolationRecord.pattern_code, models.ViolationRecord.pattern_name,
                          func.count(models.ViolationRecord.id)).join(models.AuditJob), caller)
        .group_by(models.ViolationRecord.pattern_code, models.ViolationRecord.pattern_name)
        .all()
    )
    return {
        "total_audits": total_jobs,
        "total_violations": total_violations,
        "by_pattern": [{"code": c, "name": n, "count": cnt} for c, n, cnt in by_pattern],
        # Whether ticking "LLM language pass" would do anything. Read the same
        # way detectors/layer3_language.py reads it, at call time, so the
        # dashboard and the detector can never disagree. Behind the API key:
        # it says nothing about the key itself, only whether one is set.
        "llm_available": bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip()),
    }


@app.delete("/audits/{job_id}", status_code=204)
def delete_audit(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"), db: Session = Depends(get_db),
                 _key: str = Depends(require_admin_key)):
    """
    Hard-delete an audit and everything derived from it: the job row, all
    violation records, and the captured screenshot files on disk.

    This is the data-deletion control for this app. Because there are no
    user accounts, there is no "delete my account" flow to build — the
    personal data at risk here is whatever the crawler captured from an
    audited page, and this endpoint removes it at its actual granularity:
    the audit run.
    """
    job = db.get(models.AuditJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    _delete_evidence_files(job)
    # ORM cascade (see models.AuditJob.violations) removes the child
    # violation rows as part of this delete -- do not bulk-delete them
    # separately first, or the session's loaded children go stale.
    db.delete(job)
    db.commit()
    return None


@app.delete("/audits", status_code=200)
def purge_all_audits(db: Session = Depends(get_db),
                     _key: str = Depends(require_admin_key)):
    """Purge every audit, violation, and captured screenshot. Full reset."""
    jobs = db.query(models.AuditJob).all()
    jobs_deleted = 0
    violations_deleted = 0
    for job in jobs:
        _delete_evidence_files(job)
        violations_deleted += len(job.violations)
        db.delete(job)   # cascade removes children
        jobs_deleted += 1
    db.commit()
    return {"jobs_deleted": jobs_deleted, "violations_deleted": violations_deleted}


def _delete_evidence_files(job: models.AuditJob) -> None:
    """Remove every screenshot belonging to a job (its whole evidence folder).
    Screenshots are full-page captures of audited sites and are the
    highest-risk artifact this app produces, so deletion must reach disk."""
    delete_job_evidence(job)


@app.post("/extension-reports", status_code=201)
def create_extension_report(payload: schemas.ExtensionReportIn, db: Session = Depends(get_db)):
    """A page a browser-extension user chose to report.

    No API key: the person reporting has none. What keeps it safe is the
    shape (schemas.ExtensionReportIn -- a site name and at most twenty short
    findings, nothing else accepted), a per-IP rate limit, redaction before
    storage, and the retention purge. Nothing identifying the sender is
    stored. Reports are only ever displayed behind the API key, escaped.
    """
    from .privacy import redact
    findings = [{
        "code": f.code, "tier": f.tier, "title": redact(f.title), "detail": redact(f.detail),
        "evidence": {redact(k): redact(v) for k, v in f.evidence.items()},
    } for f in payload.findings]
    row = models.ExtensionReport(site=payload.site, version=payload.version,
                                 page_type=payload.page_type, findings_json=json.dumps(findings))
    db.add(row)
    db.commit()
    return {"id": row.id, "status": "received"}


@app.get("/extension-reports", response_model=List[schemas.ExtensionReportOut])
def list_extension_reports(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                           caller: Caller = Depends(require_caller)):
    """Reports from extension users, newest first -- the queue a person
    reviews before any of it is believed or repeated. The Ulterior team's
    queue, so a customer organisation's key cannot read it."""
    if not caller.admin and caller.org != config.DEFAULT_ORG:
        raise HTTPException(status_code=403, detail="Only the Ulterior team can read extension reports.")
    rows = (db.query(models.ExtensionReport)
            .order_by(models.ExtensionReport.created_at.desc()).limit(limit).all())
    return [schemas.ExtensionReportOut(id=r.id, created_at=r.created_at, site=r.site, version=r.version,
                                       page_type=r.page_type, findings=json.loads(r.findings_json or "[]"))
            for r in rows]


# ------------------------------------------------------ community prices
# Prices that extension users chose to share (off by default in the
# extension). No key: the people sending have none. Kept safe by the shapes in
# schemas.Price*, the site list in app/community.py, rate limits, one report
# per sender per product per day, and daily MEDIANS on the way out.

@app.post("/prices", status_code=201)
def add_prices(payload: schemas.PricesIn, request: Request, db: Session = Depends(get_db)):
    from . import community
    from .middleware import _client_ip
    day = community.today()
    sender = _client_ip(request)
    stored = 0
    # Validate EVERYTHING first. Marking a product "seen today" and then
    # rejecting the request at a later item left the earlier products
    # rolled back in the database but blocked for the sender until tomorrow.
    sites = [community.site_of_key(o.key) for o in payload.observations]
    if not all(sites):
        raise HTTPException(status_code=422, detail="Unsupported product id.")
    for o, site in zip(payload.observations, sites):
        if not community.seen_today.first_time(sender, o.key, day):
            continue
        db.add(models.PriceObservation(product_key=o.key, site=site, price=float(o.price), observed_on=day))
        stored += 1
    db.commit()
    return {"stored": stored}


@app.post("/prices/history")
def price_history(payload: schemas.PriceHistoryIn, db: Session = Depends(get_db)):
    from . import community
    if not community.site_of_key(payload.key):
        raise HTTPException(status_code=422, detail="Unsupported product id.")
    rows = (db.query(models.PriceObservation.observed_on, models.PriceObservation.price)
            .filter(models.PriceObservation.product_key == payload.key,
                    models.PriceObservation.observed_on >= community.window_start(payload.days))
            # Newest first, so a very busy product loses its OLDEST days at
            # the cap rather than its most recent ones.
            .order_by(models.PriceObservation.observed_on.desc()).limit(20000).all())
    rows = list(reversed(rows))
    return {"key": payload.key, "daily": community.daily_medians(rows)}


@app.post("/prices/latest")
def price_latest(payload: schemas.PriceLatestIn, db: Session = Depends(get_db)):
    """Today's (or the most recent of the last three days') median price for
    each product -- what the extension's six-hourly watchlist check asks."""
    from . import community
    keys = [k for k in dict.fromkeys(payload.keys) if community.site_of_key(k)]
    out = {}
    if keys:
        rows = (db.query(models.PriceObservation.product_key, models.PriceObservation.observed_on,
                         models.PriceObservation.price)
                .filter(models.PriceObservation.product_key.in_(keys),
                        models.PriceObservation.observed_on >= community.window_start(3))
                .order_by(models.PriceObservation.observed_on.desc())
                .limit(50000).all())
        per = {}
        for k, d, p in rows:
            per.setdefault(k, []).append((d, p))
        for k, rs in per.items():
            last = community.daily_medians(rs)[-1]
            out[k] = last
    return {"prices": out}


@app.post("/prices/drops")
def price_drops(payload: schemas.PriceDropsIn, db: Session = Depends(get_db)):
    """Products whose community price fell the most over the last few days:
    first day's median against the latest day's, at least 5% down and at
    least two reports on each of those days."""
    from . import community
    import time as _time
    # The answer is the same for every caller, so it is computed at most once
    # a minute per window. Without this, an unauthenticated caller could make
    # the server load and sort up to 100,000 rows on every request.
    with _drops_lock:
        hit = _drops_cache.get(payload.days)
        if hit and _time.monotonic() - hit[0] < 60:
            return {"drops": hit[1][:payload.limit]}
    from sqlalchemy import func
    PO = models.PriceObservation
    since = community.window_start(payload.days)
    # Step 1, in SQL: how many reports each product has on each day. This is
    # one small row per (product, day) however many reports there are, so no
    # row cap is needed and no product loses its older days -- which is what
    # the old "newest 100,000 rows" query did on a busy window, silently
    # moving each product's starting day forward and hiding real drops.
    counts = (db.query(PO.product_key, PO.observed_on, func.count(PO.id))
              .filter(PO.observed_on >= since)
              .group_by(PO.product_key, PO.observed_on).all())
    days_of: dict = {}
    for k, d, n in counts:
        days_of.setdefault(k, {})[d] = n
    # A product qualifies only with reports on at least two days and at
    # least two reports on its first and on its latest day.
    ends = {}
    for k, per_day in days_of.items():
        first, last = min(per_day), max(per_day)
        if first != last and per_day[first] >= 2 and per_day[last] >= 2:
            ends[k] = (first, last, sum(per_day.values()))
    # Step 2: prices for just those two days of just those products.
    wanted: dict = {}
    for k, (first, last, _) in ends.items():
        wanted.setdefault(first, []).append(k)
        wanted.setdefault(last, []).append(k)
    prices: dict = {}
    for day, keys in wanted.items():
        for i in range(0, len(keys), 500):
            for k, p in (db.query(PO.product_key, PO.price)
                         .filter(PO.observed_on == day, PO.product_key.in_(keys[i:i + 500])).all()):
                prices.setdefault((k, day), []).append(p)
    drops = []
    for k, (first, last, total) in ends.items():
        a_rows, b_rows = prices.get((k, first)), prices.get((k, last))
        if not a_rows or not b_rows:
            continue
        a = community.daily_medians([(first, p) for p in a_rows])[0]["price"]
        b = community.daily_medians([(last, p) for p in b_rows])[0]["price"]
        if a > 0 and b < a * 0.95:
            drops.append({"key": k, "from": a, "to": b, "pct": round((a - b) / a * 100, 1),
                          "reports": total, "since": first.isoformat()})
    drops.sort(key=lambda x: -x["pct"])
    with _drops_lock:
        _drops_cache[payload.days] = (_time.monotonic(), drops[:50])
    return {"drops": drops[:payload.limit]}


_drops_cache: dict = {}
_drops_lock = threading.Lock()


@app.get("/coverage")
def coverage():
    """The CCPA-13 coverage table, generated from the taxonomy itself.

    Deliberately UNAUTHENTICATED and served from the same source of truth the
    detectors use. Coverage claims are the thing most likely to drift between
    a slide, a README and the code — and the only one of the three that gets
    checked by an audience. Serving it from `taxonomy.py` means the pitch, the
    dashboard and the engine cannot disagree, because there is only one copy.
    """
    return coverage_summary()


@app.get("/healthz")
def healthz():
    """Liveness probe for the deployment platform.

    Separate from `/` on purpose: `/` is a human-facing status string that may
    grow content over time, while this stays a minimal, dependency-free 200 so
    a platform healthcheck never fails for a reason unrelated to the process
    being alive.
    """
    return {"status": "ok"}


@app.post("/demo-audit", response_model=schemas.AuditJobOut)
def create_demo_audit(payload: schemas.DemoAuditCreate, db: Session = Depends(get_db)):
    """Unauthenticated, deliberately narrow: runs one of two server-defined
    demo storefronts and nothing else.

    This exists so the demo link works for someone who has no API key -- a
    judge, a professor, a prospective pilot user. Everything a caller could
    otherwise influence is removed: the adapter must be one of two names
    hard-coded in config, and no caller-supplied URL reaches the crawler
    through this route at all. `/audits` remains authenticated and is the only
    way to audit anything else.
    """
    if not config.PUBLIC_DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    if payload.adapter_name not in config.PUBLIC_DEMO_ADAPTERS:
        raise HTTPException(
            status_code=400,
            detail=f"The public demo runs only: {list(config.PUBLIC_DEMO_ADAPTERS)}",
        )
    job_id = submit_audit_job(payload.adapter_name, None, enforce_limit=False, org=config.DEMO_ORG)
    job = db.get(models.AuditJob, job_id)
    return _to_job_out(job)


@app.get("/demo-audit/{job_id}", response_model=schemas.AuditJobOut)
def get_demo_audit(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"), db: Session = Depends(get_db)):
    """Read back a demo job. Scoped to demo adapters so this cannot be used as
    an unauthenticated window onto real audits someone else ran."""
    if not config.PUBLIC_DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    job = db.get(models.AuditJob, job_id)
    if not job or job.adapter_name not in config.PUBLIC_DEMO_ADAPTERS:
        raise HTTPException(status_code=404, detail="Demo audit not found")
    return _to_job_out(job)


_LANDING_INDEX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "landing", "index.html")


def _serve_page(path: str, request: Request) -> Response:
    """A same-origin HTML page with revalidation and a 304 for an unchanged
    copy -- what StaticFiles does for mounted pages, for a page with a route
    of its own."""
    page = FileResponse(path, media_type="text/html", stat_result=os.stat(path),
                        headers={"Cache-Control": _cache_policy()})
    etag = page.headers.get("etag")
    if etag and etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _cache_policy()})
    return page


_PRIVACY_PAGE = os.path.join(os.path.dirname(_LANDING_INDEX), "privacy.html")


@app.get("/privacy", include_in_schema=False)
def privacy(request: Request):
    """The privacy policy. The Chrome Web Store requires one at a public URL
    for any extension that reads page content, and the extension's popup
    links here."""
    if not os.path.isfile(_PRIVACY_PAGE):
        raise HTTPException(status_code=404, detail="Not found")
    return _serve_page(_PRIVACY_PAGE, request)


_EXTENSION_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension")
_extension_zip_cache: dict = {}
_extension_zip_lock = threading.Lock()


@app.get("/download/ulterior-extension.zip", include_in_schema=False)
def download_extension():
    """The browser extension as a zip, for installing before (or instead of)
    the Chrome Web Store: unzip, chrome://extensions, Developer mode, Load
    unpacked. Built from the same folder the server ships, so the download
    can never be older than the site describing it."""
    import io
    import zipfile
    if not os.path.isfile(os.path.join(_EXTENSION_DIR, "manifest.json")):
        raise HTTPException(status_code=404, detail="Not found")
    files = []
    for base, _dirs, names in os.walk(_EXTENSION_DIR):
        for n in sorted(names):
            if n.endswith((".md", ".pyc")) or n.startswith("."):
                continue
            full = os.path.join(base, n)
            files.append((os.path.relpath(full, _EXTENSION_DIR).replace(os.sep, "/"), full))
    files.sort()
    stamp = tuple((rel, os.path.getmtime(full), os.path.getsize(full)) for rel, full in files)
    # Requests run on several threads; the stamp and the bytes must change together.
    with _extension_zip_lock:
        if _extension_zip_cache.get("stamp") != stamp:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for rel, full in files:
                    z.write(full, "ulterior-extension/" + rel)
            _extension_zip_cache.update(stamp=stamp, data=buf.getvalue())
        data = _extension_zip_cache["data"]
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="ulterior-extension.zip"',
                             "Cache-Control": _cache_policy()})


@app.get("/", include_in_schema=False)
def root(request: Request):
    """The front door.

    This used to return {"status": "ok", ...} -- which meant the first thing a
    judge, a pilot user or a professor saw at the deployed address was a line
    of raw JSON. The public URL is the product's first impression, so it now
    serves the landing page.

    Nothing that needs a machine-readable liveness answer loses one: /healthz
    has always been the probe (see its docstring), and it is unchanged. If the
    landing folder is ever missing from a deployment, this falls back to the
    old JSON rather than a 500, so the root still answers.
    """
    if os.path.isfile(_LANDING_INDEX):
        # no-cache asks the browser to check back on every visit; answering
        # an unchanged page with a body-less 304 is what keeps that cheap.
        # StaticFiles does this for every other page; a plain FileResponse
        # does not, so _serve_page does it with the ETag Starlette computed.
        return _serve_page(_LANDING_INDEX, request)
    return {"status": "ok", "service": "Ulterior by The Odyssey"}


# The dashboard is served from the API's OWN origin rather than a separate
# static host. That is not a convenience: production config REQUIRES a
# non-wildcard HTTPS CORS origin, and a dashboard deployed somewhere else
# needs its own domain, its own TLS certificate and its own entry in
# CORS_ALLOW_ORIGINS before it can make a single request. Same-origin removes
# that entire class of deployment failure -- the dashboard is same-origin with
# the API by construction, so CORS never enters into it.
#
# Mounted LAST so it can never shadow an API route: FastAPI matches routes in
# definition order, and a mount at "/dashboard" registered earlier would
# swallow anything beneath that prefix.
def _cache_policy() -> str:
    """How long a browser may reuse a page or asset without asking again.

    Development: never (no-store) -- a developer reloads constantly and must
    always see the file on disk.

    Production: it may keep a copy but must CHECK before using it (no-cache).
    This used to be "normal caching", i.e. no header at all, and that broke
    the live demo the first time the stylesheets changed in production:
    without a Cache-Control header a browser invents its own freshness window
    from the file's age, so a visitor who had opened the demo before the
    redesign reused the OLD demo.css for days without asking -- new HTML, old
    stylesheet, no layout, icons drawn as giant black shapes. The server was
    serving the right file the whole time; the browser never requested it.

    no-cache costs one conditional request per asset, answered with a
    body-less 304 when nothing changed, so it stays cheap for returning
    visitors while making a deploy visible to everyone on their next load.
    """
    return "no-cache" if config.IS_PRODUCTION else "no-store, max-age=0, must-revalidate"


class _NoStoreStaticFiles(StaticFiles):
    """Static files that are always revalidated -- see _cache_policy()."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = _cache_policy()
        return response


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DASHBOARD_DIR = os.path.join(_ROOT, "dashboard")
if os.path.isdir(_DASHBOARD_DIR):
    app.mount("/dashboard", _NoStoreStaticFiles(directory=_DASHBOARD_DIR, html=True),
              name="dashboard")

# The public demo: a storefront the auditor can actually walk over HTTP, next
# to the findings it produces. Serving the fixtures here rather than opening
# them as file:// is what makes the demo mean something — the crawler takes
# the identical code path it would against a live commercial site, including
# DNS, redirects, caching and every other difference between fetching a page
# and reading a file off disk.
# The landing page's own stylesheet and script. Its HTML is served at "/" by
# root() above; this mount only carries the assets, under a prefix that cannot
# collide with any API route.
_LANDING_DIR = os.path.join(_ROOT, "landing")
if os.path.isdir(_LANDING_DIR):
    app.mount("/landing", _NoStoreStaticFiles(directory=_LANDING_DIR), name="landing")

_DEMO_DIR = os.path.join(_ROOT, "demo")
_STOREFRONT_DIR = os.path.join(_ROOT, "fixtures", "ecommerce_dark")
_STOREFRONT_CLEAN_DIR = os.path.join(_ROOT, "fixtures", "ecommerce_clean")
if os.path.isdir(_DEMO_DIR):
    app.mount("/demo", _NoStoreStaticFiles(directory=_DEMO_DIR, html=True), name="demo")
if os.path.isdir(_STOREFRONT_DIR):
    app.mount("/storefront", _NoStoreStaticFiles(directory=_STOREFRONT_DIR, html=True),
              name="storefront")
if os.path.isdir(_STOREFRONT_CLEAN_DIR):
    app.mount("/storefront-clean", _NoStoreStaticFiles(directory=_STOREFRONT_CLEAN_DIR, html=True),
              name="storefront_clean")


# Every file a demo surface needs in order to look like itself.
#
# The mounts above are all guarded by `os.path.isdir`, which silently skips a
# missing folder -- and a folder that EXISTS but lost a file to a partial
# extraction is worse still: the HTML serves, the stylesheet 404s, and the
# page renders as unstyled Times New Roman with a broken frame where the
# storefront should be. Nothing in the terminal says a word.
#
# That is a bad way to discover a problem thirty seconds before a demo, so
# the server checks its own assets at startup and names the missing file.
_REQUIRED_ASSETS = {
    "landing": (_LANDING_DIR, ("index.html", "privacy.html", "landing.css", "landing.js")),
    "extension": (_EXTENSION_DIR, ("manifest.json", "background.js", "popup.html", "popup.js",
                                   "icons/icon128.png", "src/core.js", "src/content.js")),
    "demo": (_DEMO_DIR, ("index.html", "demo.css", "demo.js")),
    "dashboard": (_DASHBOARD_DIR, ("index.html", "dashboard.css", "dashboard.js")),
    "storefront": (_STOREFRONT_DIR, ("listing.html", "cart.html", "checkout.html", "shop.css")),
    "storefront-clean": (_STOREFRONT_CLEAN_DIR, ("listing.html", "cart.html", "checkout.html")),
}


def missing_static_assets() -> List[str]:
    """Files a demo surface needs and does not have. Empty list means healthy.

    A zero-byte file counts as missing: an interrupted copy leaves one behind,
    and it fails exactly like an absent file while looking present in a
    directory listing.
    """
    missing = []
    for surface, (directory, names) in _REQUIRED_ASSETS.items():
        if not os.path.isdir(directory):
            missing.append(f"{surface}: the whole folder is absent ({directory})")
            continue
        for name in names:
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                missing.append(f"{surface}: {name} is missing")
            elif os.path.getsize(path) == 0:
                missing.append(f"{surface}: {name} is empty (0 bytes)")
    return missing
