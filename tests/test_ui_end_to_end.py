"""
End-to-end tests that DRIVE THE REAL INTERFACE in a real browser.

Why this file exists separately from the rest of the suite: everything else
here tests functions and HTTP responses. None of it clicks a button. Two full
code-review passes over this project found real defects, and both missed bugs
that only appear when a person actually uses the thing -- a rate-limited
response rendering as a crash, an error banner invisible on a dark surface, a
poll loop parsing an error page as a job. Those were found by using the app,
not by reading it.

So this suite starts the real server, opens the real pages in Chromium, and
presses every control a user can press. It asserts on what appears on screen.

It is slower than the rest of the suite (a real audit takes ~10 seconds) and
is marked `ui` so it can be skipped when iterating:

    pytest tests/ -q -m "not ui"     # fast
    pytest tests/ -q                 # everything

CONSOLE ERRORS ARE FAILURES here. A page that throws in the console still
looks fine in a screenshot, which is exactly how a broken button ships.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.ui

ROOT = Path(__file__).parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    """A real uvicorn, on its own port, with its own database.

    Its own database matters: these tests assert on what the dashboard shows,
    and a developer's local audit history would make those assertions pass or
    fail depending on what they happened to run yesterday.
    """
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "ALLOW_LOCAL_TARGETS": "1",
        "PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "DATABASE_URL": "sqlite:///./test_ui_e2e.db",
        # These tests deliberately press the same button repeatedly.
        "RATE_LIMIT_AUDIT_PER_MINUTE": "100",
        "RATE_LIMIT_DEFAULT_PER_MINUTE": "1000",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            import urllib.request
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except Exception:
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace")
                pytest.fail(f"server died on startup:\n{out[-2000:]}")
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("server never became healthy")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    db = ROOT / "test_ui_e2e.db"
    if db.exists():
        db.unlink()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


class Page:
    """A page plus the console errors it produced, so no test can pass while
    the browser is complaining."""

    def __init__(self, page):
        self.page = page
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(f"PAGEERROR: {e}"))

    def assert_clean(self, context=""):
        # A favicon 404 is noise from the test harness, not the app.
        real = [e for e in self.errors if "favicon" not in e.lower()]
        assert real == [], f"console errors {context}: {real}"


@pytest.fixture
def demo(server, browser):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    wrapped = Page(page)
    page.goto(f"{server}/demo/", wait_until="load")
    page.wait_for_timeout(400)
    yield wrapped
    page.close()


@pytest.fixture
def dashboard(server, browser):
    page = browser.new_page(viewport={"width": 1440, "height": 1200})
    wrapped = Page(page)
    page.goto(f"{server}/dashboard/", wait_until="load")
    page.wait_for_timeout(1200)
    yield wrapped
    page.close()


def _run_demo_audit(page, timeout_s=90):
    page.click("#run")
    for _ in range(timeout_s * 2):
        status = page.inner_text("#status")
        if "Done" in status or "Failed" in status or "Timed" in status:
            return status
        page.wait_for_timeout(500)
    return page.inner_text("#status")


# =========================================================================
# The public demo
# =========================================================================

def test_demo_page_opens_with_everything_visible(demo):
    """Whatever is meant to be read must be on screen before anyone clicks."""
    p = demo.page
    assert p.is_visible("#run")
    assert p.is_visible("#target")
    assert p.is_visible("#frame")
    assert "Ulterior" in p.inner_text("h1")
    # The legal disclaimer is the whole posture of the project and must not be
    # something a reader has to scroll to find.
    assert "not a legal determination" in p.inner_text("body")
    demo.assert_clean("on load")


def test_the_run_button_produces_findings_with_evidence(demo):
    p = demo.page
    status = _run_demo_audit(p)
    assert "Done" in status, f"audit did not finish: {status!r}"

    cards = p.query_selector_all(".finding")
    assert len(cards) >= 5, f"expected several findings, got {len(cards)}"

    # Every finding must carry a code, a tier and the evidence behind it --
    # that is the product's entire claim about itself.
    first = cards[0]
    assert first.query_selector(".code"), "finding has no pattern code"
    assert first.query_selector(".tier"), "finding has no evidence tier"
    assert first.query_selector(".evidence"), "finding shows no evidence"
    demo.assert_clean("after a run")


def test_the_kpi_tiles_stop_showing_dashes_after_a_run(demo):
    p = demo.page
    _run_demo_audit(p)
    total = p.inner_text("#k-total")
    assert total.isdigit() and int(total) > 0, f"findings KPI still {total!r}"
    tiers = [p.inner_text(i) for i in ("#k-prov", "#k-corr", "#k-ind")]
    assert all(t.isdigit() for t in tiers), f"tier KPIs not numeric: {tiers}"
    assert sum(int(t) for t in tiers) == int(total), "tiers do not sum to the total"


def test_the_button_is_disabled_while_running_so_it_cannot_be_double_submitted(demo):
    p = demo.page
    p.click("#run")
    p.wait_for_timeout(300)
    assert p.is_disabled("#run"), "a second click could start a second audit"
    _run_demo_audit(p)
    assert not p.is_disabled("#run"), "button never re-enabled"


def test_switching_storefront_updates_the_frame_its_label_and_the_address(demo):
    """All three have to move together. A stale address bar under a swapped
    page is how a demo accidentally claims to have audited the wrong site."""
    p = demo.page
    p.select_option("#target", "hosted_clean_demo")
    p.wait_for_timeout(600)
    assert "HonestCart" in p.inner_text("#frame-label")
    assert "storefront-clean" in p.inner_text("#frame-url")
    assert "storefront-clean" in p.get_attribute("#frame", "src")

    p.select_option("#target", "hosted_dark_demo")
    p.wait_for_timeout(600)
    assert "ShopMart" in p.inner_text("#frame-label")
    assert "storefront-clean" not in p.inner_text("#frame-url")


def test_the_compliant_storefront_reports_nothing_and_says_why(demo):
    """A tool that only ever finds violations is not measuring anything, and
    the empty state has to say so rather than looking broken."""
    p = demo.page
    p.select_option("#target", "hosted_clean_demo")
    p.wait_for_timeout(400)
    status = _run_demo_audit(p)
    assert "Done" in status, status
    assert p.query_selector_all(".finding") == []
    empty = p.inner_text("#findings").lower()
    assert "no findings" in empty
    assert p.inner_text("#k-total") == "0"
    demo.assert_clean("after a clean run")


def test_the_audited_storefront_actually_renders_inside_the_frame(demo):
    """The iframe is the demo's central claim -- a real page being walked. A
    blank frame reads as a mockup."""
    frame = demo.page.frame_locator("#frame")
    assert frame.locator("h1").first.is_visible()
    assert "Cart" in frame.locator("h1").first.inner_text()


# =========================================================================
# The dashboard
# =========================================================================

def test_dashboard_opens_with_coverage_and_a_chart(dashboard):
    p = dashboard.page
    body = p.inner_text("#coverage-body")
    assert "13 of 13" in body, f"coverage panel did not load: {body[:120]!r}"
    for code in ("DP-01", "DP-08", "DP-13"):
        assert code in body
    dashboard.assert_clean("on load")


def test_every_dashboard_control_is_present_and_enabled(dashboard):
    p = dashboard.page
    for sel in ("#api-key", "#reload", "#target-urls", "#run-urls",
                "#auto-discover", "#adapter-select", "#run-audit", "#use-llm"):
        assert p.is_visible(sel), f"{sel} is missing"
        assert not p.is_disabled(sel), f"{sel} is disabled on load"
    # The adapter dropdown must actually be populated from the API.
    options = p.query_selector_all("#adapter-select option")
    assert len(options) >= 5, f"adapter list looks empty: {len(options)}"


def test_the_connect_button_reloads_without_breaking_the_page(dashboard):
    p = dashboard.page
    p.click("#reload")
    p.wait_for_timeout(1500)
    assert "13 of 13" in p.inner_text("#coverage-body")
    dashboard.assert_clean("after Connect")


def test_run_audit_on_a_registered_adapter_fills_the_table(dashboard):
    p = dashboard.page
    p.select_option("#adapter-select", "hosted_dark_demo")
    p.click("#run-audit")
    for _ in range(180):
        s = p.inner_text("#run-status")
        if "Done" in s or "Failed" in s:
            break
        p.wait_for_timeout(500)
    assert "Done" in p.inner_text("#run-status"), p.inner_text("#run-status")

    rows = p.query_selector_all("#violations-table tr")
    assert len(rows) >= 5, f"violations table has {len(rows)} rows"
    text = p.inner_text("#violations-table")
    assert "DP-" in text
    dashboard.assert_clean("after an adapter audit")


def test_auditing_a_pasted_url_with_discovery_reports_how_it_walked(server, dashboard):
    """The self-serve box plus auto-discovery is the product's main claim:
    one link in, a walked funnel out. The discovery report must appear and
    must name the control it refused to press."""
    p = dashboard.page
    assert p.is_checked("#auto-discover"), "discovery should be on by default"
    p.fill("#target-urls", f"{server}/storefront/listing.html")
    p.click("#run-urls")
    for _ in range(240):
        s = p.inner_text("#run-status")
        if "Done" in s or "Failed" in s:
            break
        p.wait_for_timeout(500)
    assert "Done" in p.inner_text("#run-status"), p.inner_text("#run-status")

    report = p.inner_text("#discovery-report")
    assert "Pages walked" in report, f"no discovery report: {report[:200]!r}"
    assert "checkout" in report.lower()
    assert "refused to click" in report.lower(), "the safety refusal is not surfaced"
    dashboard.assert_clean("after a discovery audit")


def test_pressing_enter_in_the_url_box_starts_the_audit(server, dashboard):
    """The box has a keydown handler. If it ever stops working, people who
    type a URL and hit Enter get silence."""
    p = dashboard.page
    p.fill("#target-urls", f"{server}/storefront/listing.html")
    p.press("#target-urls", "Enter")
    p.wait_for_timeout(1500)
    assert p.inner_text("#run-status").strip() != "", "Enter did nothing"


def test_an_empty_url_box_does_not_start_a_broken_audit(dashboard):
    p = dashboard.page
    p.fill("#target-urls", "   ")
    p.click("#run-urls")
    p.wait_for_timeout(1200)
    status = p.inner_text("#run-status")
    assert "Done" not in status, "an empty box started an audit"


def test_an_unsafe_url_is_refused_with_the_reason_on_screen(dashboard):
    """The SSRF guard's refusal has to reach the user. Silently doing nothing
    looks identical to a broken button."""
    p = dashboard.page
    p.fill("#target-urls", "http://169.254.169.254/latest/meta-data/")
    p.click("#run-urls")
    p.wait_for_timeout(2500)
    shown = (p.inner_text("#run-status") + " " + p.inner_text("body")).lower()
    assert "refus" in shown or "private" in shown or "internal" in shown, \
        f"the refusal never reached the screen: {p.inner_text('#run-status')!r}"


def test_the_chart_draws_real_bars_with_a_legend(dashboard):
    """An SVG that renders zero rects is an empty box that looks like a
    styling choice rather than a failure."""
    p = dashboard.page
    p.select_option("#adapter-select", "hosted_dark_demo")
    p.click("#run-audit")
    for _ in range(180):
        if "Done" in p.inner_text("#run-status"):
            break
        p.wait_for_timeout(500)
    p.wait_for_timeout(1200)

    bars = p.query_selector_all("#patternChart rect[fill]:not([fill='transparent'])")
    assert len(bars) >= 1, "the chart drew no bars"
    for bar in bars:
        w = float(bar.get_attribute("width"))
        assert w > 0, "a bar has non-positive width"
        fill = bar.get_attribute("fill")
        assert fill and fill.startswith("#"), f"bar fill is not a colour: {fill!r}"
    assert p.inner_text("#chart-legend").strip(), "the chart has no legend"


def test_hovering_a_chart_row_shows_a_tooltip_naming_the_pattern(dashboard):
    p = dashboard.page
    p.select_option("#adapter-select", "hosted_dark_demo")
    p.click("#run-audit")
    for _ in range(180):
        if "Done" in p.inner_text("#run-status"):
            break
        p.wait_for_timeout(500)
    p.wait_for_timeout(1200)

    hit = p.query_selector("#patternChart rect[fill='transparent']")
    assert hit is not None, "the chart has no hover targets"
    hit.hover()
    p.wait_for_timeout(300)
    tip = p.query_selector("#chart-tip")
    assert tip.is_visible(), "no tooltip on hover"
    assert "DP-" in tip.inner_text()


def test_the_llm_checkbox_is_off_by_default(dashboard):
    """An audit must behave identically on every machine and send nothing
    off-box unless someone asks."""
    assert not dashboard.page.is_checked("#use-llm")


def test_the_api_key_box_never_persists_the_key(server, dashboard):
    """A key in localStorage survives the tab, and this page is often opened
    on a shared machine during a demo."""
    p = dashboard.page
    p.fill("#api-key", "super-secret-key-value")
    p.click("#reload")
    p.wait_for_timeout(1200)
    stored = p.evaluate(
        "() => JSON.stringify({ls: Object.entries(localStorage), ss: Object.entries(sessionStorage)})"
    )
    assert "super-secret-key-value" not in stored, "the API key was persisted"


# =========================================================================
# Both pages, both themes
# =========================================================================

@pytest.mark.parametrize("path", ["/demo/", "/dashboard/"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_pages_render_in_both_themes_without_console_errors(server, browser, path, scheme):
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, color_scheme=scheme)
    wrapped = Page(page)
    page.goto(f"{server}{path}", wait_until="load")
    page.wait_for_timeout(1500)

    # A transparent body borrows the host's ground and produces dark text on
    # a dark surface. Every page must paint its own.
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), f"{path} has no background in {scheme}"

    # The stylesheet must have applied at all. When the CSS is blocked or
    # missing the browser falls back to its default serif and the whole app
    # renders as Times New Roman -- the exact failure the CSP work fixed.
    # Note "ui-sans-serif" CONTAINS the substring "serif", so this compares
    # whole family names rather than searching for a substring.
    first_family = page.evaluate(
        "getComputedStyle(document.body).fontFamily"
    ).split(",")[0].strip().strip('"\'').lower()
    assert first_family not in ("serif", "times", "times new roman", "georgia"), \
        f"{path} fell back to a serif face ({first_family!r}) -- the stylesheet did not apply"

    wrapped.assert_clean(f"{path} in {scheme}")
    page.close()


@pytest.mark.parametrize("path", ["/demo/", "/dashboard/"])
def test_pages_do_not_scroll_sideways_on_a_narrow_screen(server, browser, path):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(f"{server}{path}", wait_until="load")
    page.wait_for_timeout(1200)
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 2, f"{path} overflows horizontally by {overflow}px on a phone"
    page.close()


# =========================================================================
# Failure and recovery — the paths a demo actually dies on
# =========================================================================

def test_a_failed_poll_does_not_freeze_the_dashboard_forever(server, dashboard):
    """The poll callback awaits a fetch with no error handling. One failed
    tick -- a 500, a dropped connection, a rate limit -- rejects inside
    setInterval, which nothing catches and nothing clears. The interval keeps
    firing, the status stays "Running... 47s" forever and the button stays
    disabled, so the page is dead until it is reloaded. A transient blip must
    not be a permanent hang."""
    p = dashboard.page
    p.route("**/audits/*", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom"}'))

    p.select_option("#adapter-select", "hosted_dark_demo")
    p.click("#run-audit")

    for _ in range(40):
        p.wait_for_timeout(500)
        if not p.is_disabled("#run-audit"):
            break
    assert not p.is_disabled("#run-audit"), \
        "the Run button never came back after a failed poll"
    status = p.inner_text("#run-status") + " " + p.inner_text("body")
    assert "Running" not in p.inner_text("#run-status"), \
        f"status still claims it is running: {p.inner_text('#run-status')!r}"


def test_starting_a_second_audit_does_not_strand_the_first_button(server, dashboard):
    """Both entry points share ONE pollTimer. Starting an adapter audit while
    a URL audit is polling clears the first timer, and the first button's
    re-enable lived only in that timer's callback -- so it stayed disabled for
    the rest of the session."""
    p = dashboard.page
    p.fill("#target-urls", f"{server}/storefront/listing.html")
    p.click("#run-urls")
    p.wait_for_timeout(700)

    p.select_option("#adapter-select", "hosted_dark_demo")
    p.click("#run-audit")

    for _ in range(240):
        s = p.inner_text("#run-status")
        if "Done" in s or "Failed" in s:
            break
        p.wait_for_timeout(500)

    assert not p.is_disabled("#run-urls"), \
        "the first button is still disabled after the second audit finished"
    assert not p.is_disabled("#run-audit")


def test_the_demo_recovers_when_the_server_disappears_mid_poll(server, demo):
    """fetch() rejects on a NETWORK failure rather than resolving with a
    status, so an aborted request escaped the poll callback into setInterval
    where nothing caught it. The page sat at "Walking the funnel... 47s" with
    the button disabled until someone reloaded it."""
    p = demo.page
    p.click("#run")
    p.wait_for_timeout(1200)
    # Kill every poll at the network level, the way a dropped connection does.
    p.route("**/demo-audit/*", lambda route: route.abort())

    for _ in range(40):
        p.wait_for_timeout(500)
        if not p.is_disabled("#run"):
            break
    assert not p.is_disabled("#run"), "the demo button never came back"
    assert "Lost contact" in p.inner_text("#status"), p.inner_text("#status")
