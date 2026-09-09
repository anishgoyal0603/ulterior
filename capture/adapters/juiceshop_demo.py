"""
OWASP Juice Shop -- purpose-built as a security/testing target, so it is
the single lowest-legal-risk real external site on this whole project's
list: being crawled aggressively is the entire point of its existence.

CORRECTION to how this was first framed: Juice Shop is actually an
Angular SPA with hash-based routing (#/search, #/basket), not a
traditional server-rendered multi-page site like OpenCart. It still
fits the existing goto()-per-step adapter model reasonably well because
each state has its OWN routed URL (unlike Saleor, where the cart is a
same-URL drawer overlay with no dedicated route) -- navigating directly
to a hash URL causes Angular's router to read it on bootstrap. This is
NOT verified against the live site from this project (network egress
here can't reach it); flagged honestly rather than assumed to work.
"""
from .base import SiteAdapter, FunnelStep

JUICE_SHOP_BASE_URL = "https://demo.owasp-juice.shop"
# For a local Docker container instead, use:
# JUICE_SHOP_BASE_URL = "http://localhost:3000"

JUICE_SHOP_DEMO = SiteAdapter(
    site_name="OWASP Juice Shop",
    funnel_steps=[
        FunnelStep("listing", f"{JUICE_SHOP_BASE_URL}/#/search", wait_for_selector="mat-card"),
        FunnelStep("product", f"{JUICE_SHOP_BASE_URL}/#/search", wait_for_selector="mat-card"),
        FunnelStep("cart", f"{JUICE_SHOP_BASE_URL}/#/basket", wait_for_selector="mat-table, .cdk-table"),
        FunnelStep("checkout", f"{JUICE_SHOP_BASE_URL}/#/order-summary", wait_for_selector="#content"),
    ],
)
