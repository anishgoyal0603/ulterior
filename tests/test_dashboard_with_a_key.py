"""
The dashboard as production serves it: behind an API key.

Every other browser test runs the server with no API_KEYS, where auth is
switched off -- so none of them ever saw what the live dashboard looks like to
someone who opens it without a key. It was reported from a screenshot: an
empty "registered site" dropdown, dashes in every tile, and an "Audit this
site" button that answered 401 five times in the server log while the page
said nothing useful. Nothing was broken; the page just never said "you need a
key". These tests run the real app WITH a key required and drive it the way
a person does: open it, try to audit, get told what to do, connect, and run a
real audit of a pasted URL.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.test_ui_end_to_end import _free_port, _stop_server, Page, ROOT  # noqa: E402

pytestmark = pytest.mark.ui

KEY = "dashboard-test-key-7f3a"


@pytest.fixture(scope="module")
def keyed_server(tmp_path_factory):
    workdir = tmp_path_factory.mktemp("keyed_server")
    log_path = workdir / "server.log"
    port = _free_port()
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # the LLM box must report "not configured"
    env.update({
        "API_KEYS": KEY,
        "ALLOW_LOCAL_TARGETS": "1",
        "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "DATABASE_URL": f"sqlite:///{(workdir / 'keyed.db').as_posix()}",
        "RATE_LIMIT_AUDIT_PER_MINUTE": "100",
        "RATE_LIMIT_DEFAULT_PER_MINUTE": "1000",
    })
    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    import urllib.request
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except Exception:
            if proc.poll() is not None:
                log_file.close()
                pytest.fail("server died on startup:\n" + log_path.read_text(errors="replace")[-3000:])
            time.sleep(0.5)
    yield base
    _stop_server(proc)
    log_file.close()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def dash(keyed_server, browser):
    page = browser.new_page(viewport={"width": 1440, "height": 1200})
    wrapped = Page(page)
    wrapped.audit_posts = []
    page.on("request", lambda r: wrapped.audit_posts.append(r.url)
            if r.method == "POST" and r.url.endswith("/audits") else None)
    page.goto(f"{keyed_server}/dashboard/", wait_until="load")
    page.wait_for_timeout(1200)
    yield wrapped
    page.close()


def _script_errors(page_wrapper):
    """Console errors other than the browser's own "Failed to load resource:
    401" lines, which are the server correctly refusing, not the page failing."""
    return [e for e in page_wrapper.errors
            if "favicon" not in e.lower() and "401" not in e and "Unauthorized" not in e]


def _connect(p, key=KEY):
    p.fill("#api-key", key)
    p.press("#api-key", "Enter")
    p.wait_for_timeout(1500)


def _wait_done(p, timeout_s=150):
    for _ in range(timeout_s * 2):
        s = p.inner_text("#run-status")
        if "Done" in s or "Failed" in s or "failed" in s:
            return s
        p.wait_for_timeout(500)
    return p.inner_text("#run-status")


def test_a_locked_dashboard_says_what_to_do_instead_of_looking_broken(dash):
    p = dash.page
    assert p.is_visible("#auth-gate"), "no explanation that a key is needed"
    assert "API key" in p.inner_text("#auth-gate")
    assert "is-locked" in p.get_attribute("#keycard", "class")
    assert "Connect with your API key" in p.inner_text("#adapter-select")
    # The public parts still load, and no scary error banner is stacked on top.
    assert "13 of 13" in p.inner_text("#coverage-body")
    assert p.query_selector(".conn-error") is None, p.inner_text(".conn-error")
    assert _script_errors(dash) == []


def test_pressing_audit_without_a_key_asks_for_one_and_sends_nothing(dash):
    p = dash.page
    p.fill("#target-urls", "https://example.com/")
    p.click("#run-urls")
    p.wait_for_timeout(600)
    assert "API key" in p.inner_text("#run-status")
    assert dash.audit_posts == [], "a request that could only 401 was sent anyway"
    assert p.evaluate("document.activeElement.id") == "api-key"


def test_a_wrong_key_is_named_as_wrong(dash):
    p = dash.page
    _connect(p, "not-the-key")
    assert "not accepted" in p.inner_text("#key-state")
    assert "is-bad" in p.get_attribute("#keycard", "class")
    assert p.is_visible("#auth-gate")


def test_the_right_key_unlocks_everything(dash):
    p = dash.page
    _connect(p)
    assert "Connected" in p.inner_text("#key-state")
    assert not p.is_visible("#auth-gate")
    assert len(p.query_selector_all("#adapter-select option")) >= 5
    assert p.inner_text("#kpi-audits").strip() not in ("", "—")
    # No ANTHROPIC_API_KEY on this server, so the LLM box must not pretend.
    assert p.is_disabled("#use-llm")
    assert "not configured" in p.inner_text("#llm-note")
    assert _script_errors(dash) == []


def test_a_pasted_url_gets_a_real_audit_with_findings_and_a_walk_report(dash):
    """The whole request, end to end: key in, URL in, real browser walks the
    shop, findings with evidence tiers come back and are shown."""
    p = dash.page
    _connect(p)
    p.click("button.chip[data-try='/storefront/listing.html']")
    assert p.input_value("#target-urls").endswith("/storefront/listing.html")
    p.click("#run-urls")
    status = _wait_done(p)
    assert "Done" in status, status

    report = p.inner_text("#discovery-report")
    assert "Pages walked" in report and "checkout" in report.lower(), report
    table = p.inner_text("#violations-table")
    for code in ("DP-02", "DP-08"):           # provable ones: hidden cost, drip pricing
        assert code in table, f"{code} missing from a real audit:\n{table[:600]}"
    assert "provable" in table.lower()
    assert p.inner_text("#violations-title").startswith("Audit #")
    assert _script_errors(dash) == []


def test_recent_audits_rows_open_their_own_findings(dash):
    p = dash.page
    _connect(p)
    p.select_option("#adapter-select", "hosted_clean_demo")
    p.click("#run-audit")
    assert "Done" in _wait_done(p)
    p.wait_for_timeout(800)
    rows = p.query_selector_all("#jobs-table tr.job-row")
    assert len(rows) >= 2, "need two audits to switch between"
    # The newest (clean shop) is selected; open an older one.
    older = rows[1]
    older_id = older.get_attribute("data-id")
    older.click()
    p.wait_for_timeout(500)
    assert f"Audit #{older_id}" in p.inner_text("#violations-title")
    assert "is-selected" in older.get_attribute("class")


def test_urls_are_normalised_the_way_people_type_them(dash):
    p = dash.page
    norm = lambda u: p.evaluate("u => normaliseUrl(u)", u)
    assert norm("flipkart.com/cart") == "https://flipkart.com/cart"
    assert norm("shop.example.in:8443/x") == "https://shop.example.in:8443/x"
    assert norm("http://example.com") == "http://example.com"
    assert norm("/storefront/cart.html").endswith("/storefront/cart.html")
    assert norm("/storefront/cart.html").startswith("http://127.0.0.1:")
    # A dangerous scheme is NOT rewritten into something harmless-looking:
    # it goes to the server as typed, and the SSRF guard refuses it.
    assert norm("javascript:alert(1)") == "javascript:alert(1)"
    assert norm("file:///etc/passwd") == "file:///etc/passwd"


def test_several_urls_with_discovery_on_are_explained_not_half_run(dash):
    p = dash.page
    _connect(p)
    p.fill("#target-urls", "https://a.example/, https://b.example/")
    p.click("#run-urls")
    p.wait_for_timeout(500)
    assert "ONE URL" in p.inner_text("#run-status")
    assert dash.audit_posts == []
