"""
Self-serve audit mode (paste a URL, no adapter code needed) -- and the
SSRF protection that makes it safe to expose to strangers. This is the
single product change needed to go from "developer tool" to "something a
non-technical customer can actually use," so it gets tested at the full
API level, not just unit-tested in isolation.
"""
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.middleware import limiter
from app import config


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(config, "API_KEYS", [])  # dev mode: auth disabled
    return TestClient(app, raise_server_exceptions=False)


def test_ssrf_attack_rejected_with_400_not_500(client):
    """The API layer, not just the standalone module -- confirms the
    HTTPException wiring actually works end to end."""
    r = client.post("/audits", json={"target_urls": ["http://169.254.169.254/latest/meta-data/"]})
    assert r.status_code == 400
    assert "private/internal" in r.json()["detail"]


def test_localhost_rejected(client):
    r = client.post("/audits", json={"target_urls": ["http://localhost:5432/"]})
    assert r.status_code == 400


def test_neither_adapter_nor_urls_rejected(client):
    r = client.post("/audits", json={})
    assert r.status_code == 400


def test_both_adapter_and_urls_rejected(client):
    r = client.post("/audits", json={"adapter_name": "ecommerce_clean_demo",
                                      "target_urls": ["https://example.com"]})
    assert r.status_code == 400


def test_legitimate_self_serve_url_accepted_and_queued(client):
    """A safe external URL should be accepted and actually queue a job --
    confirms this isn't just "SSRF checks work," but that the whole
    self-serve path (no adapter_name, just target_urls) functions."""
    r = client.post("/audits", json={"target_urls": ["https://example.com/"]})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("pending", "running", "done", "failed")
    assert body["id"] > 0


# --- The local-development escape hatch ---------------------------------
#
# ALLOW_LOCAL_TARGETS exists so a developer can audit the bundled storefront
# on 127.0.0.1 through the normal self-serve path. That is a real convenience
# and a real risk, so these tests pin the three limits that make it safe.
# If any of them ever fails, the hatch has become an SSRF hole.

import ipaddress

from app import ssrf_guard
from app.ssrf_guard import validate_target_url, UnsafeURLError


@pytest.fixture
def local_targets_allowed(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_LOCAL_TARGETS", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", False)
    yield


def test_loopback_is_refused_by_default():
    """Off unless someone asked for it. The default must stay closed."""
    assert not config.ALLOW_LOCAL_TARGETS, "the escape hatch is on by default"
    with pytest.raises(UnsafeURLError):
        validate_target_url("http://127.0.0.1:8000/storefront/listing.html")


def test_loopback_is_accepted_when_a_developer_opts_in(local_targets_allowed):
    """The whole point: the bundled storefront becomes auditable."""
    url = "http://127.0.0.1:8000/storefront/listing.html"
    assert validate_target_url(url) == url
    assert validate_target_url("http://localhost:8000/x") is not None


def test_the_hatch_never_opens_in_production(monkeypatch):
    """Even set, it must do nothing when APP_ENV=production -- defence in
    depth behind config.validate(), which refuses to start at all."""
    monkeypatch.setattr(config, "ALLOW_LOCAL_TARGETS", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    with pytest.raises(UnsafeURLError):
        validate_target_url("http://127.0.0.1:8000/storefront/listing.html")


def test_production_refuses_to_start_with_the_hatch_set(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_LOCAL_TARGETS", True)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "API_KEYS", ["k" * 32])
    monkeypatch.setattr(config, "ADMIN_API_KEYS", ["k" * 32])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@h/db?sslmode=require")
    with pytest.raises(Exception) as excinfo:
        config.validate()
    assert "ALLOW_LOCAL_TARGETS" in str(excinfo.value)


@pytest.mark.parametrize("ip,what", [
    ("169.254.169.254", "the cloud metadata endpoint"),
    ("10.0.0.5", "a private LAN host"),
    ("192.168.1.1", "a home router"),
    ("172.16.0.1", "a private LAN host"),
])
def test_the_hatch_permits_loopback_only(local_targets_allowed, ip, what):
    """Reaching your own laptop is the need; reaching the rest of the network
    is not. A dev machine is often a cloud VM, so 169.254.169.254 in
    particular must stay blocked even with the hatch open."""
    assert ssrf_guard._is_private_or_reserved(ip) is True, (
        f"{ip} ({what}) became reachable when the loopback hatch opened"
    )


def test_ipv6_loopback_is_covered_too(local_targets_allowed):
    assert ipaddress.ip_address("::1").is_loopback
    assert ssrf_guard._is_private_or_reserved("::1") is False
