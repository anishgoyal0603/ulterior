"""
"Report this page", end to end: the real extension, the real API.

Reporting is the one thing that sends data off the device, so it is held to
the strictest rules in the extension: off by default, a separate browser
permission, the exact payload shown before sending, and nothing sent until
Send is pressed. These tests drive it through the popup against a running
server and then read the report back the way the team would.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from tests.test_ui_end_to_end import _free_port, _stop_server, ROOT  # noqa: E402
from tests.test_extension_in_browser import (  # noqa: E402
    site, results_for, codes, open_popup, AUTO_RUN_DIRS,
)

pytestmark = pytest.mark.ui
KEY = "reports-test-key-1"
DB = {}


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    work = tmp_path_factory.mktemp("reports_api")
    port = _free_port()
    env = dict(os.environ)
    DB["path"] = work / "r.db"
    env.update({"API_KEYS": KEY, "DATABASE_URL": f"sqlite:///{(work / 'r.db').as_posix()}",
                "RATE_LIMIT_DEFAULT_PER_MINUTE": "1000"})
    log = open(work / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    yield base
    _stop_server(proc)
    log.close()


@pytest.fixture(scope="module")
def ctx(api, tmp_path_factory):
    d = tmp_path_factory.mktemp("ext_r") / "extension"
    shutil.copytree(ROOT / "extension", d)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["content_scripts"][0]["matches"] = [f"http://127.0.0.1/{x}/*" for x in AUTO_RUN_DIRS]
    # The test server stands in for the production address. Granting it up
    # front stands in for the person clicking "Allow" on the browser's prompt.
    m["optional_host_permissions"] = [api + "/*"]
    m["host_permissions"] = ["http://127.0.0.1/*"]
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    core = (d / "src" / "core.js").read_text(encoding="utf-8")
    core = re.sub(r'const REPORT_ORIGIN = "[^"]+"', f'const REPORT_ORIGIN = "{api}"', core)
    (d / "src" / "core.js").write_text(core, encoding="utf-8")

    from playwright.sync_api import sync_playwright
    args = [f"--disable-extensions-except={d}", f"--load-extension={d}"]
    profile = tmp_path_factory.mktemp("profile_r")
    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(str(profile), channel="chromium", headless=True, args=args)
        except Exception:
            context = p.chromium.launch_persistent_context(str(profile), headless=False, args=args + ["--headless=new"])
        sw = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        context.sw = sw
        context.ext_id = sw.url.split("/")[2]
        yield context
        context.close()


def _reports(api):
    req = urllib.request.Request(api + "/extension-reports", headers={"X-API-Key": KEY})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def test_reporting_is_off_until_switched_on_and_then_sends_only_what_it_showed(ctx, api, site):
    page = ctx.new_page()
    page.goto(f"{site}/movies/summary.html")
    r = results_for(ctx, "summary.html", want=lambda r: "DP-02" in codes(r))

    popup = open_popup(ctx, r["tabId"])
    assert not popup.is_checked("#opt-report"), "reporting must be off by default"
    assert not popup.is_visible("#report"), "no Report button while reporting is off"
    before = len(_reports(api))

    popup.check("#opt-report")
    popup.wait_for_timeout(400)
    assert popup.is_checked("#opt-report")
    popup.click("#report")
    shown = json.loads(popup.inner_text("#report-json"))
    assert shown["site"] == "127.0.0.1" and "url" not in shown
    assert len(_reports(api)) == before, "nothing may be sent before Send is pressed"

    popup.click("#report-send")
    popup.wait_for_function("document.getElementById('report-status').textContent.includes('Sent')", timeout=8000)
    after = _reports(api)
    assert len(after) == before + 1
    got = after[0]
    assert got["site"] == "127.0.0.1"
    assert [f["code"] for f in got["findings"]] == [f["code"] for f in shown["findings"]]
    assert "summary.html" not in json.dumps(got), "a page address reached the server"
    popup.close()
    page.close()


def test_switching_reporting_off_hides_the_button_again(ctx, site):
    page = ctx.new_page()
    page.goto(f"{site}/movies/summary.html")
    r = results_for(ctx, "summary.html", want=lambda r: "DP-02" in codes(r))
    popup = open_popup(ctx, r["tabId"])
    if popup.is_checked("#opt-report"):
        popup.uncheck("#opt-report")
        popup.wait_for_timeout(300)
    assert not popup.is_visible("#report")
    popup.close()
    page.close()


# ------------------------------------------------- community prices (2.2)

def _insert(key, price, days_ago, n=1):
    import sqlite3
    from datetime import date, timedelta
    con = sqlite3.connect(DB["path"])
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    for _ in range(n):
        con.execute("INSERT INTO price_observations (product_key, site, price, observed_on, created_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))", (key, key.split(":")[0], price, d))
    con.commit()
    con.close()


def _post(api, path, body):
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def _community_on(ctx, tab_id):
    popup = open_popup(ctx, tab_id)
    if not popup.is_checked("#opt-community"):
        popup.check("#opt-community")
        popup.wait_for_timeout(400)
    assert popup.is_checked("#opt-community")
    popup.close()


def test_community_prices_are_off_until_switched_on(ctx, api, site):
    page = ctx.new_page()
    page.goto(f"{site}/shop/compare-a.html")
    results_for(ctx, "compare-a.html", want=lambda r: r.get("price"))
    page.wait_for_timeout(800)
    assert _post(api, "/prices/history", {"key": "127.0.0.1:/shop/compare-a.html"})["daily"] == []
    page.close()


def test_community_prices_are_shared_and_shown_once_switched_on(ctx, api, site):
    key = "127.0.0.1:/shop/product.html"
    for d, p in [(50, 1499), (30, 1449), (12, 1399)]:
        _insert(key, p, d, n=3)
    page = ctx.new_page()
    page.goto(f"{site}/shop/product.html")
    r = results_for(ctx, "shop/product.html", want=lambda r: r.get("price"))
    _community_on(ctx, r["tabId"])
    page.reload()
    r = results_for(ctx, "shop/product.html", want=lambda r: (r.get("price") or {}).get("community", 0) >= 3)
    assert r["price"]["community"] >= 3
    assert r["price"]["verdict"]["kind"] == "good", r["price"]["verdict"]
    page.wait_for_timeout(800)
    today = _post(api, "/prices/history", {"key": key})["daily"][-1]
    assert today["price"] == 1299 and today["reports"] == 1, today
    page.close()


def test_the_six_hourly_check_alerts_from_community_prices(ctx, api, site):
    key = "127.0.0.1:/shop/compare-b.html"
    page = ctx.new_page()
    page.goto(f"{site}/shop/compare-b.html")           # ₹72,500
    r = results_for(ctx, "compare-b.html", want=lambda r: r.get("price"))
    _community_on(ctx, r["tabId"])
    popup = open_popup(ctx, r["tabId"])
    popup.fill("#target", "70000")
    popup.click("#watch")
    popup.wait_for_selector("#watching", state="visible")
    popup.close()
    page.close()
    _insert(key, 69000, 0, n=3)                        # others see it cheaper today
    result = ctx.sw.evaluate("runWatchCheck()")
    assert result["alerts"] == 1, result
    log = ctx.sw.evaluate("chrome.storage.local.get('alerts_log')")["alerts_log"]
    assert log[0]["source"] == "community" and log[0]["price"] == 69000, log[0]
    assert ctx.sw.evaluate("runWatchCheck()")["alerts"] == 0, "one drop, one alert"


def test_the_watchlist_page_shows_community_drops(ctx, api, site):
    key = "127.0.0.1:/shop/app-product.html"
    _insert(key, 2000, 5, n=2)
    _insert(key, 1500, 0, n=2)
    wl = ctx.new_page()
    wl.goto(f"chrome-extension://{ctx.ext_id}/watchlist.html")
    wl.wait_for_selector("#drops-box", state="visible", timeout=8000)
    assert "25% down" in wl.inner_text("#drops")
    wl.close()
