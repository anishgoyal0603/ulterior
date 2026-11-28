"""
Regression tests for the fifth code review (November 2026).
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


def _btn(text, role="neutral"):
    return ButtonInfo(text=text, role=role, bbox=None, font_size_px=14.0, bg_color="rgb(255,255,255)",
                      text_color="rgb(0,0,0)", contrast_ratio=21.0)


def _urgent(text):
    return [p for p in URGENCY_PATTERNS if re.search(p, text, re.IGNORECASE)]


# ------------------------------------------------------------ 2. DP-01

@pytest.mark.parametrize("text", ["Offer ends in 04h 22m", "Offer ends soon — 02h 15m non-stop",
                                  "Offer ends at 11:59 tonight"])
def test_minute_timers_durations_and_closing_times_are_not_fake_countdowns(text):
    from detectors.layer1_rules import detect_fake_urgency
    s = _state("p", text, urgency=_urgent(text))
    assert [v for v in detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (s, s)}))
            if v.evidence["claim_kind"] == "countdown"] == []


def test_a_frozen_timer_followed_by_tonight_is_still_a_countdown():
    from detectors.layer1_rules import detect_fake_urgency
    text = "Offer ends in 00:04:59 tonight"
    s = _state("p", text, urgency=_urgent(text))
    v = detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (s, s)}))
    assert v and v[0].evidence["claim_kind"] == "countdown"


# ------------------------------------------------------------ 4. DP-12

def test_an_unpriced_auto_renew_with_agree_is_saas_billing():
    from detectors.layer1_flow import detect_saas_billing
    cb = CheckboxState("p", "I agree to auto-renew my Pro membership monthly", True, True)
    assert len(detect_saas_billing(FunnelTrace("t", states=[_state("s", checkboxes=[cb])]))) == 1


# ------------------------------------------------------------ 7. DP-04 / DP-05

def test_sharing_with_delivery_partners_to_fulfil_the_order_is_lawful():
    from detectors.layer1_flow import detect_forced_action
    cb = CheckboxState("c", "I accept the Privacy Policy and agree you may share my details with delivery "
                            "partners to fulfil this order", False, False, required=True)
    assert detect_forced_action(FunnelTrace("t", states=[_state("c", checkboxes=[cb])])) == []
    ads = CheckboxState("a", "I agree to the Terms and to share my data with our partners for offers",
                        False, False, required=True)
    assert len(detect_forced_action(FunnelTrace("t", states=[_state("c", checkboxes=[ads])]))) == 1


def test_a_footer_subscribe_is_not_the_banners_paid_plan():
    from detectors.layer1_flow import _first_commitment_state
    text = "Subscribe to Prime Video ₹99/month\n" + "x " * 400 + "\nGet our newsletter\nSubscribe"
    assert _first_commitment_state(FunnelTrace("t", states=[_state("p", text, buttons=[_btn("Subscribe")])]))[0] is None
    plan = "Pro plan ₹499/month, renews monthly\nSubscribe"
    assert _first_commitment_state(FunnelTrace("t", states=[_state("p", plan, buttons=[_btn("Subscribe")])]))[0] is not None


# ------------------------------------------------------------ 8. coverage

def test_coverage_says_not_assessed_when_drip_pricing_could_not_run():
    from detectors.coverage import assess_coverage
    from detectors.layer1_rules import detect_drip_pricing
    listing = _state("l", "Shirt ₹299", price=299.0, line_items=[{"name": "Shirt", "price": 299.0}])
    product = _state("p", "Shirt ₹299 Add to cart", price=299.0)
    t = FunnelTrace("t", states=[listing, product])
    assert detect_drip_pricing(t) == []
    dp08 = next(c for c in assess_coverage(t, []) if c["code"] == "DP-08")
    assert dp08["status"] == "not_assessed"
    checkout = _state("c", "Shirt ₹299\nTotal ₹299", price=299.0, line_items=[{"name": "Shirt", "price": 299.0}])
    dp08 = next(c for c in assess_coverage(FunnelTrace("t", states=[product, checkout]), []) if c["code"] == "DP-08")
    assert dp08["status"] == "not_observed"


def test_coverage_needs_something_to_compare_a_decline_with():
    from detectors.coverage import assess_coverage
    t = FunnelTrace("t", states=[_state("p", buttons=[_btn("No thanks", "decline"), _btn("Skip", "decline")])])
    assert next(c for c in assess_coverage(t, []) if c["code"] == "DP-06")["status"] == "not_assessed"


# ------------------------------------------------------------ 9. DP-08

def test_a_product_called_packing_cubes_is_not_a_fee():
    from detectors.layer1_rules import detect_drip_pricing
    first = _state("p", "Packing Cubes ₹499. Delivery ₹40", price=499.0)
    last = _state("c", "Packing Cubes (Set of 4) ₹499\nDelivery ₹40\nTotal ₹539", price=539.0,
                  line_items=[{"name": "Packing Cubes (Set of 4)", "price": 499.0},
                              {"name": "Delivery", "price": 40.0}])
    assert detect_drip_pricing(FunnelTrace("t", states=[first, last])) == []


@pytest.mark.parametrize("name,fee", [("Delivery", True), ("Delivery fee", True), ("Platform charge", True),
                                      ("GST", True), ("Packing Cubes (Set of 4)", False),
                                      ("Service Kit (2026)", False)])
def test_fee_rows_are_recognised_by_their_shape(name, fee):
    from detectors.layer1_rules import _FEE_WORDS
    assert bool(_FEE_WORDS.search(name)) is fee


def test_boundary_decimals_are_split_from_one_rounding():
    from detectors.layer1_rules import _mentions_amount
    assert _mentions_amount("Total ₹1,299.50", 1299.5)
    assert _mentions_amount("Total ₹100.00", 99.999)


# ------------------------------------------------------------ 10. discovery

def test_a_refused_start_url_says_why(tmp_path):
    src = (ROOT / "capture" / "funnel_discovery.py").read_text(encoding="utf-8")
    block = src[src.index("Could not load the starting URL"):src.index("return trace", src.index("Could not load the starting URL"))]
    assert "browser.close()" not in block and "guard.blocked" in block


# ------------------------------------------------------------ 6. containers

def test_the_worker_has_no_web_healthcheck_and_proxy_flags_accept_every_spelling():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = compose[compose.index("  worker:"):]
    assert "healthcheck:\n      disable: true" in worker
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "1|true|yes)" in docker and "tr '[:upper:]' '[:lower:]'" in docker


# ------------------------------------------------------------ 15. dedupe

def test_offline_and_llm_findings_with_different_spacing_are_one(monkeypatch):
    from detectors import layer3_language as l3
    label = "Uncheck this box if you do not want to receive offers"
    s = _state("p", label, checkboxes=[CheckboxState("x", label, False, False)])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(l3, "_call_anthropic", lambda b, c: {"violations": [
        {"pattern_code": "DP-11", "quoted_text": "Uncheck  this box if you do not want to receive offers",
         "confidence": 0.7}]})
    found = [v for v in l3.detect_language_patterns(FunnelTrace("t", states=[s]), use_llm=True)
             if v.pattern_code == "DP-11"]
    assert len(found) == 1


# ------------------------------------------------------------ front ends (static)

def test_dashboard_demo_and_extension_fixes_are_in_place():
    dash = (ROOT / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    assert "JOBS[i] = Object.assign({}, JOBS[i], job)" in dash
    assert 'document.getElementById("review-note").value = "";' in dash
    assert "if (why && !/^rate limit/i.test(why)) throw new Error(why);" in dash
    demo = (ROOT / "demo" / "demo.js").read_text(encoding="utf-8")
    assert '$("target").disabled = on;' in demo
    bg = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    put = bg[bg.index("async function communityPut"):]
    assert put.index("communityInFlight.add(k)") < put.index("(await ")
    journey = (ROOT / "extension" / "src" / "journey.js").read_text(encoding="utf-8")
    assert "location.pathname + location.search + location.hash" in journey
    detect = (ROOT / "extension" / "src" / "detect.js").read_text(encoding="utf-8")
    assert "pricedCharge" in detect and "|for|" not in detect.split("NOT_BEFORE")[1][:200]
