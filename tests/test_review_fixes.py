"""
Regression tests for the issues found in the November 2026 code review.
Each test reproduces the reported failure first-hand and fails if it returns.
"""
import json
import os
import re
import sys
import threading
import http.server
import socketserver
import urllib.request
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

ROOT = Path(__file__).parent.parent
PERMISSION = {"owner_name": "Test Owner", "organisation": "Test Store", "authority": "owner", "confirmed": True}


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(config, "API_KEYS", [])
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- 1. money

@pytest.mark.parametrize("text,expected", [
    ("₹499 20% off", 499.0), ("₹499 10 left", 499.0), ("$29 30-day trial", 29.0),
    ("₹1,299", 1299.0), ("₹12,34,567", 1234567.0), ("Rs. 5,065.50", 5065.5),
    ("6,530/-", 6530.0), ("19.99 USD", 19.99), ("₹ 1 299", 1299.0), ("₹1,299.00", 1299.0),
])
def test_adjacent_numbers_are_never_glued_into_one_price(text, expected):
    from capture.state_extractor import parse_money
    assert parse_money(text) == expected


def test_line_items_treat_savings_and_discounts_correctly():
    from capture.state_extractor import _line_items_from_text
    items = {i["name"]: i["price"] for i in _line_items_from_text(
        ["Discounted Price: ₹500", "Coupon discount ₹100", "You save ₹200", "Convenience fee ₹49"])}
    assert items == {"Discounted Price": 500.0, "Coupon discount": -100.0, "Convenience fee": 49.0}


def test_json_ld_is_read_the_way_real_shops_write_it():
    from capture.state_extractor import _extract_schema_org_price as ld
    graph = ('<script id="ld" type=\'application/ld+json\' nonce="n">{"@graph":[{"@type":"WebPage"},'
             '{"@type":"Product","offers":[{"@type":"Offer","price":"1,299.00"}]}]}</script>')
    agg = '<script type="application/ld+json">[{"@type":"Product","offers":{"@type":"AggregateOffer","lowPrice":499}}]</script>'
    assert ld(graph) == 1299.0 and ld(agg) == 499.0
    assert ld('<script type="application/ld+json">{not json</script>') is None


def test_semi_transparent_text_is_not_scored_as_solid_black():
    from capture.state_extractor import contrast_ratio
    assert contrast_ratio("rgba(0, 0, 0, 0.1)", "rgb(255, 255, 255)") < 1.5
    assert contrast_ratio("rgb(0,0,0)", "rgb(255,255,255)", opacity=0.2) < 2
    assert contrast_ratio("rgb(0,0,0)", "rgb(255,255,255)") == 21.0


def test_unrelated_modals_with_the_same_class_are_different_requests():
    from capture.state_extractor import modal_signature as sig
    assert sig("Join our newsletter", "", "modal") != sig("Rate your order", "", "modal")
    assert sig("Get 10% off", "", "modal") == sig("Get 15% off", "", "modal")


# ---------------------------------------------------------------- privacy

def test_redaction_reaches_dicts_inside_lists_and_spares_ids():
    from app.privacy import redact, redact_evidence
    out = redact_evidence({"items": [{"name": "rahul@example.com 9876543210", "price": 50}],
                           "nested": [["+91 98765 43210"]]})
    flat = json.dumps(out)
    assert "rahul@" not in flat and "98765" not in flat and '"price": 50' in flat
    assert redact("SKU9876543210") == "SKU9876543210"
    assert "[PHONE_REDACTED]" in redact("call 09876543210") and "[PHONE_REDACTED]" in redact("9876543210")


# ---------------------------------------------------------------- permission / schema

def test_blank_permission_and_review_are_refused():
    from pydantic import ValidationError
    from app.schemas import AuthorizationIn, ReviewIn
    with pytest.raises(ValidationError):
        AuthorizationIn(owner_name="   ", organisation="   ", authority="owner", confirmed=True)
    with pytest.raises(ValidationError):
        ReviewIn(reviewer="   ", outcome="confirmed")


def test_auto_discover_refuses_extra_urls_and_empty_lists(client):
    r = client.post("/audits", json={"target_urls": ["https://a.example/", "https://b.example/"],
                                     "auto_discover": True, "authorization": PERMISSION})
    assert r.status_code == 400 and "one URL" in r.json()["detail"]
    assert client.post("/audits", json={"target_urls": [], "authorization": PERMISSION}).status_code == 422


# ---------------------------------------------------------------- middleware

def test_client_address_is_read_from_the_right_of_x_forwarded_for(monkeypatch):
    from app.middleware import _client_ip
    monkeypatch.setattr(config, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 9.9.9.9"}, client=SimpleNamespace(host="10.0.0.1"))
    assert _client_ip(req) == "9.9.9.9", "the leftmost entry is whatever the client typed"
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 2)
    assert _client_ip(req) == "1.2.3.4"


def test_a_429_still_carries_the_security_headers(client, monkeypatch):
    monkeypatch.setattr(config, "RUN_AUDIT_WORKERS", False)
    monkeypatch.setattr(config, "MAX_ACTIVE_AUDITS_PER_ORG", 1000)
    last = None
    for _ in range(config.RATE_LIMIT_AUDIT_PER_MINUTE + 1):
        last = client.post("/audits", json={"adapter_name": "ecommerce_clean_demo"})
    assert last.status_code == 429
    for header in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options"):
        assert header in last.headers, header
    with SessionLocal() as db:   # do not leave queued audits for other tests
        for j in db.query(models.AuditJob).filter(models.AuditJob.status == "pending"):
            j.status = "cancelled"
        db.commit()


def test_cors_preflight_allows_the_api_key_header(client):
    r = client.options("/audits", headers={
        "Origin": "http://127.0.0.1:8000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key,content-type"})
    assert r.status_code == 200
    assert "x-api-key" in r.headers.get("access-control-allow-headers", "").lower()


def test_correlation_ids_are_sanitised(client):
    ok = client.get("/healthz", headers={"X-Correlation-ID": "abc-123"})
    assert ok.headers["X-Correlation-ID"] == "abc-123"
    bad = client.get("/healthz", headers={"X-Correlation-ID": "x" * 500})
    assert bad.headers["X-Correlation-ID"] != "x" * 500 and len(bad.headers["X-Correlation-ID"]) <= 64


def test_only_the_practice_storefront_allows_inline_script(client):
    assert "'unsafe-inline'" not in client.get("/dashboard/").headers["Content-Security-Policy"].split("style-src")[0]
    shop = client.get("/storefront/listing.html")
    if shop.status_code == 200:
        assert "script-src 'self' 'unsafe-inline'" in shop.headers["Content-Security-Policy"]


# ---------------------------------------------------------------- deletion and retention

def _job_with_screenshots(status="done", created_at=None):
    from capture.funnel_walker import DEFAULT_EVIDENCE_DIR
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="x", status=status, org="team",
                              created_at=created_at or datetime.now(timezone.utc))
        db.add(job)
        db.commit()
        folder = Path(DEFAULT_EVIDENCE_DIR) / f"job_{job.id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "clean_step.png").write_bytes(b"png")
        return job.id, folder


def test_deleting_an_audit_removes_every_screenshot_it_took(client):
    jid, folder = _job_with_screenshots()
    assert client.delete(f"/audits/{jid}").status_code == 204
    assert not folder.exists(), "screenshots of steps without findings were left on disk"


def test_deleting_without_loading_findings_leaves_no_orphans():
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="x", status="done", org="team")
        db.add(job)
        db.commit()
        db.add(models.ViolationRecord(job_id=job.id, pattern_code="DP-02", pattern_name="x", step_name="cart",
                                      confidence=1.0, layer=1, explanation="x", evidence_json="{}"))
        db.commit()
        jid = job.id
    with SessionLocal() as db:
        db.delete(db.get(models.AuditJob, jid))
        db.commit()
    with SessionLocal() as db:
        assert db.query(models.ViolationRecord).filter_by(job_id=jid).count() == 0


def test_retention_counts_once_and_enforces_the_cap(monkeypatch):
    from app.retention import purge_expired
    old = datetime.now(timezone.utc) - timedelta(days=90)
    expired = [_job_with_screenshots(created_at=old.replace(tzinfo=None)) for _ in range(2)]
    # The cap counts customer audits only; public-demo runs have their own
    # cap (switched off here so this test measures one thing).
    not_demo = (models.AuditJob.org.is_(None) | (models.AuditJob.org != config.DEMO_ORG))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None)
    with SessionLocal() as db:
        total = db.query(models.AuditJob).filter(not_demo).count()
        old_all = db.query(models.AuditJob).filter(models.AuditJob.created_at < cutoff).count()
        old_count = db.query(models.AuditJob).filter(not_demo, models.AuditJob.created_at < cutoff).count()
    monkeypatch.setattr(config, "RETENTION_DAYS", 30)
    monkeypatch.setattr(config, "MAX_STORED_DEMO_AUDITS", 0)
    monkeypatch.setattr(config, "MAX_STORED_AUDITS", total - old_count - 1)
    removed = purge_expired()
    assert removed == old_all + 1, (removed, old_all)
    with SessionLocal() as db:
        assert db.query(models.AuditJob).filter(not_demo).count() == total - old_count - 1
    for _, folder in expired:
        assert not folder.exists()


def test_a_job_deleted_mid_walk_writes_nothing(monkeypatch):
    import app.tasks as tasks
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="ecommerce_dark_demo", status="running", org="team")
        db.add(job)
        db.commit()
        jid = job.id
    real_audit = tasks.audit

    def audit_then_delete(trace, use_llm=False):
        with SessionLocal() as db:
            db.delete(db.get(models.AuditJob, jid))
            db.commit()
        return real_audit(trace, use_llm=use_llm)

    monkeypatch.setattr(tasks, "audit", audit_then_delete)
    tasks._run_job(jid, "ecommerce_dark_demo", None)
    with SessionLocal() as db:
        assert db.get(models.AuditJob, jid) is None
        assert db.query(models.ViolationRecord).filter_by(job_id=jid).count() == 0


def test_findings_across_steps_cite_the_destination_screenshot():
    src = (ROOT / "app" / "tasks.py").read_text(encoding="utf-8")
    assert 'split(" -> ")[-1]' in src


# ---------------------------------------------------------------- community prices

def test_a_rejected_price_batch_does_not_block_its_valid_items(client):
    from app import community
    key = "amazon.in:B0TEST%06d" % (os.getpid() % 1000000)
    bad = client.post("/prices", json={"observations": [{"key": key, "price": 100},
                                                         {"key": "unknown.shop:x", "price": 1}]})
    assert bad.status_code == 422
    ok = client.post("/prices", json={"observations": [{"key": key, "price": 100}]})
    assert ok.status_code == 201 and ok.json()["stored"] == 1


# ---------------------------------------------------------------- hosted demo

def test_hosted_demo_may_reach_only_the_servers_own_origin(monkeypatch):
    from capture.navigation_guard import NavigationGuard
    from capture.adapters.hosted_demo import HOSTED_DARK_DEMO
    monkeypatch.setattr(config, "ALLOW_LOCAL_TARGETS", False)
    guard = NavigationGuard(trusted_origins=[HOSTED_DARK_DEMO.trusted_origin])
    base = HOSTED_DARK_DEMO.trusted_origin
    assert guard._is_allowed(base + "/storefront/listing.html")
    assert not guard._is_allowed("http://127.0.0.1:5432/")
    assert not NavigationGuard()._is_allowed(base + "/storefront/listing.html")


# ---------------------------------------------------------------- DNS rebinding

def _origin_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<p>hello-from-origin</p>")

        def log_message(self, *a):
            pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_egress_proxy_checks_the_address_it_connects_to():
    from capture.egress_proxy import EgressProxy
    srv, port = _origin_server()
    allow = {"v": False}
    proxy = EgressProxy(resolver=lambda h, p: ["127.0.0.1"], ip_allowed=lambda ip: allow["v"])
    proxy.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy.port}"}))
    with pytest.raises(urllib.error.HTTPError) as err:
        opener.open(f"http://rebind.test:{port}/", timeout=5)
    assert err.value.code == 403
    allow["v"] = True
    assert b"hello-from-origin" in opener.open(f"http://rebind.test:{port}/", timeout=5).read()
    srv.shutdown()


def test_dns_rebinding_after_the_check_is_refused_by_the_browser_path(monkeypatch):
    """The guard's lookup says 'public'; Chromium's own lookup would say
    127.0.0.1. Through the egress proxy the second answer is checked too."""
    pytest.importorskip("playwright")
    from capture import egress_proxy, navigation_guard
    from capture.browser import launch_browser, new_guarded_page, playwright_session
    srv, port = _origin_server()
    proxy = egress_proxy.EgressProxy(resolver=lambda h, p: ["127.0.0.1"], ip_allowed=lambda ip: False)
    proxy.start()
    monkeypatch.setattr(egress_proxy, "_shared", proxy)
    monkeypatch.setattr(config, "EGRESS_PROXY", True)
    monkeypatch.setattr(navigation_guard, "validate_target_url", lambda u: u)
    with playwright_session() as p:
        browser = launch_browser(p)
        page, _ = new_guarded_page(browser)
        try:
            response = page.goto(f"http://rebind.test:{port}/", timeout=10000)
            body = page.inner_text("body")
        except Exception:
            response, body = None, ""
        browser.close()
    assert "hello-from-origin" not in body
    assert response is None or response.status == 403
    srv.shutdown()


def _raw_get_via(proxy_port, target):
    import socket
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=5) as s:
        s.sendall(f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        return s.recv(200).decode("latin-1", "replace")


def test_proxy_lets_the_demo_reach_its_own_server_and_nothing_else_on_loopback(monkeypatch):
    """Playwright sends even 127.0.0.1 through the proxy. The server's own
    PUBLIC_BASE_URL is allowed (the practice storefront lives there); any other
    loopback port is refused unless a developer opted in."""
    from capture.egress_proxy import EgressProxy
    srv, port = _origin_server()
    monkeypatch.setattr(config, "ALLOW_LOCAL_TARGETS", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", f"http://127.0.0.1:{port}")
    proxy = EgressProxy()
    proxy.start()
    assert "hello-from-origin" in _raw_get_via(proxy.port, f"http://127.0.0.1:{port}/") or \
        "200" in _raw_get_via(proxy.port, f"http://127.0.0.1:{port}/")
    assert " 403 " in _raw_get_via(proxy.port, "http://127.0.0.1:5432/")
    srv.shutdown()
