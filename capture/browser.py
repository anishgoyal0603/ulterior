"""
The ONE place a crawling browser is launched.

Every audit browser must come up with the same safety settings, so nothing
else in the codebase calls `chromium.launch()` directly (a test enforces
this). If a browser crashes and an audit is retried, the retry goes through
here too, so it can never come back without them.

What it adds on top of the navigation guard (capture/navigation_guard.py):

  * WebRTC is switched off. WebRTC opens its own UDP connections, outside
    the HTTP requests the guard inspects, so a hostile page could use it to
    reach addresses the guard would refuse. Two layers: Chromium is told not
    to send non-proxied UDP, and the page's RTCPeerConnection constructors
    are removed before any of the site's scripts run.
  * WebSockets are routed through the same address check as every other
    request (see NavigationGuard.install). Playwright's normal request
    interception does not see WebSockets at all.
"""
import threading
from contextlib import contextmanager
from typing import Tuple

from playwright.sync_api import sync_playwright

from .navigation_guard import NavigationGuard, install_navigation_guard

# Starting Playwright spawns its driver process. Under uvloop (what uvicorn
# uses on Linux), two threads spawning at the same moment fail with
# "Racing with another loop to spawn a process" -- so two audits started
# together made one of them fail. Starts are serialised; the audits
# themselves still run in parallel.
_START_LOCK = threading.Lock()


@contextmanager
def playwright_session():
    """Use instead of `with sync_playwright() as p:` for any crawl."""
    with _START_LOCK:
        pw = sync_playwright().start()
    try:
        yield pw
    finally:
        pw.stop()

SAFE_CHROMIUM_ARGS = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-features=WebRtcHideLocalIpsWithMdns",
]

# Runs before any script of the audited site, in every frame.
NO_WEBRTC_SCRIPT = """
(() => {
  for (const name of ["RTCPeerConnection", "webkitRTCPeerConnection", "RTCDataChannel",
                      "RTCIceTransport", "RTCSctpTransport"]) {
    try { Object.defineProperty(window, name, { value: undefined, configurable: false, writable: false }); }
    catch (e) { try { window[name] = undefined; } catch (e2) {} }
  }
})();
"""

DEFAULT_VIEWPORT = {"width": 1280, "height": 900}


def launch_browser(playwright, headless: bool = True):
    """Chromium with the audit's safety flags, routed through the egress
    proxy (capture/egress_proxy.py) so every connection is made to an
    address that was checked at connect time. Use this, never chromium.launch()."""
    from app import config
    kwargs = {"headless": headless, "args": list(SAFE_CHROMIUM_ARGS)}
    if config.EGRESS_PROXY:
        from .egress_proxy import shared_proxy
        kwargs["proxy"] = {"server": f"http://127.0.0.1:{shared_proxy().port}"}
    return playwright.chromium.launch(**kwargs)


def new_guarded_page(browser, allow_file: bool = False, viewport: dict = None,
                     trusted_origins=None) -> Tuple[object, NavigationGuard]:
    """A page with WebRTC removed and every request -- including WebSockets
    and redirects -- checked against the SSRF rules."""
    page = browser.new_page(viewport=viewport or DEFAULT_VIEWPORT)
    page.add_init_script(NO_WEBRTC_SCRIPT)
    guard = install_navigation_guard(page, allow_file=allow_file, trusted_origins=trusted_origins)
    return page, guard
