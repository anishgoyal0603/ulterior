"""
Regression tests for the deep security audit: stored XSS from audited page
content, SQL injection resistance, and screenshot path traversal.

The XSS class here is specific to this app and easy to miss: every string the
dashboard renders comes from a website we audited. An audited site is by
definition untrusted and often adversarial -- we are accusing it of deceptive
design -- so its button labels must be treated as attacker-controlled input.
"""
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from playwright.sync_api import sync_playwright

from capture.state_extractor import extract_page_state
from capture.funnel_walker import FunnelTrace, walk_funnel
from capture.adapters.base import SiteAdapter, FunnelStep
from detectors.pipeline import audit

XSS_FIXTURE = Path(__file__).parent.parent / "fixtures" / "xss_attack" / "cart.html"
DASHBOARD = Path(__file__).parent.parent / "dashboard" / "index.html"
# The dashboard's JS moved out of an inline <script> block into its own
# same-origin file so the page survives a viewer that forbids inline script
# (see tests/test_frontend_csp_safety.py). The escaping this file checks is
# in that file now -- the markup no longer contains any JS to check.
DASHBOARD_JS = Path(__file__).parent.parent / "dashboard" / "dashboard.js"
POPUP_JS = Path(__file__).parent.parent / "extension" / "popup.js"


def test_xss_payload_survives_capture_so_rendering_must_escape():
    """
    Documents the threat: a malicious audited site CAN get an HTML payload
    into the violation data. This is expected and fine -- the payload is
    evidence. What matters is that the render layer escapes it (tests below).
    """
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(f"file://{XSS_FIXTURE}")
        pg.wait_for_timeout(200)
        state = extract_page_state(pg, "cart")
        b.close()

    trace = FunnelTrace(site_name="Malicious")
    trace.states = [state]
    violations = audit(trace)
    combined = " ".join(v.explanation for v in violations)
    assert "onerror" in combined, "fixture should carry a payload"


def test_dashboard_escapes_all_audit_derived_fields():
    src = DASHBOARD_JS.read_text()
    assert "function escapeHtml" in src, "dashboard must define an escaper"

    # Every interpolation of a violation/job field must be wrapped in
    # escapeHtml() or coerced with Number().
    risky_fields = [
        "v.explanation", "v.pattern_name", "v.pattern_code", "v.step_name",
        "j.site_name", "j.status",
    ]
    for field in risky_fields:
        for match in re.finditer(re.escape("${") + r"[^}]*" + re.escape(field) + r"[^}]*\}", src):
            snippet = match.group(0)
            assert "escapeHtml" in snippet, f"unescaped sink for {field}: {snippet}"


def test_extension_popup_uses_no_innerhtml_for_untrusted_data():
    src = POPUP_JS.read_text()
    code = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("*") and not line.strip().startswith("/*")
    )
    assert "innerHTML" not in code, "popup must not use innerHTML with page-derived data"
    assert "textContent" in code


def test_no_raw_sql_in_any_query_path():
    """Every query that carries data goes through the SQLAlchemy ORM, which
    parameterizes it. This fails loudly if someone later hand-writes one.

    app/db.py is exempt from the string scan and covered by the stricter test
    below instead: it holds the project's only DDL, and DDL cannot use bound
    parameters for identifiers on ANY database, so "parameterize it" is not an
    available answer there. The guarantee that replaces it -- that the DDL can
    only ever name a column the models already declare -- is a stronger one,
    and asserting it is what the next test does.
    """
    root = Path(__file__).parent.parent
    scanned = []
    offenders = []
    for py in list((root / "app").glob("*.py")) + list((root / "detectors").glob("*.py")):
        if py.name == "db.py":
            continue
        scanned.append(py.name)
        text = py.read_text()
        if (re.search(r"\bexec(ute|_driver_sql)\s*\(\s*[f\"']", text)
                or re.search(r"text\s*\(\s*f[\"']", text)
                or re.search(r"\.format\s*\(", text) and "ALTER" in text):
            offenders.append(py.name)
    assert scanned, "the scan matched no files -- it is not actually running"
    assert offenders == [], f"raw/interpolated SQL found in: {offenders}"


def test_schema_backfill_is_bounded_by_the_models():
    """app/db.py's ALTER TABLE must be derived from Base.metadata, never from a
    hand-written list of column names.

    This is what makes the exemption above safe. If the columns came from a
    literal dict, someone could add an entry that does not correspond to any
    model -- and then the one place in the codebase that emits raw DDL would be
    taking its column names from a string that nothing validates. Reading them
    out of the metadata means the set of possible statements is fixed by the
    models at import time.
    """
    source = (Path(__file__).parent.parent / "app" / "db.py").read_text()
    assert "Base.metadata.sorted_tables" in source, (
        "the backfill must iterate the model metadata"
    )
    assert "identifier_preparer" in source, (
        "identifiers must be quoted by the dialect, not by string formatting"
    )
    assert not re.search(r"ADD COLUMN[^\n]*\{[a-z_]*(type|sql)", source, re.I), (
        "column types must be rendered by the dialect's type compiler"
    )


def test_schema_backfill_actually_adds_a_missing_column(tmp_path):
    """The exemption is only worth granting if the code it protects works.

    Simulates the real case it exists for: a database created by an older
    build, missing a column the current models declare.
    """
    from sqlalchemy import create_engine, inspect, text as sa_text

    db_file = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        # An audit_jobs table as an older build would have left it.
        conn.execute(sa_text(
            "CREATE TABLE audit_jobs (id INTEGER PRIMARY KEY, status TEXT)"
        ))

    from app import db as db_module

    original = db_module.engine
    try:
        db_module.engine = engine
        db_module._add_missing_columns()
    finally:
        db_module.engine = original

    columns = {c["name"] for c in inspect(engine).get_columns("audit_jobs")}
    assert "discovery_json" in columns, (
        f"backfill did not add the new column; got {sorted(columns)}"
    )


def test_screenshot_path_cannot_escape_evidence_dir(tmp_path):
    """A malicious adapter site_name must not write outside the evidence dir."""
    evil = SiteAdapter(
        site_name="../../../../tmp/pwned",
        funnel_steps=[FunnelStep("cart", f"file://{XSS_FIXTURE}")],
    )
    evidence_dir = tmp_path / "evidence"
    trace = walk_funnel(evil, screenshot_dir=str(evidence_dir))
    for path in trace.screenshots.values():
        assert os.path.abspath(path).startswith(str(evidence_dir.resolve())), path
    assert not os.path.exists("/tmp/pwned.png")
