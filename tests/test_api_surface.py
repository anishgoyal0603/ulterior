"""
Tests for the API surface added when the dashboard moved onto the API's own
origin: /coverage, /healthz, the static mount, and the content-type-driven CSP.

The two that matter most are the shadowing test and the CSP test. Both guard
against failures that are invisible in development and total in production:
a static mount registered in the wrong order silently swallows a JSON route,
and a `default-src 'none'` policy silently renders the dashboard as a blank
page with errors only in a browser console nobody is watching.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.middleware import limiter


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client():
    limiter.reset()
    return TestClient(app, raise_server_exceptions=False)


def test_healthz_is_unauthenticated_and_minimal(client):
    """A platform healthcheck runs before any key is configured and must not
    depend on the database or on auth."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_coverage_reports_all_thirteen_with_tiers(client):
    r = client.get("/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["total_patterns"] == 13
    assert body["implemented"] == 13
    for pattern in body["patterns"]:
        assert pattern["tier"] in ("provable", "corroborated", "indicative")
        assert pattern["coverage_note"]


def test_coverage_states_the_confidence_cap_per_tier(client):
    """A coverage claim without its caps invites reading every number as
    equally strong. The endpoint must ship the caps alongside the list."""
    caps = client.get("/coverage").json()["tier_max_confidence"]
    assert caps["provable"] == 1.0
    assert caps["corroborated"] == 0.9
    assert caps["indicative"] == 0.6


def test_static_mount_does_not_shadow_the_dashboard_summary_route(client):
    """/dashboard/summary is a JSON API route; /dashboard/* is a static mount.
    FastAPI matches in definition order, so the mount must be registered last.
    If this ever regresses, the dashboard's own data call starts returning
    404s from a static file handler."""
    r = client.get("/dashboard/summary")
    assert r.status_code == 200
    assert "total_audits" in r.json()


def test_dashboard_html_is_served_from_the_api_origin(client):
    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Ulterior" in r.text


def test_html_responses_get_a_csp_that_permits_the_dashboard(client):
    r = client.get("/dashboard/")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    # connect-src must stay same-origin: this is what stops a compromised
    # dependency exfiltrating audit data to another host.
    assert "connect-src 'self'" in csp
    # 'self', not 'none' — the demo page frames the storefront it audits.
    # External origins are still blocked from framing this app.
    assert "frame-ancestors 'self'" in csp


def test_json_responses_keep_the_strict_deny_everything_csp(client):
    """Relaxing CSP for HTML must not leak into the API responses."""
    for path in ("/", "/healthz", "/coverage", "/dashboard/summary"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert csp.startswith("default-src 'none'"), path
        assert "cdnjs" not in csp, path


def test_use_llm_defaults_to_false(client):
    """An audit must behave identically on every machine and send nothing
    off-box unless explicitly asked. The default is the guarantee."""
    from app.schemas import AuditJobCreate
    assert AuditJobCreate(adapter_name="ecommerce_dark_demo").use_llm is False


def test_unknown_fields_on_audit_creation_are_still_rejected(client):
    """extra='forbid' must survive the new field being added."""
    r = client.post("/audits", json={"adapter_name": "ecommerce_dark_demo", "nope": 1})
    assert r.status_code == 422


# --- Public demo endpoints ------------------------------------------------

def test_public_demo_runs_without_an_api_key(client, monkeypatch):
    """The whole point of the demo link is that a judge or a pilot user with no
    credentials can click it. If this ever requires auth, the demo is dead."""
    r = client.post("/demo-audit", json={"adapter_name": "hosted_clean_demo"})
    assert r.status_code == 200
    assert r.json()["adapter_name"] == "hosted_clean_demo"


def test_public_demo_refuses_any_adapter_outside_its_allowlist(client):
    """The allowlist IS the security boundary. An unauthenticated endpoint that
    launches a headless browser must not be steerable at anything the operator
    did not choose."""
    r = client.post("/demo-audit", json={"adapter_name": "ecommerce_dark_demo"})
    assert r.status_code == 400
    assert "public demo runs only" in r.json()["detail"].lower()


def test_public_demo_accepts_no_urls_at_all(client):
    """No caller-supplied URL may reach the crawler through this route — not
    even one the SSRF guard would have allowed. Narrower input, less to get
    wrong."""
    r = client.post("/demo-audit", json={"target_urls": ["https://example.com"]})
    assert r.status_code == 422


def test_public_demo_readback_cannot_expose_a_real_audit(client):
    """Scoped to demo adapters so this is not an unauthenticated window onto
    audits someone else ran with a real key."""
    real = client.post("/audits", json={"adapter_name": "ecommerce_clean_demo"})
    assert real.status_code == 200
    r = client.get(f"/demo-audit/{real.json()['id']}")
    assert r.status_code == 404


def test_no_html_page_may_execute_third_party_script(client):
    """The dashboard once loaded its chart from a CDN, so the CSP had to let a
    third-party host run code on the origin that renders audit evidence — and
    when that CDN was unreachable the whole page died with it. The chart is
    inline SVG now; this asserts the allowance stays gone."""
    for path in ("/dashboard/", "/demo/"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert "cdnjs" not in csp, path
        assert "http://" not in csp and "https://" not in csp, (
            f"{path} CSP names an external script origin: {csp}"
        )


def test_the_dashboard_loads_no_script_from_another_origin(client):
    """A CSP that forbids third-party script and a page that still requests one
    is a page that half-loads.

    The dashboard's own JS now lives in a same-origin file rather than an
    inline block (see tests/test_frontend_csp_safety.py for why), so the
    property to assert is not "no <script src>" -- it is that every src stays
    on this origin. A relative path cannot leave it; an absolute URL or a
    protocol-relative one can.
    """
    html = client.get("/dashboard/").text
    assert "cdnjs.cloudflare.com" not in html
    for tag in re.findall(r"<script[^>]*\ssrc=\"([^\"]+)\"", html):
        assert not tag.startswith(("http://", "https://", "//")), (
            f"dashboard loads script from another origin: {tag}"
        )


def test_self_serve_urls_are_accepted_by_the_api(client):
    """The dashboard's URL box posts target_urls. This asserts the contract it
    depends on: a list of public URLs is accepted and becomes a job."""
    r = client.post("/audits", json={"target_urls": ["https://example.com/cart"]})
    assert r.status_code == 200, r.text
    assert r.json()["adapter_name"] == "self-serve"


def test_self_serve_still_refuses_a_loopback_url(client):
    """The URL box must not become a way to make the server fetch its own
    internal network. The SSRF guard rejects it before a job is created, and
    the reason comes back readable so the UI can show it."""
    r = client.post("/audits", json={"target_urls": ["http://127.0.0.1:8000/admin"]})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_dashboard_exposes_the_self_serve_control(client):
    """'Paste any URL' was true of the API and false of every screen a person
    could reach. Assert the control exists so that cannot silently regress."""
    html = client.get("/dashboard/").text
    assert 'id="target-urls"' in html
    assert 'id="run-urls"' in html


def test_public_demo_has_no_arbitrary_url_control(client):
    """The unauthenticated demo page must NOT offer it — an open box that
    fetches any URL you type is a crawler-for-hire."""
    html = client.get("/demo/").text
    assert 'id="target-urls"' not in html


def test_evidence_directory_is_not_a_hardcoded_posix_path():
    """The default screenshot directory was the literal "/tmp/dp_evidence".

    On Windows that resolves against the current drive to C:\\tmp\\dp_evidence,
    and creating a directory at the root of C: needs administrator rights on a
    normal install — so every audit failed with a PermissionError before
    capturing a single page. It worked in CI and on every machine we tested,
    which is exactly why it survived to the point where someone ran it on
    their own laptop.
    """
    import tempfile
    from capture.funnel_walker import DEFAULT_EVIDENCE_DIR

    # On Linux gettempdir() IS /tmp, so asserting the value isn't "/tmp/..."
    # would be wrong — what matters is that it is DERIVED, not written down.
    assert DEFAULT_EVIDENCE_DIR.startswith(tempfile.gettempdir())

    source = (Path(__file__).parent.parent / "capture" / "funnel_walker.py").read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert '"/tmp' not in code and "'/tmp" not in code, (
        "a POSIX temp path was hardcoded again in funnel_walker.py"
    )


def test_requirements_contain_no_unimported_packages():
    """Four packages sat in requirements.txt that nothing imports: lxml,
    beautifulsoup4, celery and redis.

    They were not free. lxml has no prebuilt wheel for the newest Python
    releases, so pip fell back to compiling it from C source -- which fails on
    any Windows machine without the Microsoft C++ Build Tools. An unused
    dependency that breaks installation for an entire platform is strictly
    worse than no dependency.
    """
    root = Path(__file__).parent.parent
    # Only the actual requirement lines — the file's own comments explain WHY
    # these were removed, and naming them there must not trip this test.
    requirements = "\n".join(
        line.split("#")[0].strip().lower()
        for line in (root / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    for package in ("lxml", "beautifulsoup4", "celery", "redis", "psycopg2"):
        assert package not in requirements, (
            f"{package} is in requirements.txt but nothing imports it; if it is "
            f"genuinely needed now, import it and update this test"
        )


def test_the_powershell_launcher_is_pure_ascii():
    """Windows PowerShell 5.1 reads .ps1 as ANSI unless the file has a UTF-8
    BOM. An em-dash in a Write-Host string arrived as mojibake and confused
    the parser mid-string, so the script printed its own source at the user.
    Every non-ASCII byte in that file is a chance to do it again."""
    launcher = (Path(__file__).parent.parent / "run_demo.ps1").read_bytes()
    offenders = [(i, launcher[i]) for i in range(len(launcher)) if launcher[i] > 127]
    assert not offenders, (
        f"run_demo.ps1 contains {len(offenders)} non-ASCII byte(s), first at "
        f"offset {offenders[0][0] if offenders else '-'}"
    )


def test_both_launchers_guard_the_python_version():
    """Failing at 'Microsoft Visual C++ 14.0 or greater is required', 200 lines
    into a compiler log, is a terrible way to learn your Python is too new."""
    root = Path(__file__).parent.parent
    assert "not supported" in (root / "run_demo.ps1").read_text()
    assert "not supported" in (root / "run_demo.sh").read_text()


def test_launchers_prefer_an_existing_venv_over_the_system_python():
    """The Windows launcher checked the SYSTEM python first and exited on an
    unsupported version -- even when a perfectly good .venv built with a
    supported one was already sitting there. Its own error message told the
    user to run `py -3.12 -m venv .venv`, and then it refused to use the
    result. Assert the venv check comes first in both launchers.
    """
    root = Path(__file__).parent.parent

    ps1 = (root / "run_demo.ps1").read_text()
    assert ps1.index("Test-Path $VPy") < ps1.index("No supported Python was found"), (
        "run_demo.ps1 bails on the system Python before checking for a usable .venv"
    )

    sh = (root / "run_demo.sh").read_text()
    assert sh.index('if [ -x "$VPY" ]') < sh.index("No supported Python found"), (
        "run_demo.sh bails on the system Python before checking for a usable .venv"
    )


def test_launchers_ask_for_supported_python_versions_by_name():
    """Having 3.14 installed alongside 3.12 must select 3.12. A bare `py -3`
    or `python3` picks the newest, which is the one that does not work."""
    ps1 = (Path(__file__).parent.parent / "run_demo.ps1").read_text()
    assert "$PreferredMinors = @(12, 13, 11, 10)" in ps1

    sh = (Path(__file__).parent.parent / "run_demo.sh").read_text()
    assert "python3.12 python3.13 python3.11 python3.10" in sh


def _code_lines(path):
    """The file's executable lines, with comments and strings blanked out.

    A grep for a deprecated construct hits the comment that EXPLAINS why the
    construct was removed, so the file documenting the fix fails the test
    checking for the fix. Tokenising instead of pattern-matching means the
    explanation can stay in the source where it is useful.
    """
    import io
    import tokenize

    source = path.read_text()
    lines = source.splitlines()
    blanked = list(lines)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            for row in range(tok.start[0], tok.end[0] + 1):
                blanked[row - 1] = ""
    except tokenize.TokenError:  # pragma: no cover - only on unparseable source
        return [line.strip() for line in lines]
    return [line.strip() for line in blanked if line.strip()]


def test_no_deprecated_framework_constructs_remain():
    """The suite used to print 38 warnings. That is not cosmetic.

    A wall of warnings is where a real signal goes to hide -- the line saying
    six tests had SKIPPED sat in the middle of that wall for days, and nobody
    saw it. Each of these is also a genuine time bomb: on_event and the
    class-based Config are both scheduled for removal, and Query.get() is
    legacy in SQLAlchemy 2.x. Down to one warning now, and that one is inside
    Starlette's own code, so it is theirs to fix and not ours to silence.
    """
    root = Path(__file__).parent.parent
    banned = {
        "@app.on_event": "FastAPI removed this; use the lifespan handler in main.py",
        "class Config:": "Pydantic v1 style; use model_config = ConfigDict(...)",
        ").get(job_id)": "legacy SQLAlchemy; use db.get(Model, job_id)",
    }
    for source in ("app/main.py", "app/schemas.py", "app/tasks.py"):
        for construct, why in banned.items():
            offenders = [line for line in _code_lines(root / source) if construct in line]
            assert not offenders, (
                f"{source} still uses {construct!r} -- {why}\n  " + "\n  ".join(offenders)
            )


def test_the_setup_path_installs_the_test_only_dependencies_too():
    """CI installs requirements-dev.txt and the launchers did not.

    The consequence was invisible and worth naming: tests/test_report_language.py
    begins with an importorskip for pdfminer, which is only in
    requirements-dev.txt. On a laptop set up by the launcher that module --
    SIX tests asserting what the generated PDF actually says -- skipped
    silently. The suite printed green while running less of itself than CI
    did, and the wording of a report that names a company is the last thing
    that should be checked only on a server nobody looks at.
    """
    root = Path(__file__).parent.parent
    for launcher in ("run_demo.ps1", "run_demo.sh"):
        assert "requirements-dev.txt" in (root / launcher).read_text(), (
            f"{launcher} does not install requirements-dev.txt, so the report-"
            f"wording tests will skip on every machine set up with it"
        )
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "requirements-dev.txt" in ci, "CI stopped installing the test deps"


def test_launchers_give_pip_enough_time_on_a_slow_connection():
    """pip's default 15-second socket timeout is not enough for the Playwright
    wheel on a slow link -- it dies with ReadTimeoutError halfway through, which
    reads as a broken project rather than a slow download. This is the setup
    step most likely to be someone's first impression, so the generous timeout
    is the default and not a line in a README they reach only after it failed.
    """
    root = Path(__file__).parent.parent
    for name in ("run_demo.sh", "run_demo.ps1"):
        text = (root / name).read_text()
        assert "--timeout" in text and "120" in text, (
            f"{name} leaves pip on its 15-second default timeout"
        )
        assert "--retries" in text, f"{name} does not retry a dropped download"


def test_the_server_reports_missing_demo_assets_instead_of_rendering_wrong(tmp_path, monkeypatch):
    """A missing stylesheet must be a line in the terminal, not a mystery.

    The demo page rendered as unstyled Times New Roman with a broken frame
    where the storefront should be, and nothing anywhere said why. The mounts
    are guarded by os.path.isdir, which silently skips an absent folder -- and
    a folder that merely LOST a file to a partial extraction is worse, because
    the HTML still serves and only the stylesheet 404s.

    Discovering that thirty seconds before a demo is the failure this test
    exists to prevent.
    """
    from app import main

    assert main.missing_static_assets() == [], (
        "this repository is itself missing a demo asset: "
        + "; ".join(main.missing_static_assets())
    )

    empty = tmp_path / "demo"
    empty.mkdir()
    (empty / "index.html").write_text("x")
    (empty / "demo.css").write_text("")          # the interrupted-copy case
    monkeypatch.setitem(main._REQUIRED_ASSETS, "demo",
                        (str(empty), ("index.html", "demo.css", "demo.js")))
    reported = main.missing_static_assets()
    assert any("demo.js is missing" in r for r in reported), reported
    assert any("demo.css is empty" in r for r in reported), reported


def test_demo_assets_are_not_cacheable_in_development():
    """A stale cache can serve a page that was broken two builds ago, and the
    symptom is identical to the bug that was already fixed -- which sends
    everyone hunting in the wrong place. Development sends no-store so that
    cannot happen; production keeps normal caching."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        for path in ("/demo/demo.css", "/demo/demo.js", "/dashboard/dashboard.css"):
            response = client.get(path)
            assert response.status_code == 200, f"{path} -> {response.status_code}"
            assert "no-store" in response.headers.get("cache-control", ""), (
                f"{path} may be cached by a browser: "
                f"{response.headers.get('cache-control')!r}"
            )


def test_every_user_facing_surface_credits_the_team(client):
    """The demo page, the dashboard and the API all carry the same name.

    Three surfaces had three different names -- "Ulterior", "Dark Pattern
    Auditor" and "Dark Pattern Shield" -- which reads to anyone outside the
    project as three different tools. One name, one credit, asserted so it
    cannot drift back apart as pages are edited.
    """
    for path in ("/demo/", "/dashboard/"):
        html = client.get(path).text
        assert "Ulterior" in html, f"{path} does not name the product"
        assert "The Odyssey" in html, f"{path} does not credit the team"

    assert client.get("/healthz").json() == {"status": "ok"}
    from app.main import app as fastapi_app
    assert fastapi_app.title == "Ulterior by The Odyssey"


def test_the_extension_carries_the_same_name():
    root = Path(__file__).parent.parent
    import json as _json
    manifest = _json.loads((root / "extension" / "manifest.json").read_text())
    assert manifest["name"] == "Ulterior by The Odyssey", manifest["name"]
