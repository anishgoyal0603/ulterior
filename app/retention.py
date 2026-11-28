"""
Retention enforcement.

Attack this closes: an attacker (or just normal use) can queue audits
repeatedly, and each one writes full-page PNG screenshots to disk plus rows
to the database, with nothing ever removing them. Rate limiting slows that
down but does not bound it -- at 5 audits/minute the storage grows forever.
Unbounded growth is both a denial-of-service path (fill the disk, the app
and the database stop working) and a privacy problem (captured page content
lingers indefinitely; see the privacy audit in the README).

Two independent bounds, because either alone can be defeated:
  - RETENTION_DAYS: age-based. Stops slow accumulation over months.
  - MAX_STORED_AUDITS: count-based. Stops a fast burst filling the disk
    before anything is old enough to expire.
"""

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone

from .db import SessionLocal
from .models import AuditJob, ExtensionReport, PriceObservation
from . import config

logger = logging.getLogger(__name__)


def job_evidence_dir(job_id: int) -> str:
    from capture.funnel_walker import DEFAULT_EVIDENCE_DIR
    return os.path.join(DEFAULT_EVIDENCE_DIR, f"job_{int(job_id)}")


def evidence_of(job: AuditJob):
    """Everything on disk that belongs to this audit, read BEFORE its rows are
    deleted (the rows say where the screenshots are)."""
    return ([v.screenshot_path for v in job.violations if v.screenshot_path], job_evidence_dir(job.id))


def remove_evidence(evidence) -> None:
    """Delete files gathered by evidence_of(). Call it only AFTER the database
    commit succeeded: deleting first meant a failed commit left the audit
    rows in place with their screenshots gone, and Verify evidence reported
    "Incomplete" for ever."""
    paths, folder = evidence
    for path in paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    shutil.rmtree(folder, ignore_errors=True)


def delete_job_evidence(job: AuditJob) -> None:
    """Remove EVERY screenshot an audit took, not only the ones a finding
    points at. The walker saves a full-page PNG for every step into the job's
    own folder; deleting just the violations' files left clean steps, clean
    audits and failed audits on disk forever."""
    for v in job.violations:
        if not v.screenshot_path:
            continue
        try:
            if os.path.isfile(v.screenshot_path):
                os.remove(v.screenshot_path)
        except OSError:
            pass
    shutil.rmtree(job_evidence_dir(job.id), ignore_errors=True)


_delete_evidence_files = delete_job_evidence   # older name, kept for callers


def _chunks(ids, size=500):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def purge_expired() -> int:
    """Remove audits past the age limit and any beyond the count cap.
    Returns the number of audits removed."""
    db = SessionLocal()
    removed = 0
    doomed = []          # evidence to delete once the commit has succeeded
    try:
        # 1. Age-based purge
        if config.RETENTION_DAYS > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=config.RETENTION_DAYS)
            # created_at is stored naive-UTC by the model default; compare naive.
            cutoff_naive = cutoff.replace(tzinfo=None)
            expired = (db.query(AuditJob)
                       .filter(AuditJob.created_at < cutoff_naive,
                               AuditJob.status.notin_(("pending", "running")))
                       .all())
            for job in expired:
                doomed.append(evidence_of(job))
                db.delete(job)   # cascade removes violations
                removed += 1
            # SessionLocal has autoflush off: without this the count below
            # still sees the rows deleted above, double-counts them, and
            # leaves the real overflow in place.
            db.flush()

        # 2a. Public demo runs have their own, small cap. They come from an
        #     unauthenticated endpoint; sharing one cap with customers meant a
        #     burst of demo traffic could push real audits (and their
        #     evidence) out of the database.
        demo = (AuditJob.org == config.DEMO_ORG)
        if config.MAX_STORED_DEMO_AUDITS > 0:
            extra_demo = (db.query(AuditJob).filter(demo).count() - config.MAX_STORED_DEMO_AUDITS)
            if extra_demo > 0:
                for job in (db.query(AuditJob)
                            .filter(demo, AuditJob.status.notin_(("pending", "running")))
                            .order_by(AuditJob.created_at.asc()).limit(extra_demo).all()):
                    doomed.append(evidence_of(job))
                    db.delete(job)
                    removed += 1
                db.flush()

        # 2b. Count-based cap on everything else: keep only the newest MAX_STORED_AUDITS
        not_demo = (AuditJob.org.is_(None) | (AuditJob.org != config.DEMO_ORG))
        if config.MAX_STORED_AUDITS > 0:
            total = db.query(AuditJob).filter(not_demo).count()
            excess = total - config.MAX_STORED_AUDITS
            if excess > 0:
                oldest = (
                    db.query(AuditJob)
                    .filter(not_demo, AuditJob.status.notin_(("pending", "running")))
                    .order_by(AuditJob.created_at.asc())
                    .limit(excess)
                    .all()
                )
                for job in oldest:
                    doomed.append(evidence_of(job))
                    db.delete(job)
                    removed += 1

        # Community prices: their own, longer age limit, and a count cap.
        if config.COMMUNITY_RETENTION_DAYS > 0:
            old = (datetime.now(timezone.utc) - timedelta(days=config.COMMUNITY_RETENTION_DAYS)).date()
            (db.query(PriceObservation).filter(PriceObservation.observed_on < old)
             .delete(synchronize_session=False))
        if config.MAX_PRICE_OBSERVATIONS > 0:
            extra = db.query(PriceObservation).count() - config.MAX_PRICE_OBSERVATIONS
            if extra > 0:
                ids = [r.id for r in db.query(PriceObservation.id).order_by(PriceObservation.id.asc()).limit(extra)]
                # In chunks: SQLite refuses an IN list longer than its
                # variable limit (999 on older builds).
                for chunk in _chunks(ids):
                    (db.query(PriceObservation).filter(PriceObservation.id.in_(chunk))
                     .delete(synchronize_session=False))

        # Extension reports: the same age limit, and their own count cap.
        # (Not counted in the return value, which is about audits.)
        if config.RETENTION_DAYS > 0:
            (db.query(ExtensionReport)
             .filter(ExtensionReport.created_at < cutoff_naive)
             .delete(synchronize_session=False))
        if config.MAX_STORED_EXTENSION_REPORTS > 0:
            total = db.query(ExtensionReport).count()
            excess = total - config.MAX_STORED_EXTENSION_REPORTS
            if excess > 0:
                ids = [r.id for r in db.query(ExtensionReport.id)
                       .order_by(ExtensionReport.created_at.asc()).limit(excess)]
                for chunk in _chunks(ids):
                    (db.query(ExtensionReport).filter(ExtensionReport.id.in_(chunk))
                     .delete(synchronize_session=False))

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for evidence in doomed:
        remove_evidence(evidence)
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = purge_expired()
    print(f"Retention purge removed {count} audit(s).")
