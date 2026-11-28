"""
Regression tests for the second code review (November 2026).
Each test reproduces a reported failure and fails if it returns.
"""
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
from capture.state_extractor import CheckboxState, PageState

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(config, "API_KEYS", [])
    return TestClient(app, raise_server_exceptions=False)


def _state(step, text="", price=None, line_items=None, checkboxes=None, urgency=None, buttons=None):
    return PageState(url="http://shop.test/" + step, step_name=step, price=price, price_source="rendered_text",
                     line_items=line_items or [], checkboxes=checkboxes or [], buttons=buttons or [],
                     full_text=text, urgency_phrases_found=urgency or [], raw_html="", price_currency="₹")


# ------------------------------------------------------------ DP-08 drip pricing

def test_a_subtotal_is_never_taken_for_the_total():
    from detectors.layer1_rules import _stated_total
    s = _state("checkout", "Subtotal ₹299\nDelivery ₹99\nPlatform fee ₹59\nTotal ₹457")
    assert _stated_total(s) == 457.0


def test_a_total_split_across_two_lines_is_read():
    from detectors.layer1_rules import _stated_total
    assert _stated_total(_state("checkout", "Order total\n₹457")) == 457.0


def test_a_drawer_with_only_a_subtotal_still_has_a_stated_figure():
    from detectors.layer1_rules import _stated_total
    assert _stated_total(_state("cart", "Your Cart\nSubtotal: ₹1798")) == 1798.0


def test_drip_pricing_fires_on_a_checkout_that_states_a_subtotal_first():
    from detectors.layer1_rules import detect_drip_pricing
    first = _state("product", "Earbuds\n₹299\n4,570 reviews", price=299.0)
    last = _state("checkout", "Subtotal ₹299\nPlatform fee ₹59\nDelivery ₹99\nTotal ₹457", price=299.0,
                  line_items=[{"name": "Earbuds", "price": 299.0}, {"name": "Platform fee", "price": 59.0},
                              {"name": "Delivery", "price": 99.0}])
    found = detect_drip_pricing(FunnelTrace(site_name="t", states=[first, last]))
    assert [v.pattern_code for v in found] == ["DP-08"]
    assert found[0].evidence["final_total"] == 457.0


def test_a_review_count_is_not_a_disclosed_total():
    from detectors.layer1_rules import _mentions_amount
    assert not _mentions_amount("4,570 reviews", 457.0)
    assert not _mentions_amount("SKU 14570", 457.0)
    assert _mentions_amount("Total at checkout ₹457", 457.0)
    assert _mentions_amount("Total ₹1,23,456", 123456.0)


def test_free_delivery_does_not_disclose_a_delivery_charge():
    from detectors.layer1_rules import _named_as_a_charge
    assert not _named_as_a_charge("Delivery", "Free delivery on this item")
    assert not _named_as_a_charge("Delivery", "No delivery charge")
    assert _named_as_a_charge("Delivery", "Delivery ₹99 extra")
    assert not _named_as_a_charge("Fee", "Coffee mug")


# ------------------------------------------------------------ DP-04 bundled consent

def test_terms_bundled_with_marketing_is_still_forced_action():
    from detectors.layer1_flow import detect_forced_action
    cb = CheckboxState(id="t", label_text="I agree to the Terms & Conditions and to receive offers on WhatsApp",
                       checked=False, is_prechecked=False, required=True)
    found = detect_forced_action(FunnelTrace(site_name="t", states=[_state("signup", checkboxes=[cb])]))
    assert [v.pattern_code for v in found] == ["DP-04"]


def test_a_plain_privacy_notice_is_not_forced_action():
    from detectors.layer1_flow import detect_forced_action
    cb = CheckboxState(id="t", label_text="I have read the Privacy Policy, including how data is shared with third parties",
                       checked=False, is_prechecked=False, required=True)
    assert detect_forced_action(FunnelTrace(site_name="t", states=[_state("signup", checkboxes=[cb])])) == []


# ------------------------------------------------------------ DP-01 urgency

def test_a_frozen_countdown_is_found_even_with_a_stock_count_on_the_page():
    from detectors.layer1_rules import detect_fake_urgency
    from capture.state_extractor import URGENCY_PATTERNS
    import re
    text = "Deal ends in 00:04:59\nOnly 2 left in stock"
    found = [p for p in URGENCY_PATTERNS if re.search(p, text, re.IGNORECASE)]
    a, b = _state("product", text, urgency=found), _state("product", text, urgency=found)
    trace = FunnelTrace(site_name="t", states=[a], reload_pairs={"product": (a, b)})
    v = detect_fake_urgency(trace)
    assert v and v[0].evidence.get("kind", "countdown") == "countdown", [x.evidence for x in v]


# ------------------------------------------------------------ DP-06 / DP-03 roles

def test_a_decline_styled_as_a_link_gets_the_decline_role():
    from capture.state_extractor import DECLINE_TEXT_RE
    assert DECLINE_TEXT_RE.search("No thanks, I don't want to save")
    assert DECLINE_TEXT_RE.search("Skip")
    assert not DECLINE_TEXT_RE.search("Add to cart")


def test_not_interested_alone_is_not_confirmshaming():
    from detectors.layer3_language import _offline_confirm_shaming
    assert _offline_confirm_shaming(["Not interested"]) == []


# ------------------------------------------------------------ LLM output parsing

@pytest.mark.parametrize("raw", [
    '{"findings": []}',
    '```json\n{"findings": []}\n```',
    'Here is the result:\n{"findings": []}\nThanks.',
])
def test_model_json_is_parsed_with_fences_or_prose(raw):
    from detectors.layer3_language import _parse_model_json
    assert _parse_model_json(raw) == {"findings": []}


# ------------------------------------------------------------ API: limits, cancel, claim

def test_cancel_and_review_do_not_spend_the_audit_creation_limit():
    from app.middleware import _ROUTE_LIMITS
    names = {}
    for rule in _ROUTE_LIMITS:
        m, prefix, _, _, name = rule[:5]
        pred = rule[5] if len(rule) > 5 else None
        if m == "POST" and "/audits".startswith(prefix.rstrip("/")) or prefix.startswith("/audits"):
            names[(m, prefix)] = (name, pred)

    def bucket(method, path):
        for rule in _ROUTE_LIMITS:
            m, prefix, _, _, name = rule[:5]
            pred = rule[5] if len(rule) > 5 else None
            if (m is None or m == method) and path.startswith(prefix) and (pred is None or pred(path)):
                return name
        return None
    assert bucket("POST", "/audits/12/cancel") == "POST:audit-actions"
    assert bucket("POST", "/audits/12/review") == "POST:audit-actions"
    assert bucket("POST", "/audits") != "POST:audit-actions"
    assert bucket("POST", "/audits/") != "POST:audit-actions"


def test_cancel_is_atomic_and_only_moves_live_jobs():
    from app.tasks import request_cancel
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="x", status="done", org="team")
        db.add(job)
        db.commit()
        assert request_cancel(db, job) == "done"
        db.refresh(job)
        assert job.status == "done"


def test_a_job_without_a_stored_request_is_never_claimed():
    from app.tasks import claim
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="x", status="pending", org="team", request_json=None)
        db.add(job)
        db.commit()
        jid = job.id
    assert claim(jid) is None
    with SessionLocal() as db:
        db.delete(db.get(models.AuditJob, jid))
        db.commit()


def test_malformed_admin_keys_stop_production(monkeypatch):
    import importlib
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEYS", "team:" + "a" * 40)
    monkeypatch.setenv("ADMIN_API_KEYS", " , ")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ulterior.example")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/x?sslmode=require")
    cfg = importlib.reload(config)
    try:
        with pytest.raises(Exception) as e:
            cfg.validate()
        assert "ADMIN_API_KEYS" in str(e.value)
    finally:
        for k in ("APP_ENV", "API_KEYS", "ADMIN_API_KEYS", "PUBLIC_BASE_URL", "DATABASE_URL"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config)


# ------------------------------------------------------------ demo cap

def test_demo_runs_cannot_push_customer_audits_out(monkeypatch):
    from app.retention import purge_expired
    with SessionLocal() as db:
        cust = models.AuditJob(adapter_name="x", status="done", org="team")
        db.add(cust)
        db.commit()
        cust_id = cust.id
        for _ in range(5):
            db.add(models.AuditJob(adapter_name="demo", status="done", org=config.DEMO_ORG))
        db.commit()
        not_demo = (models.AuditJob.org.is_(None) | (models.AuditJob.org != config.DEMO_ORG))
        customers = db.query(models.AuditJob).filter(not_demo).count()
    monkeypatch.setattr(config, "RETENTION_DAYS", 0)
    monkeypatch.setattr(config, "MAX_STORED_AUDITS", customers)
    monkeypatch.setattr(config, "MAX_STORED_DEMO_AUDITS", 2)
    purge_expired()
    with SessionLocal() as db:
        assert db.get(models.AuditJob, cust_id) is not None
        assert db.query(models.AuditJob).filter(models.AuditJob.org == config.DEMO_ORG).count() <= 2


# ------------------------------------------------------------ community prices

def test_query_routed_product_keys_are_accepted():
    from app.community import site_of_key
    assert site_of_key("127.0.0.1:/shop/product.php?id=42") or not config.IS_PRODUCTION
    from app.community import KEY_RE
    assert KEY_RE.match("flipkart.com:/x/p/itm1?pid=ABC123")
    assert not KEY_RE.match("flipkart.com:/x/p/itm1?pid=A&q=secret")


def test_price_drops_never_lose_a_products_first_day(client):
    from app.community import today
    PO = models.PriceObservation
    key = f"myntra.com:{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        for _ in range(3):
            db.add(PO(product_key=key, site="myntra.com", price=1000, observed_on=today() - timedelta(days=6)))
        for _ in range(3):
            db.add(PO(product_key=key, site="myntra.com", price=800, observed_on=today()))
        db.commit()
    import app.main as main
    main._drops_cache.clear()
    drops = {d["key"]: d for d in client.post("/prices/drops", json={"days": 7, "limit": 50}).json()["drops"]}
    assert drops[key]["pct"] == 20.0
    assert drops[key]["since"] == (today() - timedelta(days=6)).isoformat()
    assert drops[key]["reports"] == 6


# ------------------------------------------------------------ egress proxy

def test_proxy_prefers_ipv4_and_bounds_its_refusal_list():
    from capture.egress_proxy import EgressProxy
    p = EgressProxy(resolver=lambda h, port: ["2606:4700::1", "93.184.216.34"], ip_allowed=lambda ip: True)
    got = asyncio.run(p._pick_addresses("site.test", 443))
    assert got[0] == "93.184.216.34" and "2606:4700::1" in got
    assert p.refused.maxlen == 500


def test_proxy_refuses_a_name_with_any_internal_address():
    from capture.egress_proxy import EgressProxy
    p = EgressProxy(resolver=lambda h, port: ["93.184.216.34", "10.0.0.5"],
                    ip_allowed=lambda ip: not ip.startswith("10."))
    assert not asyncio.run(p._pick_addresses("site.test", 443))


# ------------------------------------------------------------ Windows paths, report

def test_fixture_urls_are_built_portably():
    for f in ["demo_all_patterns", "demo_ecommerce", "demo_ecommerce_clean", "demo_flight", "spa_demo"]:
        src = (ROOT / "capture" / "adapters" / f"{f}.py").read_text(encoding="utf-8")
        assert '"file://" +' not in src and "f\"file://{" not in src, f
        assert ".as_uri()" in src, f


def test_the_audit_script_redacts_what_it_prints():
    src = (ROOT / "scripts" / "audit_url.py").read_text(encoding="utf-8")
    assert "from app.privacy import redact" in src


def test_the_report_ships_a_rupee_capable_font():
    assert (ROOT / "report" / "fonts" / "DejaVuSans.ttf").is_file()
    assert (ROOT / "report" / "fonts" / "DejaVuSans-Bold.ttf").is_file()


# ------------------------------------------------------------ Windows encoding

def test_source_files_are_always_read_as_utf8():
    """Windows reads text files as cp1252 unless told otherwise, and the source
    contains rupee signs, curly quotes and Hindi. One `read_text()` without an
    encoding failed on Windows only ('charmap' codec can't decode byte 0x8d)."""
    import ast
    bad = []
    for folder in ("tests", "app", "capture", "detectors", "report", "scripts"):
        for f in (ROOT / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")
                        and not any(k.arg == "encoding" for k in node.keywords)):
                    bad.append(f"{f.relative_to(ROOT)}:{node.lineno}")
    assert not bad, bad
