"""
Evidence fingerprints, the permission record, the re-check comparison and the
fix checklist (Ulterior 2.3).

What these protect:
  - a pasted site is never audited without a recorded permission;
  - any change to a screenshot or a stored finding after the audit shows up
    when the evidence is verified;
  - the PDF carries the permission, the fingerprints, the fixes and, on a
    re-check, what changed -- and still never claims a legal violation.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from app import config, models
from app.db import SessionLocal, init_db
from app.integrity import build_manifest, compare_findings, verify_manifest
from app.main import app
from app.middleware import limiter
from report.remediation import REMEDIATION, checklist_for

PERMISSION = {"owner_name": "Asha Verma", "organisation": "Example Store Pvt Ltd",
              "authority": "written_permission", "reference": "Signed form, 2 Dec 2026",
              "confirmed": True}

FINDINGS = [
    {"pattern_code": "DP-02", "pattern_name": "Basket Sneaking", "step_name": "cart", "layer": 1,
     "confidence": 1.0, "explanation": "Insurance was pre-ticked.", "evidence": {"evidence_tier": "provable"}},
    {"pattern_code": "DP-08", "pattern_name": "Drip Pricing", "step_name": "cart -> checkout", "layer": 1,
     "confidence": 0.95, "explanation": "Rs 299 became Rs 457.", "evidence": {"evidence_tier": "provable"}},
]


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    monkeypatch.setattr(config, "API_KEYS", [])
    return TestClient(app, raise_server_exceptions=False)


def _shots(tmp_path):
    a = tmp_path / "shop_cart.png"
    b = tmp_path / "shop_checkout.png"
    a.write_bytes(b"\x89PNG cart pixels")
    b.write_bytes(b"\x89PNG checkout pixels")
    return {"cart": str(a), "checkout": str(b)}


# ------------------------------------------------------------ fingerprints

def test_an_untouched_audit_verifies_as_intact(tmp_path):
    m = build_manifest(7, "shop", _shots(tmp_path), FINDINGS)
    assert len(m["fingerprint"]) == 64 and len(m["screenshots"]) == 2
    v = verify_manifest(m, str(tmp_path), FINDINGS)
    assert v["status"] == "intact", v


def test_an_edited_screenshot_is_caught(tmp_path):
    shots = _shots(tmp_path)
    m = build_manifest(7, "shop", shots, FINDINGS)
    Path(shots["cart"]).write_bytes(b"\x89PNG edited")
    v = verify_manifest(m, str(tmp_path), FINDINGS)
    assert v["status"] == "changed"
    assert {r["step"]: r["status"] for r in v["screenshots"]} == {"cart": "changed", "checkout": "match"}


def test_an_edited_finding_is_caught(tmp_path):
    m = build_manifest(7, "shop", _shots(tmp_path), FINDINGS)
    edited = [dict(FINDINGS[0], explanation="Nothing to see here."), FINDINGS[1]]
    v = verify_manifest(m, str(tmp_path), edited)
    assert v["status"] == "changed" and v["findings"] == "changed"


def test_an_edited_manifest_is_caught(tmp_path):
    m = build_manifest(7, "shop", _shots(tmp_path), FINDINGS)
    m["findings_count"] = 0
    assert verify_manifest(m, str(tmp_path), FINDINGS)["manifest"] == "changed"


def test_deleted_evidence_is_reported_as_incomplete_not_changed(tmp_path):
    shots = _shots(tmp_path)
    m = build_manifest(7, "shop", shots, FINDINGS)
    os.remove(shots["checkout"])
    v = verify_manifest(m, str(tmp_path), FINDINGS)
    assert v["status"] == "incomplete"


def test_finding_order_and_float_noise_do_not_change_the_fingerprint(tmp_path):
    m = build_manifest(7, "shop", _shots(tmp_path), FINDINGS)
    shuffled = [dict(FINDINGS[1], confidence=0.9500000000001), FINDINGS[0]]
    assert verify_manifest(m, str(tmp_path), shuffled)["status"] == "intact"


# ------------------------------------------------------------- re-check

def test_a_recheck_lists_resolved_remaining_and_new():
    later = [FINDINGS[1], {"pattern_code": "DP-01", "pattern_name": "False Urgency",
                           "step_name": "product"}]
    c = compare_findings(FINDINGS, later)
    assert [r["pattern_code"] for r in c["resolved"]] == ["DP-02"]
    assert [r["pattern_code"] for r in c["still_present"]] == ["DP-08"]
    assert [r["pattern_code"] for r in c["new"]] == ["DP-01"]


# ------------------------------------------------------------ fix checklist

def test_every_pattern_has_a_fix_and_a_check():
    assert sorted(REMEDIATION) == [f"DP-{i:02d}" for i in range(1, 14)]
    for code, (name, fix, check) in REMEDIATION.items():
        assert name and len(fix) > 40 and len(check) > 20, code
        assert "violat" not in (fix + check).lower(), code


def test_the_checklist_lists_each_pattern_once_in_order():
    assert [c[0] for c in checklist_for(["DP-08", "DP-02", "DP-08", "DP-99"])] == ["DP-08", "DP-02"]


# ------------------------------------------------------------ permission

def test_a_pasted_url_without_permission_is_refused(client):
    r = client.post("/audits", json={"target_urls": ["https://example.com/"]})
    assert r.status_code == 400
    assert "permission" in r.json()["detail"]


def test_permission_must_be_confirmed_and_plain_text(client):
    unconfirmed = dict(PERMISSION, confirmed=False)
    r = client.post("/audits", json={"target_urls": ["https://example.com/"], "authorization": unconfirmed})
    assert r.status_code == 422
    markup = dict(PERMISSION, owner_name="<img src=x onerror=alert(1)>")
    r = client.post("/audits", json={"target_urls": ["https://example.com/"], "authorization": markup})
    assert r.status_code == 422
    r = client.post("/audits", json={"target_urls": ["https://example.com/"],
                                     "authorization": dict(PERMISSION, authority="trust me")})
    assert r.status_code == 422


def test_registered_demo_sites_need_no_permission_record(client):
    r = client.post("/audits", json={"adapter_name": "ecommerce_clean_demo"})
    assert r.status_code == 200, r.text
    assert r.json()["authorization"] is None


def test_the_permission_record_is_stored_without_the_key(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEYS", ["k-for-test-1234567890"])
    r = client.post("/audits", headers={"X-API-Key": "k-for-test-1234567890"},
                    json={"target_urls": ["https://8.8.8.8/"], "authorization": PERMISSION})
    if r.status_code == 400 and "resolve" in r.text.lower():
        pytest.skip("no DNS in this environment")
    assert r.status_code == 200, r.text
    a = r.json()["authorization"]
    assert a["owner_name"] == "Asha Verma" and a["authority"] == "written_permission"
    assert a["recorded_at"].endswith("Z") and len(a["recorded_by_key"]) == 12
    assert "k-for-test" not in json.dumps(a)


# ------------------------------------------------------- end to end

def _run_now(adapter_name, authorization=None):
    """Create a job and run it in this thread, the way the worker does."""
    from app.tasks import _run_job
    with SessionLocal() as db:
        job = models.AuditJob(adapter_name=adapter_name, status="pending",
                              authorization_json=json.dumps(authorization) if authorization else None)
        db.add(job)
        db.commit()
        job_id = job.id
    _run_job(job_id, adapter_name, None)
    return job_id


@pytest.fixture(scope="module")
def finished_audits():
    first = _run_now("ecommerce_dark_demo", authorization=dict(PERMISSION, recorded_at="2026-12-02T10:00:00Z",
                                                                  recorded_by_key="abc123abc123"))
    second = _run_now("ecommerce_clean_demo")
    with SessionLocal() as db:
        assert db.get(models.AuditJob, first).status == "done"
        assert db.get(models.AuditJob, second).status == "done"
    return first, second


def test_a_finished_audit_carries_its_fingerprints(client, finished_audits):
    first, _ = finished_audits
    job = client.get(f"/audits/{first}").json()
    m = job["evidence_manifest"]
    assert m and len(m["fingerprint"]) == 64
    assert m["findings_count"] == len(job["violations"]) > 0
    assert m["screenshots"], "the walk took screenshots, so they must be fingerprinted"
    assert job["authorization"]["organisation"] == "Example Store Pvt Ltd"


def test_verify_evidence_says_intact_then_catches_tampering(client, finished_audits):
    first, _ = finished_audits
    assert client.get(f"/audits/{first}/verify-evidence").json()["status"] == "intact"
    with SessionLocal() as db:
        v = db.query(models.ViolationRecord).filter_by(job_id=first).first()
        original = v.explanation
        v.explanation = original + " (edited)"
        db.commit()
    try:
        r = client.get(f"/audits/{first}/verify-evidence").json()
        assert r["status"] == "changed" and r["findings"] == "changed"
    finally:
        with SessionLocal() as db:
            v = db.query(models.ViolationRecord).filter_by(job_id=first).first()
            v.explanation = original
            db.commit()


def test_compare_endpoint_and_its_guards(client, finished_audits):
    first, second = finished_audits
    c = client.get(f"/audits/{second}/compare", params={"baseline": first}).json()
    assert {"resolved", "still_present", "new", "same_site"} <= set(c)
    assert len(c["resolved"]) > 0, "the honest store should not show the dark store's patterns"
    assert client.get(f"/audits/{first}/compare", params={"baseline": first}).status_code == 400
    assert client.get(f"/audits/{first}/compare", params={"baseline": 999999}).status_code == 404


def test_the_pdf_carries_permission_fixes_recheck_and_fingerprints(client, finished_audits):
    high = pytest.importorskip("pdfminer.high_level")
    first, second = finished_audits
    r = client.get(f"/audits/{first}/report.pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    import io
    text = re.sub(r"\s+", " ", high.extract_text(io.BytesIO(r.content)))
    assert "Permission to assess" in text and "Asha Verma" in text
    assert "Suggested fixes for developers" in text
    assert "Evidence integrity" in text and "Report fingerprint" in text
    assert "violates" not in text.lower()

    r = client.get(f"/audits/{second}/report.pdf", params={"baseline": first})
    assert r.status_code == 200
    text = re.sub(r"\s+", " ", high.extract_text(io.BytesIO(r.content)))
    assert f"Changes since audit #{first}" in text and "No longer observed" in text


def test_the_dashboard_has_the_permission_and_recheck_controls(client):
    html = client.get("/dashboard/").text
    for el in ('id="perm-owner"', 'id="perm-org"', 'id="perm-authority"', 'id="perm-confirm"',
               'id="verify-evidence"', 'id="baseline-id"', 'id="compare-run"'):
        assert el in html, el
    js = (Path(__file__).parent.parent / "dashboard" / "dashboard.js").read_text(encoding="utf-8")
    assert "authorization" in js and "verify-evidence" in js and "baseline=" in js
