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
from datetime import datetime, timedelta, timezone

from .db import SessionLocal
from .models import AuditJob
from . import config

logger = logging.getLogger(__name__)


def _delete_evidence_files(job: AuditJob) -> None:
    for v in job.violations:
        if not v.screenshot_path:
            continue
        try:
            if os.path.isfile(v.screenshot_path):
                os.remove(v.screenshot_path)
        except OSError:
            pass


def purge_expired() -> int:
    """Remove audits past the age limit and any beyond the count cap.
    Returns the number of audits removed."""
    db = SessionLocal()
    removed = 0
    try:
        # 1. Age-based purge
        if config.RETENTION_DAYS > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=config.RETENTION_DAYS)
            # created_at is stored naive-UTC by the model default; compare naive.
            cutoff_naive = cutoff.replace(tzinfo=None)
            expired = db.query(AuditJob).filter(AuditJob.created_at < cutoff_naive).all()
            for job in expired:
                _delete_evidence_files(job)
                db.delete(job)   # cascade removes violations
                removed += 1

        # 2. Count-based cap: keep only the newest MAX_STORED_AUDITS
        if config.MAX_STORED_AUDITS > 0:
            total = db.query(AuditJob).count()
            excess = total - config.MAX_STORED_AUDITS
            if excess > 0:
                oldest = (
                    db.query(AuditJob)
                    .order_by(AuditJob.created_at.asc())
                    .limit(excess)
                    .all()
                )
                for job in oldest:
                    _delete_evidence_files(job)
                    db.delete(job)
                    removed += 1

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = purge_expired()
    print(f"Retention purge removed {count} audit(s).")
