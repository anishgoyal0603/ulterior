"""
Hosted demo adapters — the same fixtures, walked over real HTTP.

Every other fixture adapter uses `file://` URLs, which is fine for tests but
proves less than it looks: `file://` skips DNS, TLS, redirects, cookies,
caching and every other thing that makes a real page load different from
reading a file off disk. These adapters point at the SAME fixtures once the
app is serving them at `/storefront`, so the crawler takes the identical code
path it would against a live commercial site.

That matters for the public demo: a judge clicking "Run the audit" watches a
real headless Chromium fetch real HTTP pages and produce findings, not a
recorded result. It is also the closest thing to a live-site audit available
until the deployment can reach the public internet.

Why these are registered adapters rather than self-serve `target_urls`: the
URLs come from `config.PUBLIC_BASE_URL`, which is server configuration. A
self-serve audit of a loopback address is rejected by `app/ssrf_guard.py`, and
should be — that is precisely the request an attacker would use to reach the
server's own internal network. An operator auditing a storefront the operator
deployed is a different act, and the registry is where that line is drawn.
"""

from .base import SiteAdapter, FunnelStep
from app import config


def _u(path: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/storefront/{path}"


def _clean(path: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/storefront-clean/{path}"


HOSTED_DARK_DEMO = SiteAdapter(
    site_name="ShopMart (hosted demo storefront)",
    funnel_steps=[
        # reload_for_urgency_check on the first two steps is what makes DP-01
        # provable rather than assumed: the page is loaded twice with real
        # elapsed time in between, and the "only 3 left!" counter is only a
        # violation if it provably did not move.
        FunnelStep("listing", _u("listing.html"), reload_for_urgency_check=True),
        FunnelStep("product", _u("product.html"), reload_for_urgency_check=True),
        FunnelStep("cart", _u("cart.html")),
        FunnelStep("checkout", _u("checkout.html")),
    ],
)


HOSTED_CLEAN_DEMO = SiteAdapter(
    site_name="HonestCart (hosted clean storefront)",
    funnel_steps=[
        FunnelStep("listing", _clean("listing.html")),
        FunnelStep("product", _clean("product.html")),
        FunnelStep("cart", _clean("cart.html")),
        FunnelStep("checkout", _clean("checkout.html")),
    ],
)
