"""
Waiting until a page has actually rendered.

WHY THIS EXISTS

Both walkers used a fixed `wait_for_timeout(300)` or `(600)` after navigating.
That is fine for a server-rendered page whose HTML arrives complete, which is
what every bundled fixture is. It is far too short for a modern storefront.

A React or Next.js shop -- Saleor, Shopify's newer themes, most headless
storefronts -- serves an empty shell and fills it in after hydration. Capture
300ms in, and the crawler records a page with no price, no line items and no
buttons, then reports "no findings". That is the worst possible failure mode
for this tool: not an error, but a CLEAN BILL OF HEALTH for a site it never
actually looked at.

So this waits for the page to stop changing, rather than for a guessed number
of milliseconds. Two signals, both bounded so a pathological page cannot hang
a worker:

  1. Network idle -- no in-flight requests for a moment. This is what
     finishes after hydration fetches its data.
  2. DOM settle -- the rendered text stops growing between samples. This is
     what catches lazy-loaded product lists that keep appending after the
     network goes quiet.

Neither is a guarantee. A page that polls forever never goes idle, and one
that streams content indefinitely never settles, which is why both are capped
and why the function returns rather than raising: a partially rendered page
still carries evidence worth reading, and the honest response to "it never
settled" is to audit what did arrive, not to abandon the walk.
"""

from typing import Optional

# Bounds. Generous enough for a slow storefront on a slow connection, small
# enough that four concurrent audits cannot pin the worker pool for minutes.
NETWORK_IDLE_TIMEOUT_MS = 8000
SETTLE_POLL_MS = 350
SETTLE_MAX_MS = 6000
SETTLE_STABLE_ROUNDS = 2


def wait_until_rendered(page, minimum_ms: int = 300) -> str:
    """Block until the page looks finished, and say which signal ended the wait.

    Returns one of "networkidle", "settled", "timeout" or "minimum" -- worth
    recording, because a walk that only ever returns "timeout" is a walk whose
    captures should be read with suspicion.
    """
    page.wait_for_timeout(minimum_ms)

    outcome = "minimum"
    try:
        page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
        outcome = "networkidle"
    except Exception:
        # A page that polls (a chat widget, an analytics heartbeat) never goes
        # idle. That is not a failure; fall through to the DOM check.
        outcome = "timeout"

    previous: Optional[int] = None
    stable = 0
    waited = 0
    while waited < SETTLE_MAX_MS:
        try:
            size = page.evaluate(
                "() => (document.body && document.body.innerText || '').length")
        except Exception:
            break
        if previous is not None and size == previous:
            stable += 1
            if stable >= SETTLE_STABLE_ROUNDS:
                return "settled" if outcome != "networkidle" else outcome
        else:
            stable = 0
        previous = size
        page.wait_for_timeout(SETTLE_POLL_MS)
        waited += SETTLE_POLL_MS

    return outcome


def looks_empty(page) -> bool:
    """True when the page rendered almost nothing.

    A crawler that captures an empty shell and reports "no findings" has told
    the user their site is clean when it never saw the site. Callers surface
    this rather than silently producing a reassuring result.
    """
    try:
        text = page.evaluate("() => (document.body && document.body.innerText || '').trim()")
    except Exception:
        return True
    return len(text) < 200
