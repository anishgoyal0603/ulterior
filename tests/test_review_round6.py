"""
Regression tests for the sixth code review (November 2026).
Each test reproduces a reported failure and fails if it returns.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.funnel_walker import FunnelTrace
from capture.state_extractor import ButtonInfo, CheckboxState, PageState, URGENCY_PATTERNS

ROOT = Path(__file__).parent.parent


def _state(step, text="", price=None, line_items=None, checkboxes=None, buttons=None, urgency=None):
    return PageState(url="http://shop.test/" + step, step_name=step, price=price, price_source="dom",
                     line_items=line_items or [], checkboxes=checkboxes or [], buttons=buttons or [],
                     full_text=text, urgency_phrases_found=urgency or [], raw_html="", price_currency="₹")


def _btn(text):
    return ButtonInfo(text=text, role="neutral", bbox=None, font_size_px=14.0, bg_color="rgb(255,255,255)",
                      text_color="rgb(0,0,0)", contrast_ratio=21.0)


def _urgent(text):
    return [p for p in URGENCY_PATTERNS if re.search(p, text, re.IGNORECASE)]


# ------------------------------------------------------------ 2. DP-08 "named" checks

@pytest.mark.parametrize("product_page", [
    "Phone Case ₹299. Cash on Delivery available.",
    "Phone Case ₹299. Delivery by 3 Dec",
    "Phone Case ₹299. Delivery: FREE",
    "Phone Case ₹299. Delivery ₹0",
    "Phone Case ₹299. Delivery included",
    "Phone Case ₹299. Express delivery in 2 days",
])
def test_delivery_mentioned_without_a_charge_does_not_disclose_one(product_page):
    from detectors.layer1_rules import detect_drip_pricing
    first = _state("p", product_page, price=299.0)
    last = _state("c", "Phone Case ₹299\nDelivery ₹49\nTotal ₹348", price=348.0,
                  line_items=[{"name": "Phone Case", "price": 299.0}, {"name": "Delivery", "price": 49.0}])
    assert [v.pattern_code for v in detect_drip_pricing(FunnelTrace("t", states=[first, last]))] == ["DP-08"]


def test_a_delivery_charge_stated_up_front_is_still_disclosed():
    from detectors.layer1_rules import detect_drip_pricing
    first = _state("p", "Phone Case ₹299 + Delivery ₹49", price=299.0)
    last = _state("c", "Phone Case ₹299\nDelivery ₹49\nTotal ₹348", price=348.0,
                  line_items=[{"name": "Phone Case", "price": 299.0}, {"name": "Delivery", "price": 49.0}])
    assert detect_drip_pricing(FunnelTrace("t", states=[first, last])) == []


# ------------------------------------------------------------ 4. DP-01 clocks

def test_an_honest_multi_day_timer_is_not_frozen():
    from detectors.layer1_rules import detect_fake_urgency
    t1, t2 = "Offer ends in 02:14:33:12", "Offer ends in 02:14:33:10"
    s1, s2 = _state("p", t1, urgency=_urgent(t1)), _state("p", t2, urgency=_urgent(t2))
    assert detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (s1, s2)})) == []
    frozen = _state("p", t1, urgency=_urgent(t1))
    v = detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (frozen, frozen)}))
    assert v and v[0].evidence["first_load_value"] == "02:14:33:12"


def test_opening_hours_are_not_a_countdown():
    from detectors.layer1_rules import detect_fake_urgency
    text = "Limited time: 10:00 - 18:00"
    s = _state("p", text, urgency=_urgent(text))
    assert detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (s, s)})) == []


def test_clock_to_seconds_reads_days():
    from detectors.layer1_rules import _clock_to_seconds
    assert _clock_to_seconds("02:14:33:12") == 2 * 86400 + 14 * 3600 + 33 * 60 + 12


# ------------------------------------------------------------ 5. stated total

@pytest.mark.parametrize("text", ["Order Total ₹457\nChoose how to pay\nWallet balance ₹50",
                                  "Order Total ₹457\nProceed to pay\nEMI from ₹105/month"])
def test_a_payment_heading_does_not_steal_the_next_rows_amount(text):
    from detectors.layer1_rules import _stated_total
    assert _stated_total(_state("c", text)) == 457.0


def test_a_split_total_still_reads_its_amount():
    from detectors.layer1_rules import _stated_total
    assert _stated_total(_state("c", "Amount payable\n₹457 (incl. GST)")) == 457.0


# ------------------------------------------------------------ 6. DP-02 free

def test_free_and_zero_priced_extras_are_not_basket_sneaking():
    from detectors.layer1_rules import detect_basket_sneaking_and_prechecked
    cbs = [CheckboxState("g", "Free Gift Wrap (₹0)", True, True),
           CheckboxState("w", "Complimentary 1-year Warranty (Free)", True, True),
           CheckboxState("p", "Gift wrap ₹49", True, True)]
    found = detect_basket_sneaking_and_prechecked(FunnelTrace("t", states=[_state("c", checkboxes=cbs)]))
    assert [v.evidence["checkbox_id"] for v in found] == ["p"]


# ------------------------------------------------------------ 8. DP-05 occurrences

def test_a_paid_plan_above_a_footer_newsletter_is_still_found():
    from detectors.layer1_flow import _first_commitment_state
    text = "Pro plan ₹499/month\nSubscribe\n" + "x " * 300 + "\nGet our newsletter\nSUBSCRIBE"
    state = _state("p", text, buttons=[_btn("Subscribe"), _btn("SUBSCRIBE")])
    assert _first_commitment_state(FunnelTrace("t", states=[state]))[0] is not None
    footer_only = _state("p", "Subscribe to Prime Video ₹99/month\n" + "x " * 400 + "\nGet our newsletter\nSubscribe",
                         buttons=[_btn("Subscribe")])
    assert _first_commitment_state(FunnelTrace("t", states=[footer_only]))[0] is None


# ------------------------------------------------------------ 13. layer 3 codes, discovery

def test_the_llm_cannot_report_patterns_outside_layer_3(monkeypatch):
    from detectors import layer3_language as l3
    s = _state("p", "Add insurance ₹199", buttons=[ButtonInfo("No thanks", "decline", None, 14.0, "", "", 21.0)])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(l3, "_call_anthropic", lambda b, c: {"violations": [
        {"pattern_code": "DP-02", "quoted_text": "Add insurance ₹199", "confidence": 0.9}]})
    assert [v for v in l3.detect_language_patterns(FunnelTrace("t", states=[s]), use_llm=True)
            if v.pattern_code == "DP-02"] == []


def test_discovery_records_blocked_requests_on_every_path():
    src = (ROOT / "capture" / "funnel_discovery.py").read_text(encoding="utf-8")
    assert src.count("trace.blocked_requests = list(guard.blocked)") == 2


# ------------------------------------------------------------ front ends (static)

def test_front_end_fixes_are_in_place():
    journey = (ROOT / "extension" / "src" / "journey.js").read_text(encoding="utf-8")
    assert "(last && last.isCheckout) || D.looksLikeCartOrCheckout()" in journey
    content = (ROOT / "extension" / "src" / "content.js").read_text(encoding="utf-8")
    assert "C.DIALOG_HINT_RE.test(" in content and "dismissal && el.contains(e.target)" in content
    detect = (ROOT / "extension" / "src" / "detect.js").read_text(encoding="utf-8")
    assert "function effectiveOpacity" in detect and "function rowQuantity" in detect
    assert '.replace(/,/g, "")' in detect
    dash = (ROOT / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    cancel = dash[dash.index("async function cancelRunningAudit"):]
    assert cancel.index("await authFetch") < cancel.index("stopRequested = true")
    popup = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
    assert "!(current.findings || []).length" in popup
