"""
The Chrome extension, loaded into a real Chromium and walked through
shopping and booking flows.

Version 0.1 of the extension could not be loaded at all: its manifest named
icons/icon16.png, icon48.png and icon128.png, and the icons folder did not
exist, so "Load unpacked" stopped at "Could not load icon". Nothing in the
suite loaded the extension, so nothing noticed. These tests load it the way
a person does and drive it through the flows it exists for.

The shops are fictional pages in tests/extension_fixtures/. They are served
from 127.0.0.1, so the test copy of the extension has its auto-run list
pointed there; the shipped manifest is untouched (and a test checks it never
mentions localhost).
"""
import functools
import http.server
import json
import shutil
import socket
import socketserver
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
EXT = ROOT / "extension"
FIXTURES = Path(__file__).parent / "extension_fixtures"

pytestmark = pytest.mark.ui

AUTO_RUN_DIRS = ["travel", "movies", "shop", "deal", "modal", "xss", "forced", "subs",
                 "search", "nag", "scare", "hindi"]   # not "plain"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(_Quiet, directory=str(FIXTURES))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def ext_dir(tmp_path_factory):
    """A copy of the real extension with the auto-run list pointed at the
    fixture server. Everything else is the shipped code, byte for byte."""
    d = tmp_path_factory.mktemp("ext") / "extension"
    shutil.copytree(EXT, d)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    # 127.0.0.1 and localhost are two different "sites" to the extension,
    # which is what the cross-shop price comparison test needs.
    m["content_scripts"][0]["matches"] = ([f"http://127.0.0.1/{x}/*" for x in AUTO_RUN_DIRS]
                                          + [f"http://localhost/{x}/*" for x in AUTO_RUN_DIRS])
    # Lets the test reach tabs by URL and stand in for the activeTab grant a
    # person gives by clicking the toolbar button.
    m["host_permissions"] = ["http://127.0.0.1/*", "http://localhost/*"]
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def ctx(ext_dir, tmp_path_factory):
    from playwright.sync_api import sync_playwright
    args = [f"--disable-extensions-except={ext_dir}", f"--load-extension={ext_dir}"]
    profile = tmp_path_factory.mktemp("profile")
    with sync_playwright() as p:
        try:
            # Extensions need Chromium's new headless mode.
            context = p.chromium.launch_persistent_context(str(profile), channel="chromium",
                                                           headless=True, args=args)
        except Exception:
            # Older Playwright (the pinned 1.47) has no "chromium" channel;
            # the same new headless mode is reachable through the flag.
            context = p.chromium.launch_persistent_context(str(profile), headless=False,
                                                           args=args + ["--headless=new"])
        sw = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
        context.sw = sw
        context.ext_id = sw.url.split("/")[2]
        yield context
        context.close()


@pytest.fixture
def fresh(ctx):
    """Every test starts with an empty session and no saved history."""
    ctx.sw.evaluate("""async () => {
        await chrome.storage.session.clear();
        await chrome.storage.local.clear();
        await chrome.storage.session.setAccessLevel({accessLevel: 'TRUSTED_AND_UNTRUSTED_CONTEXTS'});
    }""")
    for pg in ctx.pages[1:]:
        pg.close()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    yield page
    page.close()


def results_for(ctx, url_part, wait_ms=6000, want=None):
    """The results the extension stored for the tab whose URL contains
    url_part, polled until `want(results)` is true or time runs out."""
    import time
    deadline = time.time() + wait_ms / 1000
    last = None
    while time.time() < deadline:
        last = ctx.sw.evaluate("""async (part) => {
            const tabs = await chrome.tabs.query({});
            const tab = tabs.find(t => (t.url || '').includes(part));
            if (!tab) return null;
            const got = await chrome.storage.session.get('tab:' + tab.id);
            const r = got['tab:' + tab.id];
            return r ? Object.assign({tabId: tab.id}, r) : {tabId: tab.id, missing: true};
        }""", url_part)
        if last and not last.get("missing") and (want is None or want(last)):
            return last
        time.sleep(0.25)
    return last


def codes(r):
    return sorted(f["code"] for f in (r or {}).get("findings", []))


def open_popup(ctx, tab_id):
    pg = ctx.new_page()
    pg.goto(f"chrome-extension://{ctx.ext_id}/popup.html?tab={tab_id}")
    pg.wait_for_timeout(500)
    return pg


# ------------------------------------------------------------------ loading

def test_the_extension_loads_and_its_worker_runs(ctx):
    assert ctx.ext_id and len(ctx.ext_id) == 32
    assert ctx.sw.evaluate("typeof globalThis.UlteriorCore") == "object"


# ------------------------------------------------------- booking a flight

def test_drip_pricing_is_caught_across_a_flight_booking(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/travel/results.html")
    page.wait_for_timeout(700)
    page.click("#pick")                                    # the ₹4,599 fare
    page.wait_for_url("**/review.html")
    r = results_for(ctx, "review.html", want=lambda r: "DP-08" in codes(r))
    assert "DP-08" in codes(r), r
    drip = next(f for f in r["findings"] if f["code"] == "DP-08")
    assert "₹4,599" in drip["title"] and "₹5,148" in drip["title"], drip
    assert "Convenience fee ₹350" in drip["detail"], drip
    assert drip["tier"] == "corroborated"
    # The insurance was picked for the shopper, and saying no is a guilt trip.
    assert "DP-02" in codes(r) and "DP-03" in codes(r), codes(r)
    assert r["journey"]["start"] == "₹4,599"
    assert page.errors == []


def test_a_countdown_that_restarts_on_reload_is_caught(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/deal/fake-timer.html")
    page.wait_for_timeout(5000)                              # first reading
    r = results_for(ctx, "fake-timer", wait_ms=1000)
    assert "DP-01" not in codes(r), "one visit cannot prove a fake timer"
    page.wait_for_timeout(3000)
    page.reload()
    r = results_for(ctx, "fake-timer", wait_ms=9000, want=lambda r: "DP-01" in codes(r))
    assert "DP-01" in codes(r), r
    f = next(f for f in r["findings"] if f["code"] == "DP-01")
    assert f["tier"] == "corroborated" and "started over" in f["title"]


def test_an_honest_countdown_is_left_alone(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/deal/honest-timer.html")
    page.wait_for_timeout(5000)
    page.wait_for_timeout(3000)
    page.reload()
    page.wait_for_timeout(6000)
    r = results_for(ctx, "honest-timer", wait_ms=1000)
    assert "DP-01" not in codes(r), r["findings"]


# ------------------------------------------------------------ movie tickets

def test_ticket_fees_and_a_pre_ticked_donation_are_caught(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/movies/seats.html")
    page.wait_for_timeout(700)
    page.click("#book")                                  # "Book 2 tickets · ₹500"
    page.wait_for_url("**/summary.html")
    r = results_for(ctx, "summary.html", want=lambda r: "DP-08" in codes(r))
    assert "DP-08" in codes(r), r
    drip = next(f for f in r["findings"] if f["code"] == "DP-08")
    assert "₹500" in drip["title"] and "₹572.80" in drip["title"], drip
    donation = [f for f in r["findings"] if f["code"] == "DP-02"]
    assert donation and "charity" in donation[0]["detail"], r["findings"]


# ------------------------------------------------------------ an honest shop

def test_an_honest_shop_gets_no_findings_and_a_clean_note(ctx, fresh, site):
    """The false-alarm check: MRP struck through, "In stock", EMI text, a
    pre-ticked wallet balance and WhatsApp updates are all normal."""
    page = fresh
    page.goto(f"{site}/shop/product.html")
    page.wait_for_timeout(900)
    r = results_for(ctx, "product.html", want=lambda r: r.get("page") == "product")
    assert r["page"] == "product", r
    assert codes(r) == [], r["findings"]
    assert r["history"]["current"] == "₹1,299", r["history"]   # not the ₹2,499 MRP

    page.click("#buy")
    page.wait_for_url("**/checkout.html")
    r = results_for(ctx, "checkout.html", want=lambda r: r.get("page") == "checkout" and r.get("notes"))
    assert codes(r) == [], r["findings"]
    assert any(n["kind"] == "ok" for n in r["notes"]), r["notes"]


def test_app_style_markup_split_prices_and_custom_checkboxes(ctx, fresh, site):
    """Real shops render with script, split "₹" "799" ".00" into three
    spans, and draw checkboxes as <div role=checkbox>. The checks have to
    read that, not just tidy <input> elements."""
    page = fresh
    page.goto(f"{site}/shop/app-product.html")
    r = results_for(ctx, "app-product.html", want=lambda r: r.get("page") == "product")
    assert r["page"] == "product" and r["history"]["current"] == "₹799", r
    page.click("#go")
    page.wait_for_url("**/app-cart.html")
    r = results_for(ctx, "app-cart.html", want=lambda r: "DP-08" in codes(r) and "DP-02" in codes(r))
    assert "DP-02" in codes(r) and "DP-08" in codes(r), r
    drip = next(f for f in r["findings"] if f["code"] == "DP-08")
    assert "Platform Fee ₹20" in drip["detail"] and "Handling Fee ₹9" in drip["detail"], drip
    assert r["total"]["value"] == "₹863", r["total"]


def test_stock_claims_are_notes_not_findings(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/shop/product.html")
    r = results_for(ctx, "product.html", want=lambda r: r.get("notes"))
    assert codes(r) == []
    assert any("Only 3 left" in n["text"] for n in r["notes"]), r["notes"]


# ---------------------------------------------------- interface interference

def test_a_barely_visible_no_thanks_is_caught(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/modal/insurance.html")
    r = results_for(ctx, "insurance.html", want=lambda r: "DP-06" in codes(r))
    assert "DP-06" in codes(r), r
    f = next(f for f in r["findings"] if f["code"] == "DP-06")
    assert "no thanks" in f["detail"]
    assert float(f["evidence"]["decline_contrast"].split(":")[0]) < 3.0


# ---------------------------------------------------------- price history

def test_price_history_remembers_what_you_saw_and_can_be_cleared(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/shop/product.html?p=1499")
    results_for(ctx, "product.html", want=lambda r: r.get("history"))
    page.goto(f"{site}/shop/product.html?p=1299")
    r = results_for(ctx, "product.html", want=lambda r: (r.get("history") or {}).get("count") == 2)
    h = r["history"]
    assert h["count"] == 2 and h["lowest"] == "₹1,299" and h["highest"] == "₹1,499", h

    stored = ctx.sw.evaluate("chrome.storage.local.get(null)")
    blob = json.dumps(stored)
    assert "Steel water bottle" not in blob, "a product title was stored"
    assert "?p=" not in blob, "a URL with its query string was stored"

    popup = open_popup(ctx, r["tabId"])
    assert "₹1,499" in popup.inner_text("#history-text")
    popup.click("#clear-history")
    popup.wait_for_timeout(300)
    assert ctx.sw.evaluate("chrome.storage.local.get(null)").get("ph_index") is None
    popup.close()


def test_turning_history_off_stops_recording(ctx, fresh, site):
    ctx.sw.evaluate("chrome.storage.local.set({settings: {overlay: true, history: false}})")
    page = fresh
    page.goto(f"{site}/shop/product.html")
    page.wait_for_timeout(1500)
    stored = ctx.sw.evaluate("chrome.storage.local.get(null)")
    assert not any(k.startswith("ph:") for k in stored), stored


# ---------------------------------------------------------- popup & overlay

def test_the_popup_shows_the_findings_and_copies_a_clean_report(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/travel/results.html")
    page.wait_for_timeout(600)
    page.click("#pick")
    page.wait_for_url("**/review.html")
    r = results_for(ctx, "review.html", want=lambda r: "DP-08" in codes(r))
    popup = open_popup(ctx, r["tabId"])
    text = popup.inner_text("body")
    assert "Drip Pricing" in text and "Basket Sneaking" in text
    assert "₹4,599" in popup.inner_text("#journey")
    report = popup.evaluate("""() => UlteriorCore.buildReport('skyhop.example',
        [{code:'DP-08', tier:'corroborated', title:'t', detail:'d', evidence:{a:'b'}}], new Date())""")
    assert "1915" in report and "not a legal determination" in report
    popup.close()


def test_the_on_page_alert_appears_with_the_count(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/movies/summary.html")
    page.wait_for_selector("ulterior-alert", state="attached", timeout=6000)
    assert int(page.get_attribute("ulterior-alert", "data-count")) >= 1
    # A closed shadow root: the shop's scripts cannot read or restyle it.
    assert page.evaluate("document.querySelector('ulterior-alert').shadowRoot") is None


def test_hostile_markup_in_a_label_stays_text(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/xss/page.html")
    r = results_for(ctx, "xss/page.html", want=lambda r: "DP-02" in codes(r))
    assert "<img" in json.dumps(r["findings"]), "the label text should be kept verbatim, as text"
    popup = open_popup(ctx, r["tabId"])
    assert popup.evaluate("window.__pwned") is None
    assert popup.query_selector("#findings img") is None
    assert "<img" in popup.inner_text("#findings")
    popup.close()


# ------------------------------------------------------- unsupported sites

def test_an_unlisted_site_is_not_read_until_you_ask(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/plain/page.html")
    page.wait_for_timeout(1500)
    r = results_for(ctx, "plain/page.html", wait_ms=800)
    assert r is None or r.get("missing"), "the extension read a site that is not on its list"

    popup = open_popup(ctx, r["tabId"])
    assert popup.is_visible("#scan")
    popup.click("#scan")
    r = results_for(ctx, "plain/page.html", want=lambda r: "DP-02" in codes(r))
    assert "DP-02" in codes(r), r
    popup.close()


# ------------------------------------------------- the patterns added in 2.1

def test_forced_action_bundled_consent(ctx, fresh, site):
    fresh.goto(f"{site}/forced/signup.html")
    r = results_for(ctx, "forced/signup.html", want=lambda r: "DP-04" in codes(r))
    f = next(f for f in r["findings"] if f["code"] == "DP-04")
    assert f["tier"] == "corroborated" and "marketing" in f["title"], f


def test_forced_action_app_wall_and_its_honest_twin(ctx, fresh, site):
    fresh.goto(f"{site}/forced/appwall.html")
    r = results_for(ctx, "forced/appwall.html", want=lambda r: "DP-04" in codes(r))
    assert "DP-04" in codes(r), r
    fresh.goto(f"{site}/forced/appwall-ok.html")
    fresh.wait_for_timeout(1800)
    r = results_for(ctx, "appwall-ok.html", wait_ms=1500)
    assert "DP-04" not in codes(r), "a wall with 'Continue on website' is not forced"


def test_subscription_trap_fine_print_and_no_cancel_route(ctx, fresh, site):
    fresh.goto(f"{site}/subs/trial.html")
    r = results_for(ctx, "subs/trial.html", want=lambda r: codes(r).count("DP-05") == 2)
    tiers = sorted(f["tier"] for f in r["findings"] if f["code"] == "DP-05")
    assert tiers == ["corroborated", "indicative"], r["findings"]
    fresh.goto(f"{site}/subs/honest.html")
    fresh.wait_for_timeout(1800)
    r = results_for(ctx, "subs/honest.html", wait_ms=1500)
    assert "DP-05" not in codes(r), r["findings"]


def test_bait_and_switch_price_rises_in_the_cart(ctx, fresh, site):
    fresh.goto(f"{site}/shop/bait-product.html")
    results_for(ctx, "bait-product.html", want=lambda r: r.get("page") == "product")
    fresh.click("#buy")
    fresh.wait_for_url("**/bait-cart.html")
    r = results_for(ctx, "bait-cart.html", want=lambda r: "DP-07" in codes(r))
    f = next(f for f in r["findings"] if f["code"] == "DP-07")
    assert "₹549" in f["title"] and "₹499" in f["title"], f
    assert "DP-08" not in codes(r), "the rise is the item's own price, not a fee"


def test_hard_to_see_sponsored_labels_and_readable_ones(ctx, fresh, site):
    fresh.goto(f"{site}/search/results.html")
    r = results_for(ctx, "search/results.html", want=lambda r: "DP-09" in codes(r))
    assert "2" in next(f for f in r["findings"] if f["code"] == "DP-09")["title"]
    fresh.goto(f"{site}/search/readable.html")
    r = results_for(ctx, "search/readable.html", want=lambda r: r.get("notes"))
    assert "DP-09" not in codes(r)
    assert any("sponsored" in n["text"] for n in r["notes"])


def test_a_pop_up_that_comes_back_after_closing_is_nagging(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/nag/page.html")
    page.wait_for_selector("#pop", state="visible")
    page.wait_for_timeout(2000)                         # let a scan see it
    page.click("#close")
    page.wait_for_timeout(2000)                         # a scan sees it gone
    page.wait_for_selector("#pop", state="visible", timeout=8000)
    r = results_for(ctx, "nag/page.html", wait_ms=8000, want=lambda r: "DP-10" in codes(r))
    f = next(f for f in r["findings"] if f["code"] == "DP-10")
    assert f["evidence"]["shown_again_after_closing"] == "1", f


def test_a_pop_up_shown_once_is_not_nagging(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/nag/page.html")
    page.wait_for_selector("#pop", state="visible")
    page.wait_for_timeout(2500)
    r = results_for(ctx, "nag/page.html", wait_ms=1000)
    assert "DP-10" not in codes(r)


def test_a_virus_scare_with_a_download_is_rogue_malware(ctx, fresh, site):
    fresh.goto(f"{site}/scare/page.html")
    r = results_for(ctx, "scare/page.html", want=lambda r: "DP-13" in codes(r))
    f = next(f for f in r["findings"] if f["code"] == "DP-13")
    assert f["evidence"]["executable_link"] == "cleaner.apk", f
    # Presentation only: never above indicative, as on the server.
    assert f["tier"] == "indicative", f


def test_a_hindi_checkout_is_read_too(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/hindi/product.html")
    r = results_for(ctx, "hindi/product.html", want=lambda r: r.get("page") == "product")
    assert r["history"]["current"] == "₹699", r
    assert any("जल्दी" in n["text"] or "सिर्फ़" in n["text"] for n in r["notes"]), r["notes"]
    page.click("#buy")
    page.wait_for_url("**/hindi/checkout.html")
    r = results_for(ctx, "hindi/checkout.html",
                    want=lambda r: {"DP-02", "DP-03", "DP-08"} <= set(codes(r)))
    assert {"DP-02", "DP-03", "DP-08"} <= set(codes(r)), r
    drip = next(f for f in r["findings"] if f["code"] == "DP-08")
    assert "₹40" in drip["detail"] and "शुल्क" in drip["detail"], drip


def test_the_same_stock_claim_days_apart_becomes_an_indicative_finding(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/shop/product.html")
    results_for(ctx, "shop/product.html", want=lambda r: r.get("history"))
    # Pretend that visit was yesterday.
    ctx.sw.evaluate("""async () => {
        const all = await chrome.storage.local.get(null);
        for (const [k, v] of Object.entries(all)) {
            if (!k.startsWith('ph:') || !v.claims) continue;
            v.claims = v.claims.map(c => Object.assign(c, {t: c.t - 26 * 3600 * 1000}));
            v.pts = v.pts.map(p => Object.assign(p, {t: p.t - 26 * 3600 * 1000}));
            await chrome.storage.local.set({[k]: v});
        }
    }""")
    page.reload()
    r = results_for(ctx, "shop/product.html", want=lambda r: "DP-01" in codes(r))
    f = next(f for f in r["findings"] if f["code"] == "DP-01")
    assert f["tier"] == "indicative" and "Only 3 left" in f["title"], f


def test_price_comparison_across_two_shops(ctx, fresh, site):
    """localhost and 127.0.0.1 are different sites to the extension: the
    same phone seen on both shows up as 'seen elsewhere', by fingerprint."""
    port = site.rsplit(":", 1)[1]
    page = fresh
    page.goto(f"http://localhost:{port}/shop/compare-a.html")
    results_for(ctx, "compare-a.html", want=lambda r: r.get("history"))
    page.goto(f"{site}/shop/compare-b.html")
    r = results_for(ctx, "compare-b.html", want=lambda r: (r.get("history") or {}).get("elsewhere"))
    seen = r["history"]["elsewhere"]
    assert seen and seen[0]["site"] == "localhost" and seen[0]["price"] == "₹69,900", seen
    popup = open_popup(ctx, r["tabId"])
    text = popup.inner_text("#compare")
    assert "localhost" in text and "cheaper" in text, text
    assert popup.query_selector_all("#compare-links a"), "no compare links"
    assert all(a.get_attribute("rel") == "noopener noreferrer" for a in popup.query_selector_all("#compare-links a"))
    blob = json.dumps(ctx.sw.evaluate("chrome.storage.local.get(null)"))
    assert "iPhone" not in blob and "iphone" not in blob, "a product name was stored"
    popup.close()


def test_the_popup_lists_all_thirteen_patterns_honestly(ctx, fresh, site):
    fresh.goto(f"{site}/shop/product.html")
    r = results_for(ctx, "shop/product.html")
    popup = open_popup(ctx, r["tabId"])
    rows = popup.query_selector_all("#coverage li")
    assert len(rows) == 13
    text = popup.text_content("#coverage").lower()   # inside a closed <details>
    assert text.count("partial") == 2, "DP-05 and DP-13 are partial and must say so"
    popup.close()


def test_one_site_cannot_read_another_sites_session_memory(ctx):
    """The background hands a page only its own site's session memory: the
    price you picked on one shop is not readable by another shop's page."""
    allowed = lambda key, url: ctx.sw.evaluate("([k, u]) => allowedKey(k, {url: u, tab: {}})", [key, url])
    assert allowed("journey:makemytrip.com", "https://www.makemytrip.com/flight/review")
    assert allowed("timer:makemytrip.com/flight/review:0", "https://flights.makemytrip.com/x")
    assert not allowed("journey:makemytrip.com", "https://www.goibibo.com/flights")
    assert not allowed("tab:12", "https://www.makemytrip.com/")
    assert not allowed("journey:makemytrip.com.evil.in", "https://www.makemytrip.com/")


def test_diagnostics_name_what_was_read(ctx, fresh, site):
    fresh.goto(f"{site}/travel/results.html")
    fresh.wait_for_timeout(500)
    fresh.click("#pick")
    fresh.wait_for_url("**/review.html")
    r = results_for(ctx, "review.html", want=lambda r: "DP-08" in codes(r))
    popup = open_popup(ctx, r["tabId"])
    text = popup.evaluate("tab => UlteriorCore.buildDiagnostics(tab)", r)
    assert "Total read: ₹5,148" in text and "Convenience fee ₹350" in text and "Page type: checkout" in text
    popup.close()


# ------------------------------------------------------ price tracking (2.2)

def _seed_history(ctx, key, points):
    """points: [(days_ago, price)]"""
    ctx.sw.evaluate("""async ([key, pts]) => {
        const now = Date.now();
        await chrome.storage.local.set({['ph:' + key]: {pts: pts.map(([d, p]) => ({p, t: now - d * 86400000})), fp: [], claims: []},
                                        ph_index: [key]});
    }""", [key, points])


def test_a_product_page_gets_a_price_check_even_with_no_findings(ctx, fresh, site):
    fresh.goto(f"{site}/shop/product.html")
    r = results_for(ctx, "shop/product.html", want=lambda r: r.get("price"))
    assert r["price"]["verdict"]["kind"] == "unknown"
    fresh.wait_for_selector("ulterior-alert[data-mode=price]", state="attached", timeout=5000)
    assert fresh.get_attribute("ulterior-alert", "data-verdict") == "unknown"


def test_history_gives_a_verdict_and_exposes_a_fake_mrp_discount(ctx, fresh, site):
    _seed_history(ctx, "127.0.0.1:/shop/product.html", [(60, 1399), (40, 1349), (20, 1399), (6, 1349)])
    fresh.goto(f"{site}/shop/product.html")          # ₹1,299 now, "M.R.P. ₹2,499"
    r = results_for(ctx, "shop/product.html", want=lambda r: r.get("price") and r["price"]["verdict"]["kind"] != "unknown")
    pr = r["price"]
    assert pr["verdict"]["kind"] == "good" and "Lowest" in pr["verdict"]["label"], pr["verdict"]
    assert pr["mrp"] == 2499
    assert pr["discount"]["kind"] == "warn" and "never sold above ₹1,399" in pr["discount"]["text"], pr["discount"]
    popup = open_popup(ctx, r["tabId"])
    assert "Lowest" in popup.text_content("#verdict")   # drawn uppercase by CSS
    assert popup.query_selector("#chart path.pc-line"), "no price line drawn"
    assert len(popup.query_selector_all("#pricelist li")) == 5, "the same prices must be readable as a list"
    popup.close()


def test_watching_a_price_and_being_alerted_once_per_drop(ctx, fresh, site):
    page = fresh
    page.goto(f"{site}/shop/product.html?p=1499&utm=secret")
    r = results_for(ctx, "shop/product.html", want=lambda r: r.get("price"))
    popup = open_popup(ctx, r["tabId"])
    popup.fill("#target", "1400")
    popup.click("#watch")
    popup.wait_for_selector("#watching", state="visible")
    popup.close()
    item = ctx.sw.evaluate("chrome.storage.local.get('watch:127.0.0.1:/shop/product.html')")["watch:127.0.0.1:/shop/product.html"]
    assert item["target"] == 1400 and item["url"].endswith("/shop/product.html"), item
    assert "secret" not in json.dumps(item), "the watched link kept its query string"

    log = lambda: ctx.sw.evaluate("chrome.storage.local.get('alerts_log')").get("alerts_log") or []
    assert log() == []
    page.goto(f"{site}/shop/product.html?p=1399")         # under target: alert
    results_for(ctx, "shop/product.html", want=lambda r: r.get("price") and r["price"]["current"] == 1399)
    page.wait_for_timeout(800)
    assert len(log()) == 1 and log()[0]["price"] == 1399, log()
    page.reload()                                          # same price: no second alert
    page.wait_for_timeout(2000)
    assert len(log()) == 1, log()
    page.goto(f"{site}/shop/product.html?p=1299")         # lower again: alert
    results_for(ctx, "shop/product.html", want=lambda r: r.get("price") and r["price"]["current"] == 1299)
    page.wait_for_timeout(800)
    assert len(log()) == 2 and log()[0]["price"] == 1299, log()


def test_the_watchlist_page_lists_and_removes(ctx, fresh, site):
    fresh.goto(f"{site}/shop/product.html")
    r = results_for(ctx, "shop/product.html", want=lambda r: r.get("price"))
    popup = open_popup(ctx, r["tabId"])
    popup.fill("#target", "999")
    popup.click("#watch")
    popup.wait_for_selector("#watching", state="visible")
    popup.close()
    wl = ctx.new_page()
    wl.goto(f"chrome-extension://{ctx.ext_id}/watchlist.html")
    wl.wait_for_selector(".item")
    assert "Steel water bottle" in wl.inner_text(".item h2")
    assert "₹999" in wl.inner_text(".item")
    wl.click(".item button:has-text('Remove')")
    wl.wait_for_selector("#empty", state="visible")
    wl.close()


def test_the_price_check_can_be_switched_off(ctx, fresh, site):
    ctx.sw.evaluate("chrome.storage.local.set({settings: {overlay: true, history: true, priceCheck: false}})")
    fresh.goto(f"{site}/shop/product.html")
    results_for(ctx, "shop/product.html", want=lambda r: r.get("price"))
    fresh.wait_for_timeout(800)
    assert fresh.query_selector("ulterior-alert") is None
