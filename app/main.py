import json
import threading
import logging
import os
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi import Path as PathParam, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func

from detectors.taxonomy import coverage_summary

from . import models, schemas, config
from .db import get_db, init_db
from .tasks import submit_audit_job
from .ssrf_guard import UnsafeURLError
from .registry import ADAPTER_REGISTRY
from .middleware import (
    SecurityHeadersMiddleware,
    CorrelationIdMiddleware,
    RateLimitMiddleware,
)
from .auth import require_api_key, require_admin_key
from .retention import purge_expired

logger = logging.getLogger(__name__)

# Fail fast: refuses to start if production configuration is unsafe
# (SQLite in prod, no TLS on the DB connection, wildcard/plain-HTTP CORS).
config.validate()

app = FastAPI(
    title="Dark Pattern Auditor",
    description="Automated CCPA (India) Dark Patterns Guidelines, 2023 compliance auditor "
                "for e-commerce, travel, and other consumer-facing web funnels — SIH26199",
    version="0.1.0",
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
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Correlation-ID"],
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


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Dark Pattern Auditor starting — %s", config.startup_report())
    try:
        removed = purge_expired()
        if removed:
            logger.info("Retention purge removed %s expired audit(s)", removed)
    except Exception:
        logger.exception("Retention purge failed at startup")

    threading.Thread(target=_retention_loop, args=(_retention_stop,),
                     name="retention-sweep", daemon=True).start()


@app.on_event("shutdown")
def on_shutdown():
    _retention_stop.set()


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
        violations=[_to_violation_out(v) for v in job.violations],
    )


@app.get("/adapters")
def list_adapters(_key: str = Depends(require_api_key)):
    """Which sites can currently be audited. Add a new one in app/registry.py."""
    return {name: adapter.site_name for name, adapter in ADAPTER_REGISTRY.items()}


@app.post("/audits", response_model=schemas.AuditJobOut)
def create_audit(payload: schemas.AuditJobCreate, db: Session = Depends(get_db),
                 _key: str = Depends(require_api_key)):
    if payload.adapter_name and payload.adapter_name not in ADAPTER_REGISTRY:
        # Echo back only the caller's own input and the known-safe list of
        # adapter names -- never str(e) from an arbitrary exception, which
        # can carry internal paths or connection details.
        raise HTTPException(
            status_code=400,
            detail=f"Unknown adapter. Available: {sorted(ADAPTER_REGISTRY)}",
        )
    try:
        job_id = submit_audit_job(payload.adapter_name, payload.target_urls,
                                  use_llm=payload.use_llm,
                                  auto_discover=payload.auto_discover)
    except UnsafeURLError as e:
        # Safe to echo -- ssrf_guard's messages never contain internal
        # paths or secrets, only the rejected URL/hostname and the reason.
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job = db.query(models.AuditJob).get(job_id)
    return _to_job_out(job)


@app.get("/audits/{job_id}", response_model=schemas.AuditJobOut)
def get_audit(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"), db: Session = Depends(get_db),
              _key: str = Depends(require_api_key)):
    job = db.query(models.AuditJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _to_job_out(job)


@app.get("/audits", response_model=List[schemas.AuditJobOut])
def list_audits(
    limit: int = Query(50, ge=1, le=200,
                       description="How many audits to return, newest first."),
    offset: int = Query(0, ge=0, description="How many to skip."),
    db: Session = Depends(get_db),
    _key: str = Depends(require_api_key),
):
    """Newest first, paginated.

    This used to be `.all()`, serialising EVERY audit ever run together with
    every violation and every evidence blob into one response. Four hundred
    audits already made it megabytes; a server left running for a term would
    eventually time out on its own audit list, and the dashboard calls this on
    every page load.
    """
    jobs = (db.query(models.AuditJob)
            .order_by(models.AuditJob.created_at.desc())
            .offset(offset).limit(limit).all())
    return [_to_job_out(j) for j in jobs]


@app.get("/dashboard/summary")
def summary(db: Session = Depends(get_db),
            _key: str = Depends(require_api_key)):
    total_jobs = db.query(func.count(models.AuditJob.id)).scalar() or 0
    total_violations = db.query(func.count(models.ViolationRecord.id)).scalar() or 0
    by_pattern = (
        db.query(models.ViolationRecord.pattern_code, models.ViolationRecord.pattern_name,
                 func.count(models.ViolationRecord.id))
        .group_by(models.ViolationRecord.pattern_code, models.ViolationRecord.pattern_name)
        .all()
    )
    return {
        "total_audits": total_jobs,
        "total_violations": total_violations,
        "by_pattern": [{"code": c, "name": n, "count": cnt} for c, n, cnt in by_pattern],
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
    job = db.query(models.AuditJob).get(job_id)
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
    """Remove screenshot files belonging to a job. Screenshots are full-page
    captures of audited sites and are the highest-risk artifact this app
    produces, so deletion must reach disk, not just the database rows."""
    for v in job.violations:
        if not v.screenshot_path:
            continue
        try:
            if os.path.isfile(v.screenshot_path):
                os.remove(v.screenshot_path)
        except OSError:
            # Best-effort: a missing or already-removed file must not block
            # deletion of the database records.
            pass


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
    job_id = submit_audit_job(payload.adapter_name, None)
    job = db.query(models.AuditJob).get(job_id)
    return _to_job_out(job)


@app.get("/demo-audit/{job_id}", response_model=schemas.AuditJobOut)
def get_demo_audit(job_id: int = PathParam(..., ge=1, lt=2**63, description="Audit job id"), db: Session = Depends(get_db)):
    """Read back a demo job. Scoped to demo adapters so this cannot be used as
    an unauthenticated window onto real audits someone else ran."""
    if not config.PUBLIC_DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    job = db.query(models.AuditJob).get(job_id)
    if not job or job.adapter_name not in config.PUBLIC_DEMO_ADAPTERS:
        raise HTTPException(status_code=404, detail="Demo audit not found")
    return _to_job_out(job)


@app.get("/")
def root():
    return {"status": "ok", "service": "Dark Pattern Auditor"}


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
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DASHBOARD_DIR = os.path.join(_ROOT, "dashboard")
if os.path.isdir(_DASHBOARD_DIR):
    app.mount("/dashboard", StaticFiles(directory=_DASHBOARD_DIR, html=True), name="dashboard")

# The public demo: a storefront the auditor can actually walk over HTTP, next
# to the findings it produces. Serving the fixtures here rather than opening
# them as file:// is what makes the demo mean something — the crawler takes
# the identical code path it would against a live commercial site, including
# DNS, redirects, caching and every other difference between fetching a page
# and reading a file off disk.
_DEMO_DIR = os.path.join(_ROOT, "demo")
_STOREFRONT_DIR = os.path.join(_ROOT, "fixtures", "ecommerce_dark")
_STOREFRONT_CLEAN_DIR = os.path.join(_ROOT, "fixtures", "ecommerce_clean")
if os.path.isdir(_DEMO_DIR):
    app.mount("/demo", StaticFiles(directory=_DEMO_DIR, html=True), name="demo")
if os.path.isdir(_STOREFRONT_DIR):
    app.mount("/storefront", StaticFiles(directory=_STOREFRONT_DIR, html=True), name="storefront")
if os.path.isdir(_STOREFRONT_CLEAN_DIR):
    app.mount("/storefront-clean", StaticFiles(directory=_STOREFRONT_CLEAN_DIR, html=True),
              name="storefront_clean")
