from typing import Optional, List, Dict, Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthorizationIn(BaseModel):
    """Who allowed this site to be audited.

    Required for every pasted-URL audit. Ulterior audits only sites whose
    owner agreed (or public test sandboxes that invite automated testing),
    and the report prints this record, so the permission travels with the
    findings."""
    model_config = ConfigDict(extra="forbid")

    owner_name: str = Field(min_length=2, max_length=120)      # person who gave permission
    organisation: str = Field(min_length=2, max_length=160)    # store or company
    authority: Literal["owner", "written_permission", "public_test_site"]
    reference: str = Field(default="", max_length=160)         # e.g. "Signed form, 2 Dec 2026"
    confirmed: bool

    @field_validator("confirmed")
    @classmethod
    def _must_confirm(cls, v):
        if v is not True:
            raise ValueError("confirm that you have permission to audit this site")
        return v

    # mode="before": collapse whitespace BEFORE min_length is checked.
    # Afterwards, "   " passed min_length=2 and was then stored as "",
    # an empty permission record that the gate accepted.
    @field_validator("owner_name", "organisation", "reference", mode="before")
    @classmethod
    def _plain_text(cls, v):
        v = " ".join(str(v if v is not None else "").split())
        if any(ch in v for ch in "<>"):
            raise ValueError("plain text only")
        return v


class AuditJobCreate(BaseModel):
    # extra="forbid": reject unknown fields outright rather than silently
    # ignoring them. Pydantic already ignores extras, so mass assignment was
    # not exploitable, but a 422 tells a caller their field was wrong instead
    # of letting them believe it took effect.
    model_config = ConfigDict(extra="forbid")

    # Exactly one of these two must be provided:
    #   adapter_name -- a pre-registered adapter (demo/benchmark sites)
    #   target_urls  -- self-serve mode: paste 1+ real URLs, no code needed.
    #                   Each becomes one funnel step (step_1, step_2, ...).
    #                   Every URL passes through app/ssrf_guard.py before
    #                   the crawler ever touches it.
    adapter_name: Optional[str] = None
    # Capped, because every URL costs a blocking DNS resolution in the request
    # handler and then up to 30 seconds of page load in a worker. An uncapped
    # list let one request pin a worker for days and stall every other audit,
    # including the public demo. Twenty steps is far more than any real
    # checkout funnel has.
    target_urls: Optional[List[str]] = Field(default=None, min_length=1, max_length=20)

    # Opt in to the Layer 3 LLM pass. Offline language detection ALWAYS runs
    # regardless of this flag -- setting it only ADDS findings that the
    # deterministic rules missed, and every such finding is marked
    # "source": "anthropic_api" in its evidence so a reader can weigh it
    # separately. Default false: an audit should behave identically on every
    # machine, cost nothing, and send nothing off-box unless asked.
    use_llm: bool = False

    # Discovery mode: give ONE url and let the crawler find its own way from
    # that page to the checkout, instead of listing every step yourself.
    # Nobody knows every URL in a funnel of a site they have not already
    # studied, which is what made the multi-URL form useful only in theory.
    auto_discover: bool = False

    # Required with target_urls (checked in app/main.py); not needed for the
    # registered demo and sandbox sites.
    authorization: Optional[AuthorizationIn] = None


class ViolationOut(BaseModel):
    id: int
    pattern_code: str
    pattern_name: str
    step_name: str
    confidence: float
    layer: int
    explanation: str
    evidence: dict
    # PRIVACY: deliberately exposes only WHETHER evidence imagery exists, not
    # the server filesystem path. Screenshots are full-page captures that may
    # contain session content from the audited site; the path itself also
    # leaks server directory structure to any API caller.
    has_screenshot: bool = False

    # model_config, not `class Config` -- the class-based form is the
    # Pydantic v1 style, deprecated in v2 and removed in v3.
    model_config = ConfigDict(from_attributes=True)


class AuditJobOut(BaseModel):
    id: int
    site_name: Optional[str] = None
    adapter_name: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    # Present only for auto-discovered audits: what the crawler reached, what
    # it could not find, and why. Reported alongside the findings so an
    # incomplete walk is never mistaken for a clean result.
    discovery: Optional[dict] = None
    # The permission record given when the audit was started, if any.
    authorization: Optional[dict] = None
    # Fingerprints of the evidence (app/integrity.py); null until it finishes.
    evidence_manifest: Optional[dict] = None
    # 2.4: per-pattern Observed / Not observed / Partially assessed / Not assessed.
    coverage: Optional[List[dict]] = None
    # 2.4: the person who checked the findings before the report went out.
    review: Optional[dict] = None
    # 2.4: how many times a worker has started this job (retries after a crash).
    attempts: int = 0
    violations: List[ViolationOut] = []

    # model_config, not `class Config` -- the class-based form is the
    # Pydantic v1 style, deprecated in v2 and removed in v3.
    model_config = ConfigDict(from_attributes=True)


class DemoAuditCreate(BaseModel):
    """Input for the public demo endpoint.

    Deliberately narrower than AuditJobCreate: no target_urls, no use_llm, no
    anything. A public endpoint should accept the smallest input that does the
    job, so there is less to reason about and less to get wrong.
    """
    model_config = ConfigDict(extra="forbid")

    adapter_name: str


# ------------------------------------------------- browser-extension reports
#
# The one write endpoint that needs no API key, so it accepts the smallest,
# most tightly bounded shape that does the job: a site name, a version, and
# up to twenty findings of capped length. Anything else is a 422.


_SITE_RE = r"^[a-z0-9.-]{1,120}$"


class ExtensionFindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^DP-(0[1-9]|1[0-3])$")
    tier: Literal["provable", "corroborated", "indicative"]
    title: str = Field(max_length=300)
    detail: str = Field(default="", max_length=600)
    evidence: Dict[str, str] = Field(default_factory=dict)

    @field_validator("evidence")
    @classmethod
    def _small_evidence(cls, v):
        if len(v) > 10:
            raise ValueError("at most 10 evidence items")
        for k, val in v.items():
            if len(k) > 60 or len(val) > 300:
                raise ValueError("evidence keys up to 60 and values up to 300 characters")
        return v


class ExtensionReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(pattern=_SITE_RE)
    version: str = Field(pattern=r"^[0-9]{1,3}(\.[0-9]{1,3}){1,3}$")
    page_type: Literal["checkout", "product", "other"] = "other"
    captured_at: Optional[str] = Field(default=None, max_length=40)
    findings: List[ExtensionFindingIn] = Field(min_length=1, max_length=20)


class ExtensionReportOut(BaseModel):
    id: int
    created_at: datetime
    site: str
    version: Optional[str] = None
    page_type: Optional[str] = None
    findings: List[dict]


# ------------------------------------------------------ community prices

class PriceObservationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=230)
    price: float = Field(gt=0, le=10_000_000)


class PricesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: List[PriceObservationIn] = Field(min_length=1, max_length=20)


class PriceHistoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=230)
    days: int = Field(default=365, ge=1, le=730)


class PriceLatestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: List[str] = Field(min_length=1, max_length=100)


class PriceDropsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=7, ge=2, le=30)
    limit: int = Field(default=15, ge=1, le=50)


class ReviewIn(BaseModel):
    """A person's sign-off that every finding was checked against its
    screenshot before the report was sent."""
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=2, max_length=120)
    outcome: Literal["confirmed", "edited", "rejected_some"]
    note: str = Field(default="", max_length=600)

    @field_validator("reviewer", "note", mode="before")
    @classmethod
    def _plain(cls, v):
        v = " ".join(str(v if v is not None else "").split())
        if any(ch in v for ch in "<>"):
            raise ValueError("plain text only")
        return v
