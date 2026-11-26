"""
Tamper-evident evidence for an audit.

When an audit finishes, every screenshot it took is fingerprinted with
SHA-256, and so is the list of findings exactly as stored. Those fingerprints
are combined into one manifest with its own fingerprint, which is printed on
the PDF report.

Why: a report handed to a store, an agency or a consumer forum is only as
good as the reader's confidence that nothing in it was changed afterwards.
With the manifest, anyone holding the report can ask the server to recompute
the hashes (GET /audits/{id}/verify-evidence) and see, file by file, whether
the evidence still matches what was captured.

What this is NOT: a digital signature or a timestamping service. It proves
that the evidence on this server has not changed since the audit, not who ran
it. That is stated in the report too.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

MANIFEST_VERSION = 1


def _canonical(obj) -> bytes:
    """One byte-for-byte representation of a JSON value, whatever order its
    keys were built in, so the same content always hashes the same."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def findings_digest(findings: List[dict]) -> str:
    """Hash of the findings as stored. Each finding is reduced to the fields a
    reader relies on, rounded so a float read back from PostgreSQL hashes the
    same as the one written."""
    rows = sorted(
        (
            {
                "code": f.get("pattern_code"),
                "step": f.get("step_name"),
                "layer": f.get("layer"),
                "confidence": round(float(f.get("confidence") or 0), 6),
                "explanation": f.get("explanation") or "",
                "evidence": f.get("evidence") or {},
            }
            for f in findings
        ),
        key=lambda r: (str(r["code"]), str(r["step"]), r["explanation"]),
    )
    return sha256_bytes(_canonical(rows))


def build_manifest(job_id: int, site_name: str, screenshots: Dict[str, str],
                   findings: List[dict], captured_at: Optional[datetime] = None) -> dict:
    """screenshots: step name -> file path. findings: dicts with the stored
    (already redacted) values."""
    shots = []
    for step in sorted(screenshots):
        path = screenshots[step]
        if not path or not os.path.isfile(path):
            continue
        shots.append({
            "step": step,
            "file": os.path.basename(path),
            "bytes": os.path.getsize(path),
            "sha256": sha256_file(path),
        })
    body = {
        "version": MANIFEST_VERSION,
        "algorithm": "SHA-256",
        "job_id": job_id,
        "site": site_name,
        "captured_at": (captured_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "screenshots": shots,
        "findings_count": len(findings),
        "findings_sha256": findings_digest(findings),
    }
    return {**body, "fingerprint": sha256_bytes(_canonical(body))}


def verify_manifest(manifest: dict, evidence_dir: str, findings: List[dict]) -> dict:
    """Recompute everything the manifest vouches for.

    status:
      intact      -- every screenshot and the findings match
      changed     -- something no longer matches (or the manifest itself was edited)
      incomplete  -- nothing changed, but some screenshots are gone (the
                     retention purge deletes them); what remains matches
    """
    body = {k: v for k, v in manifest.items() if k != "fingerprint"}
    manifest_ok = sha256_bytes(_canonical(body)) == manifest.get("fingerprint")

    rows = []
    for shot in manifest.get("screenshots", []):
        name = os.path.basename(str(shot.get("file", "")))
        path = os.path.join(evidence_dir, name)
        if not name or not os.path.isfile(path):
            state = "missing"
        elif sha256_file(path) == shot.get("sha256"):
            state = "match"
        else:
            state = "changed"
        rows.append({"step": shot.get("step"), "file": name, "status": state})

    findings_state = "match" if findings_digest(findings) == manifest.get("findings_sha256") else "changed"

    if not manifest_ok or findings_state == "changed" or any(r["status"] == "changed" for r in rows):
        status = "changed"
    elif any(r["status"] == "missing" for r in rows):
        status = "incomplete"
    else:
        status = "intact"
    return {
        "status": status,
        "fingerprint": manifest.get("fingerprint"),
        "manifest": "match" if manifest_ok else "changed",
        "findings": findings_state,
        "screenshots": rows,
    }


def compare_findings(baseline: List[dict], current: List[dict]) -> dict:
    """What a re-check is for: which observations went away after the fixes,
    which are still there, and which are new.

    Two findings are "the same" when they name the same pattern at the same
    step (the step's first part -- discovery steps are entry/product/cart/
    checkout, pasted URLs are step_1..n, so re-auditing the same pages lines
    up)."""
    def key(f):
        return (f.get("pattern_code"), str(f.get("step_name") or "").split(" -> ")[0])

    def row(f):
        return {"pattern_code": f.get("pattern_code"), "pattern_name": f.get("pattern_name"),
                "step_name": f.get("step_name")}

    base = {}
    for f in baseline:
        base.setdefault(key(f), f)
    cur = {}
    for f in current:
        cur.setdefault(key(f), f)
    return {
        "resolved": [row(f) for k, f in base.items() if k not in cur],
        "still_present": [row(f) for k, f in cur.items() if k in base],
        "new": [row(f) for k, f in cur.items() if k not in base],
    }
