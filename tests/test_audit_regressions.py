"""
Regressions for the defects found in the code audit.

Each test below corresponds to a bug that WAS in this codebase and could
actually happen. The docstrings say what went wrong, because a test named
after a symptom is easy to delete by accident and a test that explains the
harm is not.

The false-positive tests matter most. A finding from this tool is a public
statement that a named company's checkout is deceptive. Reporting an honest
shop is worse than reporting nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import re
import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth import _key_matches
from app.db import init_db
from app.main import app
from app.middleware import limiter, SlidingWindowLimiter
from capture.state_extractor import PageState, URGENCY_PATTERNS, contrast_ratio
from capture.navigation_guard import NavigationGuard
from detectors.layer1_rules import detect_fake_urgency, detect_basket_sneaking_and_prechecked
from detectors.layer1_flow import UNRELATED_CONSENT_TERMS, detect_forced_action
from detectors.layer3_language import _safe_confidence


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client():
    limiter.reset()
    return TestClient(app, raise_server_exceptions=False)


def _state(text, step="listing", **kw):
    return PageState(
        step_name=step, url="https://shop.example/p", price=kw.get("price", 299.0),
        price_source="dom", line_items=kw.get("line_items", []),
        checkboxes=kw.get("checkboxes", []), buttons=[], full_text=text,
        urgency_phrases_found=[p for p in URGENCY_PATTERNS if re.search(p, text, re.I)],
        raw_html="<html></html>",
    )


class _Trace:
    def __init__(self, pair=None, states=None):
        self.reload_pairs = {"listing": pair} if pair else {}
        self.states = states or []


# --- DP-01: the detector was accusing honest shops ------------------------

@pytest.mark.parametrize("copy", [
    "Premium Phone Case. In stock: 42 units. Ships tomorrow.",
    "Sale ends Sunday. Hurry, limited time offer on selected items.",
    "Independence Day sale. Hurry, offer ends 15 August.",
    "Hurry while stocks last on our festive range.",
])
def test_dp01_does_not_fire_on_honest_urgency_copy(copy):
    """The comparison used to re-match the same regex on both loads, so for a
    phrase like `hurry` the two sides were equal BY CONSTRUCTION. Any page
    saying "Hurry" twice was reported at 0.9 as having a fabricated countdown,
    and "In stock: 42 units" -- the honest way to state availability -- was
    reported as fake scarcity. There is no evidence of fabrication in any of
    these strings."""
    page = _state(copy)
    assert detect_fake_urgency(_Trace((page, page))) == []


def test_dp01_still_catches_a_countdown_that_does_not_count_down():
    """A clock reading the same value after real elapsed time is provably not
    measuring a deadline. This is the case the detector exists for."""
    page = _state("Offer ends in: 00:04:59. Hurry!")
    found = detect_fake_urgency(_Trace((page, page)))
    assert len(found) == 1
    assert found[0].evidence["claim_kind"] == "countdown"
    assert found[0].evidence["evidence_tier"] == "corroborated"


def test_dp01_reports_a_static_stock_count_only_as_indicative():
    """A shop that really does have two left still has two left a second
    later. That is consistent with fake scarcity and is not proof of it, so it
    may not be published at the same confidence as a frozen countdown."""
    page = _state("Hurry! Only 2 left in stock!")
    found = detect_fake_urgency(_Trace((page, page)))
    assert len(found) == 1
    assert found[0].evidence["evidence_tier"] == "indicative"
    assert found[0].confidence <= 0.6
    assert "human should confirm" in found[0].explanation


def test_in_stock_is_not_treated_as_urgency_language():
    assert not any(re.search(p, "In stock: 42 units", re.I) for p in URGENCY_PATTERNS)


# --- Contrast: a transparent background was scored as pure black ----------

def test_transparent_background_is_not_scored_as_black():
    """getComputedStyle returns rgba(0, 0, 0, 0) for any element that sets no
    background -- most of them. Reading that literally made a plain dark-text
    button measure 1.66:1 instead of its real 12.6:1, so DP-06 and DP-09
    reported perfectly legible controls as unreadable."""
    assert contrast_ratio("rgb(51,51,51)", "rgba(0, 0, 0, 0)") > 3.0


def test_contrast_understands_hex_as_its_docstring_promised():
    """It did not, so contrast_ratio("#ffffff", "#000000") returned 1.0 --
    the maximum-contrast pair reported as the minimum."""
    assert contrast_ratio("#ffffff", "#000000") == 21.0
    assert contrast_ratio("#fff", "#000") == 21.0


def test_contrast_still_reports_genuinely_unreadable_text():
    assert contrast_ratio("rgb(204,204,204)", "rgb(240,240,240)") < 2.0


def test_every_contrast_measurement_resolves_a_painted_background():
    """The button and disclosure-label extractors read backgroundColor
    directly while the link extractor walked ancestors for a painted one.
    They must all use the same lookup."""
    source = (Path(__file__).parent.parent / "capture" / "state_extractor.py").read_text()
    assert source.count("_BG_WALK_JS") >= 4, "not every extractor uses the ancestor walk"
    assert 'evaluate("el => getComputedStyle(el).backgroundColor")' not in source


# --- DP-02: one malformed attribute accused every control -----------------

def test_dp02_ignores_a_line_item_with_an_empty_name():
    """`"" in anything` is True. A single <div data-name="" data-price="99">
    put "" into the chargeable-name set, and every pre-ticked control on the
    page -- "Remember me", "Save this address" -- became a PROVABLE 1.0
    basket-sneaking finding."""
    from capture.state_extractor import CheckboxState
    cb = CheckboxState(id="remember", label_text="Remember me", checked=True,
                       is_prechecked=True, required=False, input_type="checkbox")
    page = _state("Cart", step="cart", checkboxes=[cb],
                  line_items=[{"name": "", "price": 99.0}])
    assert detect_basket_sneaking_and_prechecked(_Trace(states=[page])) == []


# --- DP-04: lawful consent notices were reported as dark patterns ---------

def test_promotions_is_actually_matched():
    """`\\bpromotional?\\b` parses as "promotiona" + optional "l", so it matched
    "promotional" but never "promotion" or "promotions"."""
    for text in ("Send me promotions", "Send me a promotion", "promotional emails"):
        assert any(re.search(p, text, re.I) for p in UNRELATED_CONSENT_TERMS), text


@pytest.mark.parametrize("label", [
    "I have read the Privacy Policy, including how my data is shared with third parties",
    "I agree our delivery partners may contact me about this shipment",
    "I acknowledge the Terms and Conditions",
])
def test_dp04_does_not_report_a_lawful_transaction_consent(label):
    """These matched `third parties` / `partners` and were reported at
    PROVABLE 1.0 as consent "not required to complete the transaction" --
    accusing a shop of a dark pattern for showing the disclosure the law
    requires of it."""
    from capture.state_extractor import CheckboxState
    cb = CheckboxState(id="c1", label_text=label, checked=False,
                       is_prechecked=False, required=True, input_type="checkbox")
    page = _state("Checkout", step="checkout", checkboxes=[cb])
    codes = [v.pattern_code for v in detect_forced_action(_Trace(states=[page]))]
    assert "DP-04" not in codes


def test_dp04_still_reports_bundled_marketing_consent():
    from capture.state_extractor import CheckboxState
    cb = CheckboxState(id="c1", label_text="Send me marketing offers by SMS",
                       checked=False, is_prechecked=False, required=True,
                       input_type="checkbox")
    page = _state("Checkout", step="checkout", checkboxes=[cb])
    codes = [v.pattern_code for v in detect_forced_action(_Trace(states=[page]))]
    assert "DP-04" in codes


# --- Layer 3: a model's bad field aborted the whole audit ------------------

@pytest.mark.parametrize("value", [None, "high", "", [], float("nan")])
def test_a_malformed_model_confidence_cannot_abort_the_audit(value):
    """float() sat outside the try/except around the LLM call, so
    "confidence": null raised TypeError and killed the audit at the last step
    -- after all the browser work was already done, because of a field the
    model made up."""
    assert 0.0 <= _safe_confidence({"confidence": value}) <= 1.0


# --- Auth: a non-ASCII header was a 500 -----------------------------------

def test_a_non_ascii_api_key_is_rejected_not_a_server_error():
    """secrets.compare_digest raises TypeError on a str with any character
    above U+007F. `X-API-Key: café` produced a 500 and a full traceback in the
    log on every attempt -- an unauthenticated log-flooding primitive."""
    assert _key_matches("café", ["k" * 32]) is False
    assert _key_matches("k" * 32, ["k" * 32]) is True


# --- API input bounds ------------------------------------------------------

def test_an_enormous_job_id_is_rejected_not_a_500(client):
    """job_id: int accepts arbitrary-precision Python ints; SQLite then raises
    OverflowError, which the catch-all turned into a 500."""
    r = client.get("/audits/9223372036854775808")
    assert r.status_code in (404, 422), r.text
    assert r.status_code != 500


def test_target_urls_is_capped(client):
    """Every URL costs a blocking DNS resolution in the request handler and up
    to 30s of page load in a worker. Uncapped, one request could pin a worker
    for days and stall every other audit."""
    r = client.post("/audits", json={"target_urls": [f"https://e{i}.example.com" for i in range(50)]})
    assert r.status_code == 422


# --- Rate limiting ---------------------------------------------------------

def test_the_delete_limit_counts_the_route_not_the_path():
    """The bucket key was the full path, so DELETE /audits/1 and
    DELETE /audits/2 were different buckets and "3 destructive operations per
    hour" never fired for the route it was written for."""
    fresh = SlidingWindowLimiter()
    allowed = [fresh.check("1.2.3.4:DELETE:/audits", 3, 3600) for _ in range(6)]
    assert allowed[:3] == [True, True, True]
    assert allowed[3:] == [False, False, False]


def test_the_public_demo_is_rate_limited(client):
    """/demo-audit launches a headless browser for anyone with no key, and was
    matched by no route rule -- so it fell through to the default 60/minute
    while config.py claimed the 5/minute audit limit applied."""
    from app.middleware import _ROUTE_LIMITS
    assert any(prefix == "/demo-audit" for _, prefix, _, _, _ in _ROUTE_LIMITS)


def test_the_limiter_forgets_idle_buckets():
    """Entries were emptied of timestamps but never removed, so the dict grew
    forever -- one permanent entry per IP per path, and the middleware runs
    before routing so 404s counted too."""
    fresh = SlidingWindowLimiter()
    for i in range(200):
        fresh.check(f"ip{i}:default", 60, 60)
    assert len(fresh._hits) == 200
    # Age every bucket past the TTL, then force an eviction sweep.
    import time as _time
    from app import middleware as mw
    for bucket in fresh._hits.values():
        bucket[-1] = _time.monotonic() - (mw._BUCKET_TTL_SECONDS + 10)
    fresh._last_evict = 0.0
    fresh.check("someone-new:default", 60, 60)
    assert len(fresh._hits) == 1, f"idle buckets were not evicted: {len(fresh._hits)}"


# --- SSRF: the guard was bypassed by any redirect -------------------------

def test_the_navigation_guard_blocks_the_cloud_metadata_endpoint():
    """The pre-flight check validates a URL the browser has not fetched yet.
    A validated host can redirect to 169.254.169.254 and the crawler would
    fetch the instance's IAM credentials, store them in the page text and
    return them in the finding evidence."""
    guard = NavigationGuard()
    assert guard._is_allowed("http://169.254.169.254/latest/meta-data/") is False
    assert guard._is_allowed("http://10.0.0.5:5432/") is False
    assert guard._is_allowed("http://127.0.0.1:8000/admin") is False


def test_the_navigation_guard_allows_ordinary_pages_and_inert_schemes():
    """A guard that blocks everything is not a guard, it is an outage."""
    guard = NavigationGuard()
    assert guard._is_allowed("data:image/png;base64,iVBORw0KGgo=") is True
    assert guard._is_allowed("about:blank") is True
    assert guard._is_allowed("blob:https://example.com/abc") is True


def test_the_navigation_guard_refuses_file_urls_unless_asked():
    """Local fixtures load over file://. A crawl of a real site must never be
    able to read the server's own disk."""
    assert NavigationGuard()._is_allowed("file:///etc/passwd") is False
    assert NavigationGuard(allow_file=True)._is_allowed("file:///tmp/x.html") is True


def test_both_walkers_install_the_navigation_guard():
    """A guard that only one code path uses protects only one code path."""
    for name in ("funnel_walker.py", "funnel_discovery.py"):
        src = (Path(__file__).parent.parent / "capture" / name).read_text()
        assert "install_navigation_guard" in src, name


# --- Evidence isolation ----------------------------------------------------

def test_screenshots_are_written_per_job():
    """Paths were built from adapter and step name alone, and discovery's step
    names are fixed literals -- so every discovery audit ever run wrote to the
    same five files. Job 1's stored evidence path then showed job 2's
    screenshots, and deleting job 2 removed the file job 1 pointed at."""
    src = (Path(__file__).parent.parent / "app" / "tasks.py").read_text()
    assert 'f"job_{job_id}"' in src, "evidence directory is not scoped to the job"
    assert "screenshot_dir=evidence_dir" in src


def test_discovery_notes_are_redacted_before_storage():
    """The notes quote button text straight off the audited page. Every other
    captured string is redacted; this one was not, so a logged-in checkout
    leaked a name and phone number into the database and back out of the API."""
    src = (Path(__file__).parent.parent / "app" / "tasks.py").read_text()
    assert "redact_list(safe_discovery[field])" in src


def test_a_failed_job_rolls_back_before_recording_the_failure():
    """If the exception came from the commit, the session is dead and every
    later statement raises PendingRollbackError -- including the ones
    recording the failure. The job then sat at "running" forever."""
    src = (Path(__file__).parent.parent / "app" / "tasks.py").read_text()
    failure_block = src.split("except Exception as e:")[1]
    assert "db.rollback()" in failure_block
    assert failure_block.index("db.rollback()") < failure_block.index('job.status = "failed"')


def test_a_genuine_countdown_that_really_counts_down_is_not_reported():
    """The comparison must not be "the two clocks are identical". A real
    countdown captured twice reads 04:59 then 04:56, and a FAKE one that
    resets reads 04:58 then 04:59 -- close but unequal, which an equality test
    misses entirely. The question that survives capture jitter is whether it
    went DOWN."""
    first = _state("Offer ends in: 00:04:59")
    second = _state("Offer ends in: 00:04:56")
    assert detect_fake_urgency(_Trace((first, second))) == []


def test_a_countdown_that_resets_higher_is_still_caught():
    """A fake timer restarting from its start value reads LOWER then HIGHER
    across the two captures, because the second capture happens earlier in the
    page's life."""
    first = _state("Offer ends in: 00:04:58")
    second = _state("Offer ends in: 00:04:59")
    found = detect_fake_urgency(_Trace((first, second)))
    assert len(found) == 1
    assert found[0].evidence["claim_kind"] == "countdown"


# --- DP-05: a newsletter footer is not a subscription trap ----------------

@pytest.mark.parametrize("cta", [
    "Subscribe to our newsletter",
    "Subscribe for updates",
])
def test_a_newsletter_signup_is_not_a_paid_commitment(cta):
    """`\\bsubscribe\\b` was on the commitment-CTA list, so a newsletter box --
    present in the footer of nearly every shop on the internet -- made that
    page a "commitment step". If any later page then said "monthly" or
    "subscription" anywhere, DP-05 fired at 0.85 CORROBORATED claiming the
    recurring nature of a charge had been concealed, on a site with no
    subscription product at all."""
    from detectors.layer1_flow import COMMITMENT_CTA, NON_COMMITMENT_SUBSCRIBE
    from detectors.vocab import matches_any
    assert matches_any(cta, COMMITMENT_CTA)
    assert matches_any(cta, NON_COMMITMENT_SUBSCRIBE), "should be excluded as non-paid"


@pytest.mark.parametrize("cta", ["Subscribe", "Start my free trial", "Upgrade to Premium"])
def test_a_real_paid_commitment_is_still_detected(cta):
    from detectors.layer1_flow import COMMITMENT_CTA, NON_COMMITMENT_SUBSCRIBE
    from detectors.vocab import matches_any
    assert matches_any(cta, COMMITMENT_CTA)
    assert not matches_any(cta, NON_COMMITMENT_SUBSCRIBE)


# --- API: unbounded responses and a retention policy that never ran -------

def test_the_audit_list_is_paginated(client):
    """This was `.all()` -- every audit ever run, with every violation and
    every evidence blob, in one response. Four hundred audits already made it
    megabytes, and the dashboard calls it on every page load."""
    r = client.get("/audits?limit=3")
    assert r.status_code == 200
    assert len(r.json()) <= 3
    assert client.get("/audits?limit=0").status_code == 422
    assert client.get("/audits?limit=99999").status_code == 422


def test_retention_runs_while_the_server_is_up_not_only_at_boot():
    """purge_expired() was called once in the startup hook and nowhere else,
    so RETENTION_DAYS and MAX_STORED_AUDITS bounded nothing on a server that
    stays running -- the only kind of server a retention policy is for."""
    src = (Path(__file__).parent.parent / "app" / "main.py").read_text()
    assert "_retention_loop" in src
    assert "daemon=True" in src
    assert hasattr(config, "RETENTION_SWEEP_SECONDS")


# --- Frontend: every server failure must arrive as a usable sentence ------

def test_the_dashboard_names_a_rate_limit_instead_of_crashing_on_it():
    """authFetch special-cased only 401/403 and called r.json() on everything
    else. A 429 is valid JSON, so it sailed through as an object and the
    caller crashed doing .slice() on it -- the user saw "Could not load",
    which says nothing about waiting a minute."""
    js = (Path(__file__).parent.parent / "dashboard" / "dashboard.js").read_text()
    assert "r.status === 429" in js
    assert "Too many requests" in js
    assert "if (r.ok)" in js, "success and failure must be separated before parsing"


def test_the_demo_page_handles_a_rate_limit_and_a_lost_server():
    js = (Path(__file__).parent.parent / "demo" / "demo.js").read_text()
    assert "429" in js
    assert "pollResponse.ok" in js, "a failed poll must stop the loop, not parse an error page"


def test_the_connection_banner_is_readable_in_dark_mode():
    """It set its own colours with style.cssText -- hardcoded light-mode
    values. The one banner whose entire job is to be seen when the app cannot
    reach its API rendered as near-black on near-black."""
    js = (Path(__file__).parent.parent / "dashboard" / "dashboard.js").read_text()
    assert "style.cssText" not in js
    css = (Path(__file__).parent.parent / "dashboard" / "dashboard.css").read_text()
    assert ".conn-error" in css
