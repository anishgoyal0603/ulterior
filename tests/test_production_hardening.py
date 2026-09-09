"""
Regression tests for the pre-deployment hardening checks: config fail-fast,
security headers, sanitized errors with correlation IDs, and rate limiting.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.middleware import limiter, SlidingWindowLimiter
from app import config


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client():
    limiter.reset()
    return TestClient(app, raise_server_exceptions=False)


# --- Check 4: security headers ---------------------------------------------

def test_security_headers_present(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_absent_outside_production(client):
    # HSTS over plain HTTP is meaningless and can lock devs out of localhost.
    assert not config.IS_PRODUCTION
    r = client.get("/")
    assert "Strict-Transport-Security" not in r.headers


# --- Check 3: error handling ------------------------------------------------

def test_correlation_id_on_every_response(client):
    r = client.get("/")
    assert len(r.headers.get("X-Correlation-ID", "")) > 0


def test_404_includes_correlation_id(client):
    r = client.get("/audits/999999")
    assert r.status_code == 404
    assert r.json()["correlation_id"]


def test_500_leaks_nothing(client):
    @app.get("/__leak_probe")
    def _probe():
        raise RuntimeError("password=SUPERSECRET host=10.0.0.5 /srv/app/secret.py")

    logging.disable(logging.CRITICAL)
    try:
        r = client.get("/__leak_probe")
    finally:
        logging.disable(logging.NOTSET)

    assert r.status_code == 500
    body = r.text
    assert "SUPERSECRET" not in body
    assert "/srv/app" not in body
    assert "10.0.0.5" not in body
    assert "Traceback" not in body
    assert r.json()["correlation_id"]


# --- Check 5: rate limiting -------------------------------------------------

def test_audit_endpoint_rate_limited(client):
    codes = [
        client.post("/audits", json={"adapter_name": "ecommerce_clean_demo"}).status_code
        for _ in range(config.RATE_LIMIT_AUDIT_PER_MINUTE + 2)
    ]
    assert codes.count(429) >= 2
    assert codes.count(200) == config.RATE_LIMIT_AUDIT_PER_MINUTE


def test_rate_limit_response_has_retry_after(client):
    for _ in range(config.RATE_LIMIT_AUDIT_PER_MINUTE + 1):
        r = client.post("/audits", json={"adapter_name": "ecommerce_clean_demo"})
    assert r.status_code == 429
    assert r.headers["Retry-After"]


def test_sliding_window_expires():
    lim = SlidingWindowLimiter()
    assert lim.check("k", limit=1, window_seconds=0) is True
    # zero-width window means the previous hit is already outside it
    assert lim.check("k", limit=1, window_seconds=0) is True


# --- Check 1: config fail-fast ----------------------------------------------

def _valid_keys(monkeypatch):
    keys = ["k" * 32]
    monkeypatch.setattr(config, "API_KEYS", keys)
    monkeypatch.setattr(config, "ADMIN_API_KEYS", keys)


def test_production_rejects_sqlite(monkeypatch):
    _valid_keys(monkeypatch)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///./x.db")
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", ["https://a.com"])
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./x.db")
    with pytest.raises(config.ConfigError, match="SQLite"):
        config.validate()


def test_production_rejects_wildcard_cors(monkeypatch):
    _valid_keys(monkeypatch)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", ["*"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    with pytest.raises(config.ConfigError, match=r"\*"):
        config.validate()


def test_production_requires_db_tls(monkeypatch):
    _valid_keys(monkeypatch)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@h/d")
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", ["https://a.com"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d")
    with pytest.raises(config.ConfigError, match="sslmode"):
        config.validate()


def test_valid_production_config_passes(monkeypatch):
    _valid_keys(monkeypatch)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", ["https://a.com"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    config.validate()


def test_debug_defaults_off():
    assert config.DEBUG is False


# --- New: authentication is mandatory in production ------------------------

def test_production_refuses_to_start_without_api_keys(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "API_KEYS", [])
    monkeypatch.setattr(config, "ADMIN_API_KEYS", [])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", ["https://a.com"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    with pytest.raises(config.ConfigError, match="API_KEYS"):
        config.validate()


def test_production_rejects_short_api_keys(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    monkeypatch.setattr(config, "API_KEYS", ["short"])
    monkeypatch.setattr(config, "ADMIN_API_KEYS", ["short"])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    monkeypatch.setattr(config, "CORS_ALLOW_ORIGINS", ["https://a.com"])
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    with pytest.raises(config.ConfigError, match="brute-forceable"):
        config.validate()
