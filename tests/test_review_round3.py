"""
Regression tests for the third code review (November 2026).
Each test reproduces a reported failure and fails if it returns.
"""
import asyncio
import re
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from app import config, models
from app.db import SessionLocal, init_db
from app.main import app
from app.middleware import limiter
from capture.funnel_walker import FunnelTrace
from capture.state_extractor import ButtonInfo, CheckboxState, PageState, URGENCY_PATTERNS

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(config, "API_KEYS", [])
    return TestClient(app, raise_server_exceptions=False)


def _state(step, text="", price=None, line_items=None, checkboxes=None, buttons=None, urgency=None):
    return PageState(url="http://shop.test/" + step, step_name=step, price=price, price_source="dom",
                     line_items=line_items or [], checkboxes=checkboxes or [], buttons=buttons or [],
                     full_text=text, urgency_phrases_found=urgency or [], raw_html="", price_currency="₹")


def _btn(text, role="neutral", bbox=None, contrast=12.0, font=14.0):
    return ButtonInfo(text=text, role=role, bbox=bbox, font_size_px=font,
                      bg_color="rgb(255,255,255)", text_color="rgb(0,0,0)", contrast_ratio=contrast)


# ------------------------------------------------------------ 1. DP-08

def test_total_savings_below_the_total_is_not_the_total():
    from detectors.layer1_rules import _stated_total, detect_drip_pricing
    text = "Subtotal: ₹299\nPlatform Fee: ₹158\nTotal: ₹457\nTotal Savings: ₹150"
    assert _stated_total(_state("c", text)) == 457.0
    first = _state("p", "Item ₹299", price=299.0)
    last = _state("c", text, price=457.0, line_items=[{"name": "Item", "price": 299.0},
                                                     {"name": "Platform Fee", "price": 158.0}])
    found = detect_drip_pricing(FunnelTrace("s", states=[first, last]))
    assert [v.pattern_code for v in found] == ["DP-08"]


def test_a_payable_label_outranks_a_bare_total_and_promos_are_ignored():
    from detectors.layer1_rules import _stated_total
    assert _stated_total(_state("c", "Amount payable ₹457\nTotal items 3\nTotal Discount ₹130")) == 457.0
    assert _stated_total(_state("c", "Free delivery on total orders over ₹500\nTotal ₹348")) == 348.0


def test_the_renamed_product_line_is_not_an_undisclosed_charge():
    from detectors.layer1_rules import detect_drip_pricing
    first = _state("p", "Cotton Shirt — ₹299. Delivery ₹49", price=299.0)
    last = _state("c", "Cotton Shirt (Size M) — ₹299\nDelivery — ₹49\nTotal ₹348", price=348.0,
                  line_items=[{"name": "Cotton Shirt (Size M)", "price": 299.0},
                              {"name": "Delivery", "price": 49.0}])
    assert detect_drip_pricing(FunnelTrace("s", states=[first, last])) == []


# ------------------------------------------------------------ 2. DP-02

def test_benefits_and_required_radio_defaults_are_not_basket_sneaking():
    from detectors.layer1_rules import detect_basket_sneaking_and_prechecked
    cbs = [CheckboxState("w", "Use my ₹50 wallet balance", True, True, "checkbox"),
           CheckboxState("c", "Apply ₹100 cashback", True, True, "checkbox"),
           CheckboxState("s", "Standard Delivery (₹49)", True, True, "radio", group="ship"),
           CheckboxState("e", "Express Delivery (₹99)", False, False, "radio", group="ship"),
           CheckboxState("cod", "Cash on Delivery (₹0)", True, True, "checkbox"),
           CheckboxState("cr", "Store credit -₹50", True, True, "checkbox")]
    assert detect_basket_sneaking_and_prechecked(FunnelTrace("s", states=[_state("c", checkboxes=cbs)])) == []


def test_paid_add_ons_in_any_currency_form_are_caught():
    from detectors.layer1_rules import detect_basket_sneaking_and_prechecked
    cbs = [CheckboxState("g", "Gift wrap Rs. 49", True, True, "checkbox"),
           CheckboxState("i", "Yes, insure my trip ₹199", True, True, "radio", group="ins"),
           CheckboxState("n", "No, I'll take the risk", False, False, "radio", group="ins")]
    found = detect_basket_sneaking_and_prechecked(FunnelTrace("s", states=[_state("c", checkboxes=cbs)]))
    assert sorted(v.evidence["checkbox_id"] for v in found) == ["g", "i"]


# ------------------------------------------------------------ 3. DP-07

def test_one_of_several_fares_is_not_bait_and_switch():
    from detectors.layer1_rules import detect_bait_and_switch
    listing = _state("r", buttons=[_btn("Book ₹4,199"), _btn("Book ₹4,599"), _btn("Book ₹5,050")])
    review = _state("rev", price=4599.0)
    assert detect_bait_and_switch(FunnelTrace("s", states=[listing, review])) == []
    single = _state("r", buttons=[_btn("Book now ₹4,199")])
    assert len(detect_bait_and_switch(FunnelTrace("s", states=[single, review]))) == 1


# ------------------------------------------------------------ 4. DP-06

def test_a_readable_small_cancel_next_to_a_big_button_is_not_interference():
    from detectors.layer2_visual import detect_interface_interference
    big = _btn("Checkout", "accept", {"x": 0, "y": 0, "width": 300, "height": 60}, 12.0, 18)
    cancel = _btn("No thanks", "decline", {"x": 0, "y": 80, "width": 60, "height": 20}, 12.0, 12)
    assert detect_interface_interference(FunnelTrace("s", states=[_state("p", buttons=[big, cancel])])) == []


def test_interference_tiers_need_two_signals_and_a_nearby_pair():
    from detectors.layer2_visual import detect_interface_interference
    accept = _btn("Yes, add it", "accept", {"x": 0, "y": 0, "width": 300, "height": 60}, 12.0, 20)
    faint = _btn("No thanks", "decline", {"x": 0, "y": 80, "width": 60, "height": 20}, 1.8, 12)
    v = detect_interface_interference(FunnelTrace("s", states=[_state("p", buttons=[accept, faint])]))
    assert len(v) == 1 and v[0].evidence["evidence_tier"] == "corroborated"
    only_faint = _btn("No thanks", "decline", {"x": 0, "y": 80, "width": 280, "height": 60}, 2.5, 20)
    v = detect_interface_interference(FunnelTrace("s", states=[_state("p", buttons=[accept, only_faint])]))
    assert len(v) == 1 and v[0].evidence["evidence_tier"] == "indicative"
    # A banner "Close" far away is not paired with the checkout button, and
    # does not hide a real suppressed decline further down.
    banner = _btn("No thanks", "decline", {"x": 1000, "y": 2000, "width": 40, "height": 20}, 12.0, 12)
    v = detect_interface_interference(FunnelTrace("s", states=[_state("p", buttons=[banner, accept, faint])]))
    assert len(v) == 1 and v[0].evidence["decline_text"] == "No thanks"


# ------------------------------------------------------------ 5. proxy

def test_a_closed_https_tunnel_is_torn_down_promptly():
    """The browser closes its side; the server keeps the connection open (as
    CDNs do). The proxy must drop the upstream within a couple of seconds,
    not after the 120 s idle limit."""
    from capture.egress_proxy import EgressProxy
    closed = threading.Event()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def upstream():
        conn, _ = srv.accept()
        conn.settimeout(10)
        try:
            while conn.recv(1024):
                pass
        except OSError:
            pass
        closed.set()
        conn.close()
    threading.Thread(target=upstream, daemon=True).start()

    proxy = EgressProxy(resolver=lambda h, p: ["127.0.0.1"], ip_allowed=lambda ip: True)
    proxy.start()
    c = socket.create_connection(("127.0.0.1", proxy.port))
    c.sendall(f"CONNECT tunnel.test:{port} HTTP/1.1\r\nHost: tunnel.test:{port}\r\n\r\n".encode())
    assert b"200" in c.recv(1024)
    started = time.time()
    c.close()
    assert closed.wait(6), "the upstream connection was left open"
    assert time.time() - started < 6
    srv.close()


def test_a_connect_to_an_invalid_port_is_refused():
    from capture.egress_proxy import EgressProxy
    proxy = EgressProxy(resolver=lambda h, p: ["127.0.0.1"], ip_allowed=lambda ip: True)
    proxy.start()
    c = socket.create_connection(("127.0.0.1", proxy.port))
    c.sendall(b"CONNECT tunnel.test:0 HTTP/1.1\r\n\r\n")
    assert b"400" in c.recv(1024)
    c.close()


# ------------------------------------------------------------ 6. browser

def test_guards_cover_popups_and_service_workers():
    src = (ROOT / "capture" / "browser.py").read_text(encoding="utf-8")
    assert "--disable-quic" in src
    assert 'service_workers="block"' in src
    assert "context.add_init_script(NO_WEBRTC_SCRIPT)" in src


@pytest.mark.ui
def test_a_popup_opened_by_the_page_is_guarded(tmp_path):
    """window.open() makes a new page. Its requests must go through the same
    guard as the first page's."""
    from capture.browser import launch_browser, new_guarded_page, playwright_session
    page_file = tmp_path / "p.html"
    page_file.write_text("<html><body>hi</body></html>", encoding="utf-8")
    with playwright_session() as p:
        browser = launch_browser(p)
        try:
            page, guard = new_guarded_page(browser, allow_file=True)
            page.goto(page_file.as_uri())
            with page.expect_popup() as info:
                page.evaluate("window.open('http://169.254.169.254/latest/meta-data/')")
            popup = info.value
            popup.wait_for_timeout(500)
            assert any("169.254.169.254" in b for b in guard.blocked), guard.blocked
            assert popup.evaluate("typeof RTCPeerConnection") == "undefined"
        finally:
            browser.close()


# ------------------------------------------------------------ 7. SQLite

def test_sqlite_runs_with_wal_a_busy_timeout_and_foreign_keys():
    from app.db import engine
    if not str(engine.url).startswith("sqlite"):
        pytest.skip("SQLite only")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() >= 30000
        if ":memory:" not in str(engine.url):
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"


# ------------------------------------------------------------ 8. demo read-back

def test_a_team_audit_of_the_demo_store_is_not_public(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_DEMO_ENABLED", True)
    adapter = sorted(config.PUBLIC_DEMO_ADAPTERS)[0]
    with SessionLocal() as db:
        team = models.AuditJob(adapter_name=adapter, status="done", org="team")
        demo = models.AuditJob(adapter_name=adapter, status="done", org=config.DEMO_ORG)
        db.add_all([team, demo])
        db.commit()
        team_id, demo_id = team.id, demo.id
    assert client.get(f"/demo-audit/{team_id}").status_code == 404
    assert client.get(f"/demo-audit/{demo_id}").status_code == 200


# ------------------------------------------------------------ 12. DP-01 / DP-05 / DP-12

def _urgent(text):
    return [p for p in URGENCY_PATTERNS if re.search(p, text, re.IGNORECASE)]


def test_a_departure_time_next_to_hurry_is_not_a_countdown():
    from detectors.layer1_rules import detect_fake_urgency
    text = "Hurry! 07:15 DEL — Only 3 seats left"
    s = _state("r", text, urgency=_urgent(text))
    v = detect_fake_urgency(FunnelTrace("s", reload_pairs={"r": (s, s)}))
    assert all(x.evidence["claim_kind"] != "countdown" for x in v), [x.evidence for x in v]


def test_a_deal_ends_in_clock_is_seen_as_a_countdown():
    from detectors.layer1_rules import detect_fake_urgency
    text = "Deal ends in 00:04:59\nOnly 2 left in stock"
    s = _state("r", text, urgency=_urgent(text))
    v = detect_fake_urgency(FunnelTrace("s", reload_pairs={"r": (s, s)}))
    assert v and v[0].evidence["claim_kind"] == "countdown", [x.evidence for x in v]


def test_emi_lines_never_make_a_page_a_subscription():
    from detectors.layer1_flow import detect_subscription_trap
    page = _state("p", "Phone ₹12,999\nNo-cost EMI from ₹1,083/month", buttons=[_btn("Join now")])
    checkout = _state("c", "EMI ₹1,083 per month for 12 months")
    assert detect_subscription_trap(FunnelTrace("s", states=[page, checkout])) == []


def test_saas_billing_ignores_emi_free_tiers_and_cycle_choices():
    from detectors.layer1_flow import detect_saas_billing
    cbs = [CheckboxState("emi", "No-cost EMI ₹1,083/month", True, True, "radio", group="pay"),
           CheckboxState("upi", "UPI", False, False, "radio", group="pay"),
           CheckboxState("free", "Free plan ₹0/month", True, True, "checkbox"),
           CheckboxState("m", "Monthly ₹199/month", True, True, "radio", group="cycle"),
           CheckboxState("a", "Annual ₹1,999/year", False, False, "radio", group="cycle")]
    assert detect_saas_billing(FunnelTrace("s", states=[_state("c", checkboxes=cbs)])) == []
    plans = [CheckboxState("pro", "Pro — ₹999 billed monthly", True, True, "radio", group="plan"),
             CheckboxState("fr", "Free — ₹0, no card required", False, False, "radio", group="plan")]
    assert len(detect_saas_billing(FunnelTrace("s", states=[_state("c", checkboxes=plans)]))) == 1


# ------------------------------------------------------------ 13. layer 3

def test_a_lone_double_negative_is_indicative():
    from detectors.layer3_language import detect_language_patterns
    label = "Uncheck this box if you do not want to receive offers"
    cb = CheckboxState("x", label, False, False, "checkbox")
    s = _state("p", label, checkboxes=[cb])
    v = [x for x in detect_language_patterns(FunnelTrace("s", states=[s])) if x.pattern_code == "DP-11"]
    assert v and all(x.evidence["evidence_tier"] == "indicative" for x in v)


def test_model_json_with_braces_in_the_prose_still_parses():
    from detectors.layer3_language import _parse_model_json
    assert _parse_model_json('Note {see below}: {"violations": []} -- done {ok}') == {"violations": []}


def test_accepting_a_discount_is_not_confirmshaming():
    src = (ROOT / "detectors" / "layer3_language.py").read_text(encoding="utf-8")
    assert "d like \\\\d+% off" not in src and "'?d like" not in src


# ------------------------------------------------------------ 17. jobs

def test_site_name_is_the_host_only():
    from app.tasks import _build_adhoc_adapter
    from app import ssrf_guard
    orig = ssrf_guard.socket.getaddrinfo
    ssrf_guard.socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]
    try:
        assert _build_adhoc_adapter(["https://shop.example?token=secret"]).site_name == "shop.example"
    finally:
        ssrf_guard.socket.getaddrinfo = orig


def test_idempotency_key_is_unique_and_a_failed_audit_can_be_retried(monkeypatch):
    from app.tasks import submit_audit_job
    monkeypatch.setattr(config, "RUN_AUDIT_WORKERS", False)
    key = uuid.uuid4().hex
    org = "idem-" + uuid.uuid4().hex[:6]
    first = submit_audit_job(adapter_name="ecommerce_dark_demo", org=org, idempotency_key=key)
    assert submit_audit_job(adapter_name="ecommerce_dark_demo", org=org, idempotency_key=key) == first
    with SessionLocal() as db:
        db.get(models.AuditJob, first).status = "failed"
        db.commit()
    second = submit_audit_job(adapter_name="ecommerce_dark_demo", org=org, idempotency_key=key)
    assert second != first
    from sqlalchemy.exc import IntegrityError
    with SessionLocal() as db:
        db.add(models.AuditJob(adapter_name="x", status="pending", org=org, idempotency_key=key))
        with pytest.raises(IntegrityError):
            db.commit()


def test_one_key_cannot_belong_to_two_organisations():
    keys, orgs, bad = config._parse_keys("alpha:" + "k" * 30 + ",beta:" + "k" * 30)
    assert orgs["k" * 30] == "alpha" and bad


# ------------------------------------------------------------ 18. container

def test_the_container_does_not_run_as_root():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER\s+pwuser\s*$", docker, re.MULTILINE)


# ------------------------------------------------------------ extension + dashboard (static)

def test_extension_and_dashboard_fixes_are_in_place():
    bg = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "REPORTS_TO_LOCAL_SERVER &&" in bg
    content = (ROOT / "extension" / "src" / "content.js").read_text(encoding="utf-8")
    assert "function onRouteChange" in content and "state.timerFindings = [];" in content
    overlay = (ROOT / "extension" / "src" / "overlay.js").read_text(encoding="utf-8")
    assert "typed.key !== price.key" in overlay
    dash = (ROOT / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    assert "function clearJobPanel" in dash and "setInterval(" not in dash
    assert "setInterval(" not in (ROOT / "demo" / "demo.js").read_text(encoding="utf-8")
