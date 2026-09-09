"""
End-to-end tests: real Playwright browser, real local HTML fixtures, real
detection pipeline. No mocking of the browser or the DOM -- if these pass,
the engine genuinely works, not just in theory.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.funnel_walker import walk_funnel
from capture.adapters.demo_ecommerce import ECOMMERCE_DARK_DEMO
from capture.adapters.demo_ecommerce_clean import ECOMMERCE_CLEAN_DEMO
from capture.adapters.demo_flight import FLIGHT_DARK_DEMO
from detectors.pipeline import audit


@pytest.fixture(scope="module")
def dark_ecommerce_violations():
    trace = walk_funnel(ECOMMERCE_DARK_DEMO, reload_delay_seconds=1.2)
    return audit(trace)


@pytest.fixture(scope="module")
def clean_ecommerce_violations():
    trace = walk_funnel(ECOMMERCE_CLEAN_DEMO)
    return audit(trace)


@pytest.fixture(scope="module")
def dark_flight_violations():
    trace = walk_funnel(FLIGHT_DARK_DEMO, reload_delay_seconds=1.2)
    return audit(trace)


def test_clean_site_has_zero_violations(clean_ecommerce_violations):
    assert clean_ecommerce_violations == []


def test_dark_ecommerce_catches_drip_pricing(dark_ecommerce_violations):
    codes = [v.pattern_code for v in dark_ecommerce_violations]
    assert "DP-08" in codes
    drip = next(v for v in dark_ecommerce_violations if v.pattern_code == "DP-08")
    assert drip.evidence["hidden_amount"] == 158.0
    assert drip.confidence == 1.0


def test_dark_ecommerce_catches_prechecked_basket_sneaking(dark_ecommerce_violations):
    codes = [v.pattern_code for v in dark_ecommerce_violations]
    assert "DP-02" in codes


def test_dark_ecommerce_catches_fake_urgency(dark_ecommerce_violations):
    codes = [v.pattern_code for v in dark_ecommerce_violations]
    assert "DP-01" in codes


def test_dark_ecommerce_catches_interface_interference(dark_ecommerce_violations):
    codes = [v.pattern_code for v in dark_ecommerce_violations]
    assert "DP-06" in codes
    dp06 = next(v for v in dark_ecommerce_violations if v.pattern_code == "DP-06")
    assert dp06.evidence["decline_contrast"] < 3.0


def test_dark_ecommerce_catches_confirm_shaming(dark_ecommerce_violations):
    codes = [v.pattern_code for v in dark_ecommerce_violations]
    assert "DP-03" in codes


def test_flight_funnel_reuses_same_engine_no_adapter_changes(dark_flight_violations):
    """The point of this test: a completely different funnel shape (search
    -> seats -> addons -> payment, vs listing -> product -> cart -> checkout)
    is caught by the SAME detection code, proving the adapter pattern works."""
    codes = [v.pattern_code for v in dark_flight_violations]
    assert "DP-08" in codes
    assert "DP-02" in codes
    assert "DP-01" in codes
    assert "DP-06" in codes


def test_violations_sorted_by_confidence(dark_ecommerce_violations):
    confidences = [v.confidence for v in dark_ecommerce_violations]
    assert confidences == sorted(confidences, reverse=True)


def test_every_violation_has_evidence(dark_ecommerce_violations, dark_flight_violations):
    for v in dark_ecommerce_violations + dark_flight_violations:
        assert v.evidence, f"{v.pattern_code} has no evidence attached"


# --- Beyond-CCPA-13 detectors, added from DPAF (arXiv:2412.09147) cross-reference ---

def test_reference_pricing_flags_implausible_discount():
    from capture.state_extractor import PageState
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_reference_pricing

    state = PageState(
        url="x", step_name="product", price=199.0, price_source="data_attribute",
        line_items=[], checkboxes=[], buttons=[],
        full_text="Was ₹4999 Now only ₹199!", urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [state]
    violations = detect_reference_pricing(trace)
    assert len(violations) == 1
    assert violations[0].evidence["claimed_discount_pct"] > 90


def test_reference_pricing_does_not_flag_normal_discount():
    from capture.state_extractor import PageState
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_reference_pricing

    state = PageState(
        url="x", step_name="product", price=799.0, price_source="data_attribute",
        line_items=[], checkboxes=[], buttons=[],
        full_text="Was ₹999 Now ₹799", urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [state]
    violations = detect_reference_pricing(trace)
    assert violations == [], "20% discount is plausible, should not be flagged"


def test_immortal_account_flags_missing_delete_option():
    from capture.state_extractor import PageState
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_immortal_account

    state = PageState(
        url="x", step_name="settings", price=None, price_source="not_found",
        line_items=[], checkboxes=[], buttons=[],
        full_text="My Account Settings: change email, change password, notification preferences",
        urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [state]
    violations = detect_immortal_account(trace)
    assert len(violations) == 1


def test_immortal_account_does_not_flag_when_delete_present():
    from capture.state_extractor import PageState
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_immortal_account

    state = PageState(
        url="x", step_name="settings", price=None, price_source="not_found",
        line_items=[], checkboxes=[], buttons=[],
        full_text="My Account Settings: change email. Delete Account.",
        urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [state]
    violations = detect_immortal_account(trace)
    assert violations == []


def test_immortal_account_does_not_flag_unrelated_pages():
    from capture.state_extractor import PageState
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_immortal_account

    state = PageState(
        url="x", step_name="product", price=299.0, price_source="data_attribute",
        line_items=[], checkboxes=[], buttons=[],
        full_text="Premium Phone Case - great quality, buy now.",
        urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [state]
    violations = detect_immortal_account(trace)
    assert violations == [], "must not fire on pages that are not account/settings pages"


def test_bait_and_switch_flags_specific_price_mismatch():
    from capture.state_extractor import PageState, ButtonInfo
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_bait_and_switch

    listing = PageState(
        url="x", step_name="listing", price=None, price_source="not_found",
        line_items=[], checkboxes=[],
        buttons=[ButtonInfo(text="Book IndiGo 6E-123 for ₹4999", role="neutral", bbox=None,
                             font_size_px=14, bg_color="rgb(0,0,0)", text_color="rgb(255,255,255)",
                             contrast_ratio=21.0)],
        full_text="", urgency_phrases_found=[], raw_html="",
    )
    booking = PageState(
        url="x", step_name="booking", price=6499.0, price_source="data_attribute",
        line_items=[], checkboxes=[], buttons=[],
        full_text="", urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [listing, booking]
    violations = detect_bait_and_switch(trace)
    assert len(violations) == 1
    assert violations[0].evidence["claimed_price"] == 4999.0
    assert violations[0].evidence["actual_price_on_next_page"] == 6499.0


def test_bait_and_switch_does_not_flag_matching_price():
    from capture.state_extractor import PageState, ButtonInfo
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_bait_and_switch

    listing = PageState(
        url="x", step_name="listing", price=None, price_source="not_found",
        line_items=[], checkboxes=[],
        buttons=[ButtonInfo(text="Book IndiGo 6E-123 for ₹4999", role="neutral", bbox=None,
                             font_size_px=14, bg_color="rgb(0,0,0)", text_color="rgb(255,255,255)",
                             contrast_ratio=21.0)],
        full_text="", urgency_phrases_found=[], raw_html="",
    )
    booking = PageState(
        url="x", step_name="booking", price=4999.0, price_source="data_attribute",
        line_items=[], checkboxes=[], buttons=[],
        full_text="", urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [listing, booking]
    violations = detect_bait_and_switch(trace)
    assert violations == [], "price delivered as promised -- must not flag"


def test_bait_and_switch_ignores_buttons_with_no_specific_price_claim():
    from capture.state_extractor import PageState, ButtonInfo
    from capture.funnel_walker import FunnelTrace
    from detectors.layer1_rules import detect_bait_and_switch

    listing = PageState(
        url="x", step_name="listing", price=None, price_source="not_found",
        line_items=[], checkboxes=[],
        buttons=[ButtonInfo(text="Continue", role="neutral", bbox=None,
                             font_size_px=14, bg_color="rgb(0,0,0)", text_color="rgb(255,255,255)",
                             contrast_ratio=21.0)],
        full_text="", urgency_phrases_found=[], raw_html="",
    )
    booking = PageState(
        url="x", step_name="booking", price=6499.0, price_source="data_attribute",
        line_items=[], checkboxes=[], buttons=[],
        full_text="", urgency_phrases_found=[], raw_html="",
    )
    trace = FunnelTrace(site_name="t"); trace.states = [listing, booking]
    violations = detect_bait_and_switch(trace)
    assert violations == [], "generic 'Continue' makes no specific claim to violate"


def test_regulatory_case_index_maps_to_taxonomy():
    import sys
    sys.path.insert(0, "data/real_world_validation")
    from india_regulatory_cases import find_cases_for_pattern, REGULATORY_CASES
    assert len(REGULATORY_CASES) == 4
    dp07_cases = find_cases_for_pattern("DP-07")
    assert len(dp07_cases) >= 1
    assert "MakeMyTrip" in dp07_cases[0]["title"] or "Goibibo" in dp07_cases[0]["title"]


# --- SPA support (click-driven steps, no URL navigation) ---

def test_spa_click_driven_step_captures_drawer_state():
    """Validates the click_selector capability against a real fixture,
    since the real Saleor demo isn't reachable from this sandbox."""
    from capture.adapters.spa_demo import SPA_DEMO
    trace = walk_funnel(SPA_DEMO)
    assert len(trace.states) == 2
    assert trace.states[0].step_name == "listing"
    assert trace.states[1].step_name == "cart_drawer"
    # The drawer's line items are only meaningfully present once "opened" --
    # confirm the click actually ran, not just a no-op wait.
    assert any(li["name"] == "Extended Warranty" for li in trace.states[1].line_items)


def test_spa_trace_detects_drip_pricing_and_basket_sneaking():
    from capture.adapters.spa_demo import SPA_DEMO
    trace = walk_funnel(SPA_DEMO)
    violations = audit(trace)
    codes = [v.pattern_code for v in violations]
    assert "DP-08" in codes
    assert "DP-02" in codes


def test_spa_trace_does_not_duplicate_the_same_checkbox_across_steps():
    """Regression test for the real bug this SPA testing surfaced: the
    cart drawer's checkbox persists in the DOM across both captured states
    (no navigation resets it), and was originally flagged twice for the
    same physical element. Must be reported exactly once."""
    from capture.adapters.spa_demo import SPA_DEMO
    trace = walk_funnel(SPA_DEMO)
    violations = audit(trace)
    basket_sneaking = [v for v in violations if v.pattern_code == "DP-02"]
    assert len(basket_sneaking) == 1, (
        f"expected exactly 1 basket-sneaking violation, got {len(basket_sneaking)} "
        f"-- the same checkbox is being double-counted across SPA steps"
    )


# --- DP-09 Disguised Ad (v1: disclosure-label contrast/size, no CV needed) ---

def test_disguised_ad_flags_tiny_low_contrast_disclosure():
    import os
    from playwright.sync_api import sync_playwright
    from capture.state_extractor import extract_page_state
    from capture.funnel_walker import FunnelTrace
    from detectors.pipeline import audit

    fixture = os.path.join(os.path.dirname(__file__), "..", "fixtures", "disguised_ad", "index.html")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(f"file://{os.path.abspath(fixture)}")
        pg.wait_for_timeout(200)
        state = extract_page_state(pg, "test")
        b.close()

    trace = FunnelTrace(site_name="t")
    trace.states = [state]
    violations = audit(trace)
    dp09 = [v for v in violations if v.pattern_code == "DP-09"]
    assert len(dp09) == 1
    assert dp09[0].evidence["disclosure_text"] == "Sponsored"


def test_disguised_ad_does_not_flag_clearly_disclosed_ad():
    """False-positive guard: a normal-sized, high-contrast 'Advertisement'
    label must not be flagged -- only a practically unreadable one."""
    import os
    from playwright.sync_api import sync_playwright
    from capture.state_extractor import extract_page_state
    from capture.funnel_walker import FunnelTrace
    from detectors.pipeline import audit

    fixture = os.path.join(os.path.dirname(__file__), "..", "fixtures", "disguised_ad", "index.html")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(f"file://{os.path.abspath(fixture)}")
        pg.wait_for_timeout(200)
        state = extract_page_state(pg, "test")
        b.close()

    trace = FunnelTrace(site_name="t")
    trace.states = [state]
    violations = audit(trace)
    flagged_texts = [v.evidence["disclosure_text"] for v in violations if v.pattern_code == "DP-09"]
    assert "Advertisement" not in flagged_texts
