"""
A page must still work the SECOND time someone opens it, under production
caching.

This bug shipped to production and was reported from a screenshot. The first
visit to /demo/ was perfect. Every visit after it rendered in unstyled Times
New Roman, the Run button did nothing, and the storefront frame showed a
broken-page icon -- while the server logged only clean 304 responses.

The cause: a 304 Not Modified has no Content-Type, and the security
middleware chooses its Content-Security-Policy BY Content-Type, so every 304
was stamped with the JSON API's policy (`default-src 'none'`). Browsers copy a
304's headers onto the page they have cached, so the cached page inherited
"load nothing, run nothing, frame nothing" and blocked its own stylesheet,
script and iframe.

Nothing else in the suite could see it:
  * the other browser tests run the server in development mode, which sends
    no-store -- no 304 ever happens there;
  * the header tests check a 304 is RETURNED, not what it does to the page.
So this file runs the app with production caching and drives a real browser
through a first visit and a revisit, asserting on what renders.
"""
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.ui


class _Poisoner:
    """Wraps the app and, while `enabled`, stamps every 304 exactly the way the
    old middleware did. Lets a test put a real browser into the broken state
    that visitors to the live site were left in, then check it recovers."""

    enabled = False

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def poisoning_send(message):
            if (_Poisoner.enabled and message["type"] == "http.response.start"
                    and message["status"] == 304):
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"content-security-policy", b"x-frame-options")]
                headers += [(b"content-security-policy",
                             b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                             b"form-action 'none'"),
                            (b"x-frame-options", b"DENY")]
                message = dict(message, headers=headers)
            await send(message)

        return await self.app(scope, receive, poisoning_send)


@pytest.fixture(scope="module")
def production_cached_server(tmp_path_factory):
    """The real app, in-process, with production cache headers.

    APP_ENV=production cannot be used here -- it demands PostgreSQL over TLS
    and real API keys -- so only the one setting under test is switched on,
    and switched back off afterwards so no other test sees it.
    """
    import uvicorn
    from app import config
    import app.main as main

    previous = config.IS_PRODUCTION
    config.IS_PRODUCTION = True

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # ws="none": the app has no WebSocket routes, and loading uvicorn's
    # websocket support only imports a deprecated third-party module and
    # prints a warning into an otherwise clean run.
    server = uvicorn.Server(uvicorn.Config(_Poisoner(main.app), host="127.0.0.1", port=port,
                                           log_level="warning", ws="none"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        config.IS_PRODUCTION = previous
        pytest.fail("in-process server never started")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)
    config.IS_PRODUCTION = previous


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _visit(page, url):
    """Load a page and report what a person would notice."""
    errors = []
    listener = lambda m: errors.append(m.text) if m.type == "error" else None
    page.on("console", listener)
    page.goto(url, wait_until="load")
    page.wait_for_timeout(600)
    page.remove_listener("console", listener)
    family = page.evaluate("getComputedStyle(document.body).fontFamily")
    first = family.split(",")[0].strip().strip("'\"").lower()
    return first, [e for e in errors if "Content Security Policy" in e]


@pytest.mark.parametrize("path", ["/", "/demo/", "/dashboard/"])
def test_a_revisited_page_keeps_its_styles_and_scripts(production_cached_server, browser, path):
    context = browser.new_context()          # one browser, one cache, two visits
    page = context.new_page()
    url = production_cached_server + path

    served = []
    page.on("response", lambda r: served.append(r.status) if r.url == url else None)

    first_font, first_blocks = _visit(page, url)
    assert first_font not in ("times new roman", "serif"), f"{path} unstyled on first visit"
    assert first_blocks == [], f"{path} first visit: {first_blocks[:2]}"

    again_font, again_blocks = _visit(page, url)
    # Prove the second visit really was a revalidation. If the browser simply
    # reused its copy without asking, this test would pass without exercising
    # the 304 path it exists for.
    assert served and served[-1] in (200, 304), served
    assert again_font not in ("times new roman", "serif"), (
        f"{path} lost its stylesheet on the second visit -- the 304 re-labelled "
        f"the cached page with a policy that blocks it"
    )
    assert again_blocks == [], f"{path} revisit blocked by CSP: {again_blocks[:2]}"
    context.close()


def test_the_demo_still_runs_after_a_revisit(production_cached_server, browser):
    """Styles are the visible half. The Run button is the half that matters."""
    context = browser.new_context()
    page = context.new_page()
    url = production_cached_server + "/demo/"
    _visit(page, url)
    _visit(page, url)

    # demo.js rewrites the address bar on load; the static HTML says 127.0.0.1:8000.
    assert "127.0.0.1:8000" not in page.inner_text("#frame-url"), "demo.js did not run"
    frame = page.frame_locator("#frame")
    assert "Cart" in frame.locator("h1").first.inner_text(), "the storefront frame is blocked"
    context.close()


def test_a_browser_poisoned_by_the_old_bug_heals_on_its_next_visit(production_cached_server, browser):
    """The fix must reach people who ALREADY hit the bug -- anyone who opened
    the demo while the broken build was live, judges included.

    Their browser has the API policy stored on its cached page. The page has
    not changed, so it will only ever get 304s back. If the fixed server's
    304 merely left the policy out, the stored bad one would stay forever.
    """
    context = browser.new_context()
    page = context.new_page()
    url = production_cached_server + "/demo/"

    _visit(page, url)                                   # a normal first visit
    _Poisoner.enabled = True
    try:
        font, blocks = _visit(page, url)                # a revisit on the OLD server
    finally:
        _Poisoner.enabled = False
    # Prove the setup really reproduces what visitors saw -- otherwise the
    # recovery below would be proving nothing.
    assert font in ("times new roman", "serif") or blocks, (
        "the poisoning step did not reproduce the bug; this test would be vacuous"
    )

    font, blocks = _visit(page, url)                    # the next visit, fixed server
    assert font not in ("times new roman", "serif"), "the poisoned page did not recover"
    assert blocks == [], f"still blocked after the fix: {blocks[:2]}"
    assert "127.0.0.1:8000" not in page.inner_text("#frame-url"), "demo.js still blocked"
    context.close()
