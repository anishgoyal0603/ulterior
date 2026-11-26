"""
Take screenshots of every Ulterior page and of the browser extension at work.

    1. Start the server in one window:    .\\run_demo.ps1
    2. In a second window:                 .\\.venv\\Scripts\\python.exe scripts\\capture_screenshots.py

Pictures land in screenshots\\<date-time>\\ . Add --sample-history to also show
the price tracker with 80 days of SAMPLE prices for the bundled practice shop
(ShopMart is fictional, and so is that history -- label it as sample data if
you use those pictures anywhere).

Options:
    --base http://127.0.0.1:8000   where the server is running
    --sample-history               seed sample price history for the practice shop
    --skip-extension               website pages only

It only ever visits the server you point it at. It never visits a real shop.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


def wait_for_text(page, selector, words, timeout_s):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            text = page.inner_text(selector)
        except Exception:
            text = ""
        if any(w in text for w in words):
            return text
        page.wait_for_timeout(500)
    return page.inner_text(selector)


def shot(page, out, name, full=True):
    path = out / f"{name}.png"
    page.screenshot(path=str(path), full_page=full)
    print(f"  saved {path.relative_to(ROOT)}")


def website(p, base, out):
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport=DESKTOP, color_scheme=scheme)
        page = ctx.new_page()
        page.goto(base + "/", wait_until="load")
        page.wait_for_timeout(800)
        # Reveal-on-scroll sections: scroll through once so all of them show.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        shot(page, out, f"01-home-{scheme}")
        ctx.close()

    ctx = browser.new_context(viewport=MOBILE, device_scale_factor=2, is_mobile=True)
    page = ctx.new_page()
    page.goto(base + "/", wait_until="load")
    page.wait_for_timeout(600)
    shot(page, out, "02-home-mobile", full=False)
    ctx.close()

    ctx = browser.new_context(viewport=DESKTOP)
    page = ctx.new_page()
    page.goto(base + "/privacy", wait_until="load")
    shot(page, out, "03-privacy")

    page.goto(base + "/demo/", wait_until="load")
    page.wait_for_timeout(600)
    page.click("#run")
    print("  running the live demo audit (about 10-20 s)...")
    wait_for_text(page, "#status", ["Done", "Failed", "Timed"], 120)
    page.wait_for_timeout(800)
    shot(page, out, "04-demo-dark-shop")
    page.select_option("#target", "hosted_clean_demo")
    page.wait_for_timeout(800)
    page.click("#run")
    wait_for_text(page, "#status", ["Done", "Failed", "Timed"], 120)
    page.wait_for_timeout(800)
    shot(page, out, "05-demo-honest-shop")

    page.goto(base + "/dashboard/", wait_until="load")
    page.wait_for_timeout(1500)
    page.select_option("#adapter-select", "hosted_dark_demo")
    page.click("#run-audit")
    print("  running a dashboard audit...")
    wait_for_text(page, "#run-status", ["Done", "Failed", "failed"], 180)
    page.wait_for_timeout(1500)
    shot(page, out, "06-dashboard-audit")
    chip = page.query_selector("button.chip[data-try='/storefront/listing.html']")
    if chip:
        chip.click()
        page.click("#run-urls")
        print("  auditing a pasted URL with checkout discovery...")
        wait_for_text(page, "#run-status", ["Done", "Failed", "failed"], 240)
        page.wait_for_timeout(1500)
        shot(page, out, "07-dashboard-pasted-url")
    ctx.close()
    browser.close()


def extension(p, base, out, sample_history):
    work = Path(tempfile.mkdtemp(prefix="ulterior-shots-"))
    ext = work / "extension"
    shutil.copytree(ROOT / "extension", ext)
    origin = base.rstrip("/")
    host = origin.split("://", 1)[1].split(":")[0]
    m = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
    # Let the copy run on the local practice shop. The real extension folder
    # is not touched.
    m["content_scripts"][0]["matches"] += [f"http://{host}/storefront/*", f"http://{host}/storefront-clean/*"]
    m["host_permissions"] = [f"http://{host}/*"]
    (ext / "manifest.json").write_text(json.dumps(m), encoding="utf-8")

    args = [f"--disable-extensions-except={ext}", f"--load-extension={ext}"]
    profile = work / "profile"
    try:
        ctx = p.chromium.launch_persistent_context(str(profile), channel="chromium", headless=True,
                                                   viewport={"width": 1280, "height": 800}, args=args)
    except Exception:
        ctx = p.chromium.launch_persistent_context(str(profile), headless=False,
                                                   viewport={"width": 1280, "height": 800},
                                                   args=args + ["--headless=new"])
    sw = None
    for _ in range(60):                       # our worker, not one of the browser's own
        sw = next((w for w in ctx.service_workers if w.url.endswith("/background.js")), None)
        if sw:
            break
        time.sleep(0.25)
    if not sw:
        sys.exit("The extension did not start. Is Chromium installed?  .\\.venv\\Scripts\\python.exe -m playwright install chromium")
    ext_id = sw.url.split("/")[2]

    def tab_id(part):
        for _ in range(40):
            t = sw.evaluate("""async (part) => {
                const tab = (await chrome.tabs.query({})).find(t => (t.url || '').includes(part));
                if (!tab) return null;
                const got = await chrome.storage.session.get('tab:' + tab.id);
                return got['tab:' + tab.id] ? tab.id : null;
            }""", part)
            if t:
                return t
            time.sleep(0.25)
        return None

    def popup(tid, name):
        pp = ctx.new_page()
        pp.set_viewport_size({"width": 380, "height": 900})
        pp.goto(f"chrome-extension://{ext_id}/popup.html?tab={tid}")
        pp.wait_for_timeout(800)
        shot(pp, out, name)
        pp.close()

    def open_panel(page):
        box = page.viewport_size
        page.mouse.click(box["width"] - 110, box["height"] - 28)   # the corner pill
        page.wait_for_timeout(600)

    if sample_history:
        key = f"{host}:/storefront/product.html"
        sw.evaluate("""async (key) => {
            const now = Date.now(), D = 86400000;
            const pts = [[80,349],[66,329],[52,349],[38,319],[24,339],[12,329],[4,319]].map(([d,p]) => ({p, t: now - d*D}));
            await chrome.storage.local.set({['ph:' + key]: {pts, fp: [], claims: []}, ph_index: [key]});
        }""", key)

    page = ctx.new_page()
    page.goto(origin + "/storefront/product.html")
    page.wait_for_timeout(2500)
    open_panel(page)
    shot(page, out, "10-extension-product-price-check", full=False)
    tid = tab_id("storefront/product.html")
    if tid:
        popup(tid, "11-extension-popup-product")

    page.goto(origin + "/storefront/cart.html")
    page.wait_for_timeout(2500)
    open_panel(page)
    shot(page, out, "12-extension-cart-findings", full=False)
    tid = tab_id("storefront/cart.html")
    if tid:
        popup(tid, "13-extension-popup-cart")

    page.goto(origin + "/storefront/checkout.html")
    page.wait_for_timeout(2500)
    open_panel(page)
    shot(page, out, "14-extension-checkout-drip-pricing", full=False)
    tid = tab_id("storefront/checkout.html")
    if tid:
        popup(tid, "15-extension-popup-checkout")

    if sample_history:
        sw.evaluate("""async (key) => {
            await chrome.storage.local.set({['watch:' + key]: {key, site: key.split(':')[0], title: 'Premium Phone Case',
              url: null, target: 309, addedAt: Date.now() - 20*86400000, lastPrice: 299, lastSeen: Date.now(),
              startPrice: 339, alertedPrice: 299}, watch_index: [key]});
        }""", f"{host}:/storefront/product.html")
        wl = ctx.new_page()
        wl.set_viewport_size({"width": 1100, "height": 700})
        wl.goto(f"chrome-extension://{ext_id}/watchlist.html")
        wl.wait_for_timeout(800)
        shot(wl, out, "16-extension-watchlist", full=False)
    ctx.close()
    shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--sample-history", action="store_true")
    ap.add_argument("--skip-extension", action="store_true")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    try:
        urllib.request.urlopen(base + "/healthz", timeout=5)
    except Exception:
        sys.exit(f"Nothing is answering at {base}. Start the server first:  .\\run_demo.ps1")

    out = ROOT / "screenshots" / datetime.now().strftime("%Y-%m-%d_%H-%M")
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        print("Website pages:")
        website(p, base, out)
        if not a.skip_extension:
            print("Browser extension:")
            extension(p, base, out, a.sample_history)
    print(f"\nDone. Open the folder:  {out}")


if __name__ == "__main__":
    main()
