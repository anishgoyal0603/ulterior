from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Text, ForeignKey, Index
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class AuditJob(Base):
    __tablename__ = "audit_jobs"
    # One audit per (workspace, Idempotency-Key). Checking first and inserting
    # second let two identical POSTs arriving together both create a job; the
    # database index is what makes "the same request twice is one audit" true.
    __table_args__ = (Index("uq_audit_jobs_org_idempotency", "org", "idempotency_key", unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    site_name = Column(String(200))
    adapter_name = Column(String(100))     # which adapter was used
    status = Column(String(20), default="pending")   # pending | running | done | failed
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    # JSON record of how automatic funnel discovery went: which stages it
    # reached, which it could not find, and why. Null for adapter-driven and
    # explicit-URL audits, which have nothing to discover.
    discovery_json = Column(Text, nullable=True)
    # Who gave permission for this audit, recorded before it ran: required for
    # every pasted-URL audit, empty for the registered demo/sandbox sites.
    authorization_json = Column(Text, nullable=True)
    # SHA-256 of every screenshot and of the stored findings, plus one
    # fingerprint over all of it (app/integrity.py). Printed on the report.
    evidence_manifest_json = Column(Text, nullable=True)

    # --- 2.4: ownership, durable jobs, coverage, review -------------------
    # Which organisation owns this audit. NULL = the default team workspace
    # (every audit made before 2.4, and the public demo).
    org = Column(String(80), nullable=True, index=True)
    # Everything needed to run (or re-run) the job, so a restart can resume it.
    request_json = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=True)            # NULL = 0
    heartbeat_at = Column(DateTime, nullable=True)       # updated while running
    cancel_requested = Column(Integer, nullable=True)    # 1 = stop at the next safe point
    # Optional client-chosen key: the same POST sent twice makes one audit.
    idempotency_key = Column(String(80), nullable=True, index=True)
    # Observed / not observed / partially assessed / not assessed, per pattern.
    coverage_json = Column(Text, nullable=True)
    # Who checked the findings against the screenshots before the report went out.
    review_json = Column(Text, nullable=True)

    # cascade: deleting a job must delete its violations in the same
    # operation. Without this, bulk-deleting children separately and then
    # deleting the parent raises StaleDataError, because the session still
    # holds the loaded children and tries to null their foreign keys.
    #
    # No passive_deletes: with it the ORM skipped loading the children and
    # left their removal to the database -- but the foreign key had no ON
    # DELETE CASCADE, so PostgreSQL refused the delete and SQLite (foreign
    # keys off) left orphan rows. The ORM now deletes the children itself,
    # and the key also cascades for databases created from now on.
    violations = relationship(
        "ViolationRecord", back_populates="job",
        cascade="all, delete-orphan",
    )


class ViolationRecord(Base):
    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("audit_jobs.id", ondelete="CASCADE"))
    pattern_code = Column(String(10), index=True)
    pattern_name = Column(String(100))
    step_name = Column(String(100))
    confidence = Column(Float)
    layer = Column(Integer)
    explanation = Column(Text)
    evidence_json = Column(Text)
    screenshot_path = Column(String(300), nullable=True)

    job = relationship("AuditJob", back_populates="violations")


class ExtensionReport(Base):
    """A page a browser-extension user chose to report.

    Sent only when the person switched reporting on in the extension, read
    the exact payload, and pressed Send. Holds the site name and the
    findings -- never a page address, never anything identifying the person
    who sent it (no IP, no account). Bounded by the same retention purge as
    audits.
    """
    __tablename__ = "extension_reports"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    site = Column(String(120), index=True)
    version = Column(String(20))
    page_type = Column(String(20))
    findings_json = Column(Text)


class PriceObservation(Base):
    """One price for one product on one day, sent by an extension user who
    switched community prices on.

    Product id and price only: no URL, no title, no IP, no user. The day is
    set by the server (a client cannot backdate), and history is served as
    the median of each day's reports, so one bad report cannot move it.
    """
    __tablename__ = "price_observations"

    id = Column(Integer, primary_key=True)
    product_key = Column(String(230), index=True)
    site = Column(String(60), index=True)
    price = Column(Float)
    observed_on = Column(Date, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
