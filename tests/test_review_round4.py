"""
Regression tests for the fourth code review (November 2026).
Each test reproduces a reported failure and fails if it returns.
"""
import re
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from app import config, models
from app.db import SessionLocal, init_db
from app.main import app
from app.middleware import limiter
from capture.funnel_walker import FunnelTrace
from capture.state_extractor import ButtonInfo, CheckboxState, DisclosureLabel, PageState, URGENCY_PATTERNS

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(config, "API_KEYS", [])
    return TestClient(app, raise_server_exceptions=False)


def _state(step, text="", price=None, line_items=None, checkboxes=None, buttons=None, urgency=None, labels=None):
    return PageState(url="http://shop.test/" + step, step_name=step, price=price, price_source="dom",
                     line_items=line_items or [], checkboxes=checkboxes or [], buttons=buttons or [],
                     full_text=text, urgency_phrases_found=urgency or [], raw_html="", price_currency="₹",
                     disclosure_labels=labels or [])


def _btn(text, role="neutral", bbox=None, contrast=12.0, font=14.0):
    return ButtonInfo(text=text, role=role, bbox=bbox, font_size_px=font,
                      bg_color="rgb(255,255,255)", text_color="rgb(0,0,0)", contrast_ratio=contrast)


def _urgent(text):
    return [p for p in URGENCY_PATTERNS if re.search(p, text, re.IGNORECASE)]


# ------------------------------------------------------------ 4. DP-12

def test_free_email_preferences_are_not_saas_billing():
    from detectors.layer1_flow import detect_saas_billing
    cbs = [CheckboxState("n", "Subscribe to our monthly newsletter", True, True),
           CheckboxState("o", "Send me monthly order updates", True, True),
           CheckboxState("a", "Auto-renew my Pro membership every month", True, True)]
    found = detect_saas_billing(FunnelTrace("t", states=[_state("s", checkboxes=cbs)]))
    assert [v.evidence["control_id"] for v in found] == ["a"]


# ------------------------------------------------------------ 5. DP-02

def test_a_dash_before_the_price_is_punctuation_and_paid_extras_beat_benefit_words():
    from detectors.layer1_rules import detect_basket_sneaking_and_prechecked
    cbs = [CheckboxState("bag", "Carry Bag - ₹15", True, True),
           CheckboxState("war", "Extended Warranty ₹299 (T&C apply)", True, True),
           CheckboxState("ins", "I agree to add Travel Insurance ₹199", True, True),
           CheckboxState("cr", "Store credit -₹50", True, True),
           CheckboxState("cr2", "- ₹50 instant credit", True, True),
           CheckboxState("wal", "Use my ₹50 wallet balance", True, True)]
    found = detect_basket_sneaking_and_prechecked(FunnelTrace("t", states=[_state("c", checkboxes=cbs)]))
    assert sorted(v.evidence["checkbox_id"] for v in found) == ["bag", "ins", "war"]


# ------------------------------------------------------------ 6. DP-01

@pytest.mark.parametrize("text", ["Offer ends at 11:59 PM tonight", "Offer ends 23:59 IST", "Offer ends by 18:00"])
def test_a_fixed_closing_time_is_not_a_countdown(text):
    from detectors.layer1_rules import detect_fake_urgency
    s = _state("p", text, urgency=_urgent(text))
    assert detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (s, s)})) == []


@pytest.mark.parametrize("text,value", [
    ("Limited time offer!\nFree returns\nDeal ends in 04h 22m 18s", "4:22:18"),
    ("Offer ends in 08m 12s", "8:12"),
])
def test_unit_clocks_and_later_matches_are_read(text, value):
    from detectors.layer1_rules import detect_fake_urgency
    s = _state("p", text, urgency=_urgent(text))
    v = detect_fake_urgency(FunnelTrace("t", reload_pairs={"p": (s, s)}))
    assert v and v[0].evidence["claim_kind"] == "countdown" and v[0].evidence["first_load_value"] == value


# ------------------------------------------------------------ 7. DP-08

def test_a_fee_at_the_product_price_is_still_a_fee():
    from detectors.layer1_rules import detect_drip_pricing
    first = _state("p", "Milk ₹49", price=49.0)
    last = _state("c", "Milk ₹49\nDelivery Fee ₹49\nTotal ₹98", price=98.0,
                  line_items=[{"name": "Milk", "price": 49.0}, {"name": "Delivery Fee", "price": 49.0}])
    assert [v.pattern_code for v in detect_drip_pricing(FunnelTrace("t", states=[first, last]))] == ["DP-08"]


def test_total_labels_with_notes_and_savings_below_are_read_correctly():
    from detectors.layer1_rules import _stated_total
    assert _stated_total(_state("c", "Subtotal ₹299\nOrder Total (₹100 off): ₹457")) == 457.0
    assert _stated_total(_state("c", "Subtotal ₹299\nTotal\nYou saved ₹150")) == 299.0


def test_decimal_totals_are_recognised_as_disclosed():
    from detectors.layer1_rules import _mentions_amount
    assert _mentions_amount("Total at checkout: ₹1,299.50", 1299.5)
    assert _mentions_amount("₹1,23,456.50", 123456.5)
    assert not _mentions_amount("₹1,300.00", 1299.5)


# ------------------------------------------------------------ 13. DP-07

def test_an_add_on_button_price_is_not_a_bait():
    from detectors.layer1_rules import detect_bait_and_switch
    product = _state("p", price=299.0, buttons=[_btn("Add to Cart"), _btn("Add 2-year warranty ₹49")])
    cart = _state("c", price=299.0)
    assert detect_bait_and_switch(FunnelTrace("t", states=[product, cart])) == []


# ------------------------------------------------------------ 14. reference pricing

@pytest.mark.parametrize("text", ["M.R.P. ₹2,499 Now ₹499", "M.R.P.: ₹2,499 Now ₹499"])
def test_indian_mrp_format_is_read(text):
    from detectors.layer1_rules import detect_reference_pricing
    assert detect_reference_pricing(FunnelTrace("t", states=[_state("p", text, price=499.0)]))


# ------------------------------------------------------------ 15. layer 3

@pytest.mark.parametrize("text,shaming", [
    ("No, I don't need warranty", False),
    ("No, continue without protection", False),
    ("No thanks, I don't like discounts", True),
    ("No thanks, I don't want to save money", True),
])
def test_neutral_declines_are_not_confirmshaming(text, shaming):
    from detectors.layer3_language import _offline_confirm_shaming
    assert bool(_offline_confirm_shaming([text])) is shaming


def test_grounding_accepts_control_labels_and_normalised_text(monkeypatch):
    from detectors import layer3_language as l3
    cb = CheckboxState("x", "Uncheck this box if you do not   want offers", False, False)
    s = _state("p", "Page text without that label", checkboxes=[cb])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(l3, "_call_anthropic", lambda b, c: {"violations": [
        {"pattern_code": "DP-11", "quoted_text": "Uncheck this box if you do not want offers", "confidence": 0.7}]})
    found = l3.detect_language_patterns(FunnelTrace("t", states=[s]), use_llm=True)
    assert any(v.pattern_code == "DP-11" for v in found)


def test_the_llm_is_given_choice_labels_and_a_timeout():
    src = (ROOT / "detectors" / "layer3_language.py").read_text(encoding="utf-8")
    assert "timeout=30.0" in src and "_call_anthropic(decline_texts, choice_labels)" in src


# ------------------------------------------------------------ 16. DP-05 / DP-04

def test_a_cancel_flow_with_one_confirmation_is_not_a_trap():
    from detectors.layer1_flow import detect_subscription_trap
    t = FunnelTrace("t", states=[_state("offer", "Pro ₹499/month, renews monthly. Cancel anytime in Account.",
                                        buttons=[_btn("Subscribe")])],
                    step_roles={"offer": "signup", "account": "cancel", "confirm": "cancel"})
    assert not [v for v in detect_subscription_trap(t) if "asymmetry_ratio" in v.evidence]


def test_premium_products_and_footer_newsletters_are_not_plans():
    from detectors.layer1_flow import _first_commitment_state
    shop = _state("p", "Premium Phone Case ₹499\nProtection Plan available\nJoin now for updates",
                  buttons=[_btn("Join now")])
    assert _first_commitment_state(FunnelTrace("t", states=[shop]))[0] is None
    footer = _state("p", "Prime Video ₹99/month banner\n" + "x " * 400 + "\nGet our newsletter\nSubscribe",
                    buttons=[_btn("Subscribe")])
    assert _first_commitment_state(FunnelTrace("t", states=[footer]))[0] is None


def test_terms_bundled_with_selling_data_to_advertisers_is_forced_action():
    from detectors.layer1_flow import detect_forced_action
    cb = CheckboxState("t", "I agree to the Terms of Use and to share my data with third-party advertisers",
                       False, False, required=True)
    assert [v.pattern_code for v in detect_forced_action(FunnelTrace("t", states=[_state("s", checkboxes=[cb])]))] == ["DP-04"]


# ------------------------------------------------------------ 17. integrity

def test_digest_is_stable_under_row_order_and_comparison_counts():
    from app.integrity import compare_findings, findings_digest
    a = {"pattern_code": "DP-02", "step_name": "cart", "explanation": "x", "evidence": {"id": 1}}
    b = {"pattern_code": "DP-02", "step_name": "cart", "explanation": "x", "evidence": {"id": 2}}
    assert findings_digest([a, b]) == findings_digest([b, a])
    r = compare_findings([a, b], [a])
    assert len(r["resolved"]) == 1 and len(r["still_present"]) == 1 and r["new"] == []
    r = compare_findings([a], [a, b])
    assert len(r["new"]) == 1


# ------------------------------------------------------------ 18. retention order

def test_evidence_is_deleted_only_after_the_commit(monkeypatch, tmp_path):
    from app import retention
    from datetime import datetime, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=90)).replace(tzinfo=None)
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="x", status="done", org="retain-" + uuid.uuid4().hex[:6],
                              created_at=old)
        db.add(job)
        db.commit()
        jid = job.id
    folder = Path(retention.job_evidence_dir(jid))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "shot.png").write_bytes(b"png")
    # The job is past the age limit, so this purge would delete it.
    monkeypatch.setattr(config, "RETENTION_DAYS", 30)
    monkeypatch.setattr(config, "MAX_STORED_DEMO_AUDITS", 0)
    monkeypatch.setattr(config, "MAX_STORED_AUDITS", 0)

    from sqlalchemy.orm import Session
    real_commit = Session.commit

    def failing_commit(self):
        raise RuntimeError("database went away")
    monkeypatch.setattr(Session, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        retention.purge_expired()
    monkeypatch.setattr(Session, "commit", real_commit)
    assert (folder / "shot.png").exists(), "evidence was deleted although nothing was committed"
    # ...and once the commit does succeed, it goes.
    retention.purge_expired()
    assert not folder.exists()


# ------------------------------------------------------------ 19. proxy headers

def test_proxy_headers_follow_trust_proxy_headers():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '--forwarded-allow-ips="*"' not in docker
    assert "1|true|yes) export FORWARDED_ALLOW_IPS='*'; PROXY=--proxy-headers" in docker
    assert "--no-proxy-headers" in docker
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "pg_isready" in compose and "condition: service_healthy" in compose
    assert "/data/evidence && chown -R pwuser:pwuser" in docker


# ------------------------------------------------------------ 20. card redaction

def test_timestamps_and_prices_are_not_card_numbers():
    from app.privacy import redact
    assert redact("Captured at 2026-11-28 15-08-30") == "Captured at 2026-11-28 15-08-30"
    assert "[CARD_REDACTED]" in redact("Card 4111 1111 1111 1111")
    assert "[CARD_REDACTED]" in redact("Amex 3782 822463 10005")


# ------------------------------------------------------------ 21. browser close

def test_walkers_close_the_browser_on_every_path():
    for name in ("funnel_walker.py", "funnel_discovery.py", "inspect_site.py"):
        src = (ROOT / "capture" / name).read_text(encoding="utf-8")
        assert "finally:\n            browser.close()" in src, name


# ------------------------------------------------------------ 22. DP-09 / DP-06

def test_a_readable_grey_sponsored_tag_is_not_hidden():
    from detectors.layer2_visual import detect_disguised_ad_disclosure
    ok = DisclosureLabel(text="Sponsored", font_size_px=12.0, contrast_ratio=4.48, bbox=None)
    faint = DisclosureLabel(text="Sponsored", font_size_px=9.5, contrast_ratio=2.1, bbox=None)
    assert detect_disguised_ad_disclosure(FunnelTrace("t", states=[_state("p", labels=[ok])])) == []
    v = detect_disguised_ad_disclosure(FunnelTrace("t", states=[_state("p", labels=[faint])]))
    assert v and v[0].evidence["evidence_tier"] == "corroborated"


def test_a_carousel_skip_is_not_interference():
    from detectors.layer2_visual import detect_interface_interference
    nxt = _btn("Next", "accept", {"x": 0, "y": 0, "width": 300, "height": 60}, 12.0, 20)
    skip = _btn("Skip", "decline", {"x": 0, "y": 80, "width": 40, "height": 20}, 1.8, 11)
    page = _state("p", "Welcome tour: swipe to see what is new", buttons=[nxt, skip])
    assert detect_interface_interference(FunnelTrace("t", states=[page])) == []


# ------------------------------------------------------------ 23. community drops

def test_one_stray_report_does_not_hide_a_real_drop(client):
    from app.community import today
    PO = models.PriceObservation
    key = f"myntra.com:{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(PO(product_key=key, site="myntra.com", price=5000, observed_on=today() - timedelta(days=6)))
        for _ in range(5):
            db.add(PO(product_key=key, site="myntra.com", price=1000, observed_on=today() - timedelta(days=5)))
            db.add(PO(product_key=key, site="myntra.com", price=800, observed_on=today()))
        db.commit()
    import app.main as main
    main._drops_cache.clear()
    drops = {d["key"]: d for d in client.post("/prices/drops", json={"days": 7, "limit": 50}).json()["drops"]}
    assert drops[key]["pct"] == 20.0


# ------------------------------------------------------------ extension + dashboard (static)

def test_extension_and_dashboard_fixes_are_in_place():
    content = (ROOT / "extension" / "src" / "content.js").read_text(encoding="utf-8")
    assert "price.key, price.current" in content and "productTitle, findings" in content
    assert "location.pathname + location.search + location.hash" in content
    assert "timerGeneration" in content
    detect = (ROOT / "extension" / "src" / "detect.js").read_text(encoding="utf-8")
    assert "THRESHOLD_CLAUSE_RE" in detect and "CLOSE_CONTROL_RE" in detect and "previousElementSibling" in detect
    bg = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "communityInFlight" in bg
    dash = (ROOT / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    assert 'document.getElementById("reports-card").hidden = true;' in dash
    demo = (ROOT / "demo" / "demo.js").read_text(encoding="utf-8")
    assert "return;              // finished" in demo
