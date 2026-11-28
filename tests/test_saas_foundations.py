"""
Ulterior 2.4: the foundations the roadmap made release gates.

  * Browser safety: one launch path; WebRTC removed; WebSockets checked;
    carrier-grade NAT, NAT64, 6to4 and IPv4-mapped addresses refused.
  * Organisations: a key sees only its own organisation's audits, reports,
    evidence and comparisons -- enforced on the server.
  * Durable jobs: atomic claim, heartbeat recovery, retry limit, cancel,
    idempotency, per-organisation concurrency cap, resume after restart.
  * Coverage: every pattern Observed / Not observed / Partially / Not assessed.
  * Human review recorded and printed.
"""
import json
import re
import sys
import time
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
TEAM, ORG_A, ORG_B = "team-key-" + "t" * 24, "orga-key-" + "a" * 24, "orgb-key-" + "b" * 24


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def orgs(monkeypatch):
    keys, key_orgs, _ = config._parse_keys(f"team:{TEAM},orga:{ORG_A},orgb:{ORG_B}")
    monkeypatch.setattr(config, "API_KEYS", keys)
    monkeypatch.setattr(config, "KEY_ORGS", key_orgs)
    monkeypatch.setattr(config, "ADMIN_API_KEYS", [k for k in keys if key_orgs[k] == "team"])
    limiter.reset()
    return TestClient(app, raise_server_exceptions=False)


def _h(key):
    return {"X-API-Key": key}


@pytest.fixture(autouse=True)
def _leave_no_queued_audits():
    """Queue-only tests leave pending audits behind; a later recovery sweep
    (here or in another test file's server) would then run them all."""
    yield
    with SessionLocal() as db:
        for j in db.query(models.AuditJob).filter(models.AuditJob.status == "pending",
                                                  models.AuditJob.org.in_(("orga", "orgb", "jobs"))):
            j.status = "cancelled"
        db.commit()


def _done_job(org, site="Shop"):
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="self-serve", site_name=site, status="done", org=org,
                              evidence_manifest_json=json.dumps({"fingerprint": "x" * 64, "screenshots": [],
                                                                 "findings_sha256": "", "findings_count": 1}))
        db.add(job)
        db.commit()
        db.add(models.ViolationRecord(job_id=job.id, pattern_code="DP-02", pattern_name="Basket Sneaking",
                                      step_name="cart", confidence=1.0, layer=1, explanation="Pre-ticked.",
                                      evidence_json='{"evidence_tier": "provable"}'))
        db.commit()
        return job.id


# ------------------------------------------------------------ browser safety

def test_crawler_address_rules_cover_the_tricky_forms():
    from app.ssrf_guard import _is_private_or_reserved as blocked
    for ip in ("100.64.0.1", "100.127.255.254", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe",
               "2002:a9fe:a9fe::1", "fe80::1%eth0", "198.18.0.1", "192.0.0.8", "fd12::1", "0.0.0.0"):
        assert blocked(ip), ip
    for ip in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
        assert not blocked(ip), ip


def test_only_one_code_path_launches_the_crawling_browser():
    offenders = []
    for folder in ("app", "capture", "detectors", "report"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "browser.py":
                continue
            if re.search(r"chromium\.launch\(", path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"launch Chromium only through capture/browser.py: {offenders}"


def test_webrtc_is_gone_and_websockets_are_checked():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from capture.browser import launch_browser, new_guarded_page
    with sync_playwright() as p:
        browser = launch_browser(p)
        page, guard = new_guarded_page(browser, allow_file=True)
        page.goto((ROOT / "fixtures" / "ecommerce_clean" / "listing.html").resolve().as_uri())
        assert page.evaluate("typeof window.RTCPeerConnection") == "undefined"
        outcome = page.evaluate("""() => new Promise((resolve) => {
            let ws;
            try { ws = new WebSocket("ws://127.0.0.1:9/"); } catch (e) { return resolve("threw"); }
            ws.onopen = () => resolve("open");
            ws.onclose = () => resolve("closed");
            ws.onerror = () => resolve("error");
            setTimeout(() => resolve("timeout"), 4000);
        })""")
        browser.close()
    assert outcome in ("threw", "closed", "error"), outcome
    if guard.websockets == "routed":
        assert any("WebSocket" in note for note in guard.blocked), guard.blocked


# ------------------------------------------------------------ organisations

def test_keys_carry_their_organisation_and_org_keys_are_not_admin():
    keys, orgs, bad = config._parse_keys(f"{TEAM}, parkhya:{ORG_A}, BAD NAME:x, orgb:{ORG_B}")
    assert orgs[TEAM] == "team" and orgs[ORG_A] == "parkhya" and orgs[ORG_B] == "orgb"
    assert bad == ["bad name"]
    default_admin = [k for k in keys if orgs[k] == config.DEFAULT_ORG]
    assert default_admin == [TEAM]


def test_one_organisation_cannot_reach_anothers_audits(orgs):
    a, b = _done_job("orga", "Shop A"), _done_job("orgb", "Shop B")
    assert orgs.get(f"/audits/{a}", headers=_h(ORG_A)).status_code == 200
    for path in (f"/audits/{b}", f"/audits/{b}/report.pdf", f"/audits/{b}/verify-evidence",
                 f"/audits/{a}/compare?baseline={b}"):
        assert orgs.get(path, headers=_h(ORG_A)).status_code == 404, path
    assert orgs.post(f"/audits/{b}/cancel", headers=_h(ORG_A)).status_code == 404
    assert orgs.post(f"/audits/{b}/review", headers=_h(ORG_A),
                     json={"reviewer": "Eve", "outcome": "confirmed"}).status_code == 404

    listed = {j["id"] for j in orgs.get("/audits?limit=200", headers=_h(ORG_A)).json()}
    assert a in listed and b not in listed
    summary_a = orgs.get("/dashboard/summary", headers=_h(ORG_A)).json()
    with SessionLocal() as db:
        own = db.query(models.AuditJob).filter(models.AuditJob.org == "orga").count()
    assert summary_a["total_audits"] == own

    assert orgs.get("/extension-reports", headers=_h(ORG_A)).status_code == 403
    # The team's admin key still sees everything.
    assert orgs.get(f"/audits/{b}", headers=_h(TEAM)).status_code == 200
    assert orgs.get("/extension-reports", headers=_h(TEAM)).status_code == 200


def test_new_audits_belong_to_the_callers_organisation(orgs, monkeypatch):
    monkeypatch.setattr(config, "RUN_AUDIT_WORKERS", False)   # queue only; no browser here
    r = orgs.post("/audits", headers=_h(ORG_B), json={"adapter_name": "ecommerce_clean_demo"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert db.get(models.AuditJob, r.json()["id"]).org == "orgb"


# ------------------------------------------------------------ durable jobs

def _pending(org="jobs", request=None, **extra):
    extra.setdefault("status", "pending")
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="ecommerce_clean_demo", org=org, attempts=0,
                              request_json=json.dumps(request or {"adapter_name": "ecommerce_clean_demo",
                                                                  "target_urls": None, "use_llm": False,
                                                                  "auto_discover": False}), **extra)
        db.add(job)
        db.commit()
        return job.id


def test_a_job_can_be_claimed_only_once():
    from app.tasks import claim
    jid = _pending()
    assert claim(jid)["adapter_name"] == "ecommerce_clean_demo"
    assert claim(jid) is None
    with SessionLocal() as db:
        job = db.get(models.AuditJob, jid)
        assert job.status == "running" and job.attempts == 1 and job.heartbeat_at is not None


def test_abandoned_jobs_are_retried_then_failed_and_old_ones_are_closed():
    from app.tasks import recover_jobs
    old = datetime.now(timezone.utc) - timedelta(seconds=config.JOB_STALE_SECONDS + 60)
    retry = _pending(status="running", heartbeat_at=old)
    with SessionLocal() as db:
        db.get(models.AuditJob, retry).attempts = 1
        exhausted = models.AuditJob(adapter_name="x", status="running", heartbeat_at=old,
                                    attempts=config.JOB_MAX_ATTEMPTS, request_json="{}", org="jobs")
        legacy = models.AuditJob(adapter_name="x", status="running", created_at=old, org="jobs")
        stopping = models.AuditJob(adapter_name="x", status="running", heartbeat_at=old, attempts=1,
                                   request_json="{}", cancel_requested=1, org="jobs")
        fresh = models.AuditJob(adapter_name="x", status="running", heartbeat_at=datetime.now(timezone.utc),
                                attempts=1, request_json="{}", org="jobs")
        db.add_all([exhausted, legacy, stopping, fresh])
        db.commit()
        ids = exhausted.id, legacy.id, stopping.id, fresh.id
    recover_jobs(dispatch_pending=False)
    with SessionLocal() as db:
        assert db.get(models.AuditJob, retry).status == "pending"
        assert db.get(models.AuditJob, ids[0]).status == "failed"
        assert "stopped" in db.get(models.AuditJob, ids[0]).error_message
        assert db.get(models.AuditJob, ids[1]).status == "failed"
        assert db.get(models.AuditJob, ids[2]).status == "cancelled"
        assert db.get(models.AuditJob, ids[3]).status == "running", "a live job must not be touched"
        db.get(models.AuditJob, retry).status = "failed"   # do not leave it for other tests' sweeps
        db.commit()


def test_a_queued_audit_survives_a_restart_and_finishes():
    """A pending job left in the database (the API restarted before a worker
    took it) is found by the recovery sweep and run to completion."""
    from app.tasks import recover_jobs
    jid = _pending(org="restart")
    recover_jobs()
    deadline = time.time() + 120
    while time.time() < deadline:
        with SessionLocal() as db:
            job = db.get(models.AuditJob, jid)
            if job.status in ("done", "failed"):
                break
        time.sleep(1)
    with SessionLocal() as db:
        job = db.get(models.AuditJob, jid)
        assert job.status == "done", job.error_message
        cov = json.loads(job.coverage_json)
        assert [c["code"] for c in cov] == [f"DP-{i:02d}" for i in range(1, 14)]


def test_cancel_stops_a_running_walk_before_findings_are_written():
    from app.tasks import _run_job
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name="ecommerce_dark_demo", status="running", cancel_requested=1,
                              org="jobs")
        db.add(job)
        db.commit()
        jid = job.id
    _run_job(jid, "ecommerce_dark_demo", None)
    with SessionLocal() as db:
        job = db.get(models.AuditJob, jid)
        assert job.status == "cancelled" and job.violations == []


def test_cancel_endpoint(orgs, monkeypatch):
    monkeypatch.setattr(config, "RUN_AUDIT_WORKERS", False)
    jid = orgs.post("/audits", headers=_h(ORG_A), json={"adapter_name": "ecommerce_clean_demo"}).json()["id"]
    r = orgs.post(f"/audits/{jid}/cancel", headers=_h(ORG_A))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert orgs.post(f"/audits/{jid}/cancel", headers=_h(ORG_A)).status_code == 409


def test_idempotency_key_and_per_org_cap(orgs, monkeypatch):
    monkeypatch.setattr(config, "RUN_AUDIT_WORKERS", False)
    monkeypatch.setattr(config, "MAX_ACTIVE_AUDITS_PER_ORG", 2)
    key = {"X-API-Key": ORG_B, "Idempotency-Key": "pilot-run-%d" % time.time_ns()}
    with SessionLocal() as db:   # start from an empty queue for this org
        for j in db.query(models.AuditJob).filter(models.AuditJob.org == "orgb",
                                                  models.AuditJob.status.in_(("pending", "running"))):
            j.status = "cancelled"
        db.commit()
    first = orgs.post("/audits", headers=key, json={"adapter_name": "ecommerce_clean_demo"}).json()["id"]
    again = orgs.post("/audits", headers=key, json={"adapter_name": "ecommerce_clean_demo"}).json()["id"]
    assert first == again
    assert orgs.post("/audits", headers=_h(ORG_B), json={"adapter_name": "ecommerce_clean_demo"}).status_code == 200
    r = orgs.post("/audits", headers=_h(ORG_B), json={"adapter_name": "ecommerce_clean_demo"})
    assert r.status_code == 429 and "already queued or running" in r.json()["detail"]
    # Another organisation is not blocked by orgb's queue.
    assert orgs.post("/audits", headers=_h(ORG_A), json={"adapter_name": "ecommerce_clean_demo"}).status_code == 200


# ------------------------------------------------------------ coverage

def _state(**kw):
    base = dict(price=None, line_items=[], checkboxes=[], buttons=[], full_text="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_coverage_never_reads_silence_as_clean():
    from detectors.coverage import assess_coverage
    # Two buttons with no decline among them: DP-06 (and DP-03) had nothing to
    # judge, so they are "not assessed" -- not a clean bill of health.
    plain = [SimpleNamespace(role="neutral"), SimpleNamespace(role="primary")]
    one_product_page = SimpleNamespace(states=[_state(price=299.0, buttons=plain)], reload_pairs={},
                                       step_roles={})
    cov = {c["code"]: c for c in assess_coverage(one_product_page, [])}
    assert cov["DP-08"]["status"] == "not_assessed" and "checkout" in cov["DP-08"]["note"]
    assert cov["DP-02"]["status"] == "not_assessed"
    assert cov["DP-01"]["status"] == "not_assessed"
    assert cov["DP-06"]["status"] == "not_assessed"
    with_decline = [SimpleNamespace(role="primary"), SimpleNamespace(role="decline")]
    page2 = SimpleNamespace(states=[_state(price=299.0, buttons=with_decline)], reload_pairs={}, step_roles={})
    cov2 = {c["code"]: c for c in assess_coverage(page2, [])}
    assert cov2["DP-06"]["status"] == "not_observed"
    assert cov["DP-09"]["status"] == "partially_assessed"

    walk = SimpleNamespace(states=[_state(price=299.0, buttons=with_decline),
                                   _state(checkboxes=[1], line_items=[{"name": "x", "price": 1}])],
                           reload_pairs={"product": 1}, step_roles={})
    cov = {c["code"]: c for c in assess_coverage(walk, ["DP-08"])}
    assert cov["DP-08"]["status"] == "observed"
    assert cov["DP-02"]["status"] == "not_observed"
    assert cov["DP-01"]["status"] == "not_observed"


# ------------------------------------------------------------ review + report

def test_review_is_recorded_and_printed(orgs):
    high = pytest.importorskip("pdfminer.high_level")
    import io
    jid = _done_job("orga")
    with SessionLocal() as db:
        db.get(models.AuditJob, jid).coverage_json = json.dumps(
            [{"code": "DP-02", "name": "Basket Sneaking", "status": "observed", "note": ""},
             {"code": "DP-08", "name": "Drip Pricing", "status": "not_assessed",
              "note": "No first price followed by an itemised total (checkout) was captured."}])
        db.commit()
    pdf = orgs.get(f"/audits/{jid}/report.pdf", headers=_h(ORG_A)).content
    text = re.sub(r"\s+", " ", high.extract_text(io.BytesIO(pdf)))
    assert "Not yet reviewed by a person" in text and "Coverage of the 13 patterns" in text
    assert "Not assessed" in text

    r = orgs.post(f"/audits/{jid}/review", headers=_h(ORG_A),
                  json={"reviewer": "Asha Verma", "outcome": "confirmed", "note": "All three checked"})
    assert r.status_code == 200 and r.json()["review"]["reviewer"] == "Asha Verma"
    assert orgs.post(f"/audits/{jid}/review", headers=_h(ORG_A),
                     json={"reviewer": "<b>x</b>", "outcome": "confirmed"}).status_code == 422
    pdf = orgs.get(f"/audits/{jid}/report.pdf", headers=_h(ORG_A)).content
    text = re.sub(r"\s+", " ", high.extract_text(io.BytesIO(pdf)))
    assert "Asha Verma confirmed every observation" in text
    assert "violates" not in text.lower()


def test_dashboard_has_stop_coverage_and_review_controls(orgs):
    html = orgs.get("/dashboard/").text
    for el in ('id="cancel-audit"', 'id="trust-coverage"', 'id="review-name"', 'id="review-save"'):
        assert el in html, el
    js = (ROOT / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    assert "/cancel" in js and "/review" in js and "renderCoverage" in js


def test_audits_started_together_all_finish():
    """uvicorn runs on uvloop on Linux. Two threads starting Playwright at the
    same moment used to fail one audit with "Racing with another loop to
    spawn a process"; starts are now serialised in capture/browser.py.

    uvloop does not exist on Windows, and this used to be SKIPPED there -- a
    skipped test proves nothing. Without uvloop it still runs, on the normal
    event loop, so three audits started together must still all finish."""
    import asyncio
    from app.tasks import recover_jobs
    try:
        import uvloop
    except ImportError:
        uvloop = None
    old_policy = asyncio.get_event_loop_policy()
    if uvloop is not None:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    try:
        ids = [_pending(org="together") for _ in range(3)]
        recover_jobs()
        deadline = time.time() + 180
        while time.time() < deadline:
            with SessionLocal() as db:
                states = [db.get(models.AuditJob, i).status for i in ids]
            if all(st in ("done", "failed") for st in states):
                break
            time.sleep(1)
    finally:
        asyncio.set_event_loop_policy(old_policy)
    assert states == ["done", "done", "done"], states


def test_crawlers_start_playwright_only_through_the_locked_session():
    for name in ("funnel_walker.py", "funnel_discovery.py", "inspect_site.py"):
        src = (ROOT / "capture" / name).read_text(encoding="utf-8")
        assert "playwright_session()" in src and "sync_playwright()" not in src, name
