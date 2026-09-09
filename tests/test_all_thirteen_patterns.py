"""
Coverage tests for the six patterns added in the "all 13" build: DP-04, DP-05,
DP-10, DP-11, DP-12 and DP-13.

Every positive test drives a REAL Chromium browser over a real fixture — the
same standard as the existing e2e suite. Nothing here mocks a PageState for a
detector that is supposed to read rendered geometry or a `required` attribute,
because a mocked DOM would happily assert that a detector works while the
extractor that feeds it is broken.

The most important test in this file is `test_clean_control_*`. Positive tests
only prove a detector CAN fire. A detector that fires on everything would pass
all of them and be worthless. The clean-control fixture contains the near-miss
version of every pattern — required-but-necessary fields, an unticked optional
consent, an upfront recurring disclosure, a dismissible banner, an honestly
labelled executable — and asserts silence.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.funnel_walker import walk_funnel
from capture.state_extractor import modal_signature
from detectors.pipeline import audit
from detectors.taxonomy import (
    DARK_PATTERNS, BY_CODE, TIER_MAX_CONFIDENCE, clamp_confidence, coverage_summary,
)
from capture.adapters.demo_all_patterns import (
    FORCED_ACTION_DEMO, SUBSCRIPTION_TRAP_DEMO, SAAS_BILLING_DEMO,
    NAGGING_DEMO, ROGUE_ALERT_DEMO, CLEAN_CONTROL_DEMO,
)


def _codes(violations):
    return {v.pattern_code for v in violations}


def _of(violations, code):
    return [v for v in violations if v.pattern_code == code]


@pytest.fixture(scope="module")
def forced_action(tmp_path_factory):
    trace = walk_funnel(FORCED_ACTION_DEMO,
                        screenshot_dir=str(tmp_path_factory.mktemp("fa")))
    return trace, audit(trace)


@pytest.fixture(scope="module")
def subscription_trap(tmp_path_factory):
    trace = walk_funnel(SUBSCRIPTION_TRAP_DEMO,
                        screenshot_dir=str(tmp_path_factory.mktemp("st")))
    return trace, audit(trace)


@pytest.fixture(scope="module")
def saas_billing(tmp_path_factory):
    trace = walk_funnel(SAAS_BILLING_DEMO,
                        screenshot_dir=str(tmp_path_factory.mktemp("sb")))
    return trace, audit(trace)


@pytest.fixture(scope="module")
def nagging(tmp_path_factory):
    trace = walk_funnel(NAGGING_DEMO,
                        screenshot_dir=str(tmp_path_factory.mktemp("ng")))
    return trace, audit(trace)


@pytest.fixture(scope="module")
def rogue_alert(tmp_path_factory):
    trace = walk_funnel(ROGUE_ALERT_DEMO,
                        screenshot_dir=str(tmp_path_factory.mktemp("ra")))
    return trace, audit(trace)


@pytest.fixture(scope="module")
def clean_control(tmp_path_factory):
    trace = walk_funnel(CLEAN_CONTROL_DEMO,
                        screenshot_dir=str(tmp_path_factory.mktemp("cc")))
    return trace, audit(trace)


# --- DP-04 Forced Action -------------------------------------------------

def test_dp04_flags_mandatory_marketing_consent(forced_action):
    _, violations = forced_action
    found = _of(violations, "DP-04")
    assert found, "DP-04 did not fire on a required marketing/third-party consent"
    provable = [v for v in found if v.evidence.get("evidence_tier") == "provable"]
    assert provable, "the required-consent detection should be tiered provable"
    assert "marketing" in provable[0].evidence["label_text"].lower()
    assert provable[0].evidence["required_attribute_present"] is True


def test_dp04_does_not_flag_fields_genuinely_needed_to_transact(forced_action):
    """Address and card number are `required` on the same fixture. If the
    detector cannot tell a necessary requirement from an unrelated one, it
    would flag every checkout on the internet."""
    _, violations = forced_action
    flagged_labels = " ".join(
        str(v.evidence.get("label_text", "")).lower() for v in _of(violations, "DP-04")
    )
    assert "delivery address" not in flagged_labels
    assert "card number" not in flagged_labels
    assert "terms of service" not in flagged_labels


def test_dp04_flags_interstitial_with_no_dismissal_control(forced_action):
    _, violations = forced_action
    modal_findings = [
        v for v in _of(violations, "DP-04") if "modal_signature" in v.evidence
    ]
    assert modal_findings, "a full-screen wall with no close control should fire DP-04"
    assert modal_findings[0].evidence["dismiss_controls_found"] == []
    assert modal_findings[0].evidence["viewport_coverage"] > 0.10


# --- DP-05 Subscription Trap ---------------------------------------------

def test_dp05_flags_recurring_terms_disclosed_after_commitment(subscription_trap):
    _, violations = subscription_trap
    late = [v for v in _of(violations, "DP-05") if "disclosure_step" in v.evidence]
    assert late, "recurring terms appearing only after the commitment step should fire DP-05"
    assert late[0].evidence["offer_step"] == "offer"
    assert late[0].evidence["offer_step_disclosed_recurring"] is False
    assert late[0].evidence["disclosure_step"] == "payment"
    # The finding must quote the actual disclosure it found, not merely assert one.
    assert late[0].evidence["quoted_text"]


def test_dp05_flags_cancel_path_longer_than_signup_path(subscription_trap):
    _, violations = subscription_trap
    asym = [v for v in _of(violations, "DP-05") if "asymmetry_ratio" in v.evidence]
    assert asym, "a 4-step cancel path against a 1-step signup should fire DP-05"
    evidence = asym[0].evidence
    assert evidence["signup_step_count"] == 1
    assert evidence["cancel_step_count"] == 4
    assert evidence["asymmetry_ratio"] == 4.0


# --- DP-10 Nagging -------------------------------------------------------

def test_dp10_requires_a_dismissal_before_it_fires(nagging):
    """The crawler must actually have dismissed the popup, or a reappearance
    proves nothing. This asserts the dismissal happened AND was recorded."""
    trace, _ = nagging
    assert trace.dismissed_modals, "crawler did not dismiss any interruption"
    assert "home" in trace.dismissed_modals


def test_dp10_flags_popup_returning_after_dismissal(nagging):
    _, violations = nagging
    found = [v for v in _of(violations, "DP-10") if "reappeared_at_step" in v.evidence]
    assert found, "a popup dismissed on page 1 and shown again on page 2 is nagging"
    assert found[0].evidence["evidence_tier"] == "provable"
    assert found[0].evidence["dismissed_at_steps"]


def test_dp10_signature_survives_a_rotating_number():
    """The fixture changes the discount on every page (10% / 15% / 20%). If the
    signature keyed on raw text, each would look like a different popup and the
    detector would never fire — a one-character change would defeat it."""
    a = modal_signature("Get 10% off your first order", "", "")
    b = modal_signature("Get 20% off your first order", "", "")
    assert a == b


def test_dp10_does_not_flag_a_prompt_shown_once(clean_control):
    """A site is entitled to ask. Nagging begins at asking again."""
    _, violations = clean_control
    assert not _of(violations, "DP-10")


# --- DP-11 Trick Wording (offline, no API key) ---------------------------

def test_dp11_works_with_no_api_key_set(monkeypatch):
    """DP-11 previously had no offline path at all, so it silently detected
    nothing whenever a key was absent. This asserts the offline detector fires
    on its own."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from detectors.layer3_language import _offline_trick_wording
    from capture.state_extractor import PageState, CheckboxState

    state = PageState(
        url="x", step_name="checkout", price=None, price_source="not_found",
        line_items=[], buttons=[], full_text="", urgency_phrases_found=[], raw_html="",
        checkboxes=[CheckboxState(
            id="promo", checked=False, is_prechecked=False,
            label_text="Uncheck this box if you do not wish to receive promotional emails",
        )],
    )
    found = _offline_trick_wording(state)
    assert found, "double-negative label should be detected offline"
    assert found[0]["pattern_code"] == "DP-11"


def test_dp11_flags_prechecked_control_whose_label_means_do_not():
    """The tick and the wording point in opposite directions, so the user's
    real state is the opposite of what the control appears to say."""
    from detectors.layer3_language import _offline_trick_wording
    from capture.state_extractor import PageState, CheckboxState

    state = PageState(
        url="x", step_name="checkout", price=None, price_source="not_found",
        line_items=[], buttons=[], full_text="", urgency_phrases_found=[], raw_html="",
        checkboxes=[CheckboxState(
            id="optout", checked=True, is_prechecked=True,
            label_text="Do not send me order updates",
        )],
    )
    found = _offline_trick_wording(state)
    assert found and found[0]["pattern_code"] == "DP-11"
    assert found[0]["confidence"] >= 0.7


def test_dp11_ignores_ordinary_consent_wording():
    from detectors.layer3_language import _offline_trick_wording
    from capture.state_extractor import PageState, CheckboxState

    state = PageState(
        url="x", step_name="checkout", price=None, price_source="not_found",
        line_items=[], buttons=[], full_text="", urgency_phrases_found=[], raw_html="",
        checkboxes=[
            CheckboxState(id="a", checked=False, is_prechecked=False,
                          label_text="Email me order updates"),
            CheckboxState(id="b", checked=False, is_prechecked=False,
                          label_text="I accept the Terms of Service"),
        ],
    )
    assert _offline_trick_wording(state) == []


# --- DP-12 SaaS Billing --------------------------------------------------

def test_dp12_flags_preselected_recurring_plan(saas_billing):
    _, violations = saas_billing
    found = _of(violations, "DP-12")
    assert found, "a pre-selected auto-renewing plan should fire DP-12"
    evidence = found[0].evidence
    assert evidence["control_kind"] == "radio", "pre-selected paid tiers are radios in the wild"
    assert evidence["selected_on_first_load"] is True
    assert evidence["matched_billing_term"]


def test_dp12_does_not_also_report_the_same_control_as_dp02(saas_billing):
    """One pre-selected control must produce exactly one finding. Reporting it
    as both basket sneaking and SaaS billing would silently double the
    violation count of every audit that meets one."""
    _, violations = saas_billing
    assert not _of(violations, "DP-02"), (
        "a recurring pre-selected control was double-counted as DP-02"
    )


def test_dp12_does_not_flag_an_unselected_free_plan(saas_billing):
    _, violations = saas_billing
    labels = " ".join(str(v.evidence.get("label_text", "")).lower() for v in _of(violations, "DP-12"))
    assert "free" not in labels


# --- DP-13 Rogue Malware -------------------------------------------------

def test_dp13_flags_system_alert_framing_with_install_cta(rogue_alert):
    _, violations = rogue_alert
    found = [v for v in _of(violations, "DP-13") if "alert_framing_quote" in v.evidence]
    assert found, "OS/antivirus framing plus an install CTA should fire DP-13"
    assert found[0].evidence["install_cta_quote"]


def test_dp13_never_claims_a_file_is_malicious(rogue_alert):
    """DP-13 is the one category where an overconfident automated finding is
    genuinely defamatory. Every finding must disclaim binary analysis, stay in
    the indicative tier, and avoid asserting maliciousness in its prose."""
    _, violations = rogue_alert
    found = _of(violations, "DP-13")
    assert found
    for v in found:
        assert v.evidence["binary_analysis_performed"] is False
        assert v.evidence["evidence_tier"] == "indicative"
        assert v.confidence <= TIER_MAX_CONFIDENCE["indicative"]
        # The word "malicious" may appear, but only inside a DISCLAIMER. Any
        # sentence mentioning it must also carry a negation — banning the word
        # outright would perversely forbid the very sentence that protects the
        # project ("No claim is made that any software here is malicious").
        for sentence in v.explanation.lower().split("."):
            if "malicious" not in sentence:
                continue
            assert any(neg in sentence for neg in ("no claim", "not ", "never", "no file")), (
                f"DP-13 asserted maliciousness without a negation: {sentence.strip()!r}"
            )


def test_dp13_does_not_flag_an_honestly_labelled_download(rogue_alert):
    """'Download installer (.exe)' says exactly what it does. Flagging it would
    make every software vendor's download page a violation."""
    _, violations = rogue_alert
    anchors = [str(v.evidence.get("anchor_text", "")) for v in _of(violations, "DP-13")]
    assert "Download installer (.exe)" not in anchors
    assert "Watch in HD" in anchors, "the disguised .exe link should still be caught"


# --- The negative control ------------------------------------------------

def test_clean_control_produces_no_findings_from_the_new_detectors(clean_control):
    _, violations = clean_control
    new_codes = {"DP-04", "DP-05", "DP-10", "DP-12", "DP-13"}
    fired = _codes(violations) & new_codes
    assert not fired, (
        "detectors fired on the clean control fixture: "
        + "; ".join(f"{v.pattern_code}: {v.explanation}"
                    for v in violations if v.pattern_code in fired)
    )


# --- Tier discipline -----------------------------------------------------

def test_no_detector_can_exceed_its_evidence_tier(
    forced_action, subscription_trap, saas_billing, nagging, rogue_alert
):
    """The whole point of tiers is that a weak signal cannot be dressed up as a
    strong one. This walks every finding the new fixtures produce and asserts
    the cap held."""
    everything = []
    for fixture in (forced_action, subscription_trap, saas_billing, nagging, rogue_alert):
        everything.extend(fixture[1])
    assert everything
    for v in everything:
        tier = v.evidence.get("evidence_tier")
        if tier is None:
            continue
        assert v.confidence <= TIER_MAX_CONFIDENCE[tier], (
            f"{v.pattern_code} reported {v.confidence} above the {tier} cap"
        )


def test_clamp_confidence_refuses_to_inflate():
    assert clamp_confidence("indicative", 0.99) == 0.6
    assert clamp_confidence("corroborated", 1.0) == 0.9
    assert clamp_confidence("provable", 1.0) == 1.0


def test_every_ccpa_pattern_has_a_tier_and_a_coverage_note():
    """A pattern in the taxonomy with no stated scope is how overclaiming
    starts. Each of the 13 must say what it does and does not detect."""
    for pattern in DARK_PATTERNS:
        assert pattern.tier in TIER_MAX_CONFIDENCE, f"{pattern.code} has no valid tier"
        assert len(pattern.coverage_note) > 40, f"{pattern.code} has no meaningful coverage note"


def test_coverage_endpoint_data_matches_the_taxonomy():
    summary = coverage_summary()
    assert summary["total_patterns"] == 13
    assert summary["implemented"] == 13
    listed = {code for codes in summary["by_tier"].values() for code in codes}
    assert listed == set(BY_CODE)


def test_dp04_reports_each_control_exactly_once(forced_action):
    """A required checkbox is captured in `checkboxes` (with its selected-on-
    load state) and, before this was fixed, ALSO in `form_fields` — so the
    same marketing consent produced two identical DP-04 findings and doubled
    the count on its own. Same class of bug as the DP-02/DP-12 overlap; worth
    its own test because a duplicated finding is invisible unless counted.
    """
    _, violations = forced_action
    consent_findings = [
        v for v in _of(violations, "DP-04") if "label_text" in v.evidence
    ]
    labels = [v.evidence["label_text"] for v in consent_findings]
    assert len(labels) == len(set(labels)), f"duplicate DP-04 findings: {labels}"
