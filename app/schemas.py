from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


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
    target_urls: Optional[List[str]] = Field(default=None, max_length=20)

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
