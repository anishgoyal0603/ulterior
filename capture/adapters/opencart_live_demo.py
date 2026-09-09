"""
Real, publicly hosted multi-page e-commerce demo -- OpenCart's official
live demo instance. Explicitly intended for developers to test against;
no ToS/robots.txt concern the way a real commercial site (MMT, Goibibo)
would have. This is config only -- funnel_walker.py needs zero changes,
proving the adapter pattern generalizes to a real external multi-page
site exactly the way it did to the local fixtures.

NOT runnable from this chat sandbox (network egress here is domain-
allowlisted and demo.opencart.com isn't on it) -- runnable once deployed
somewhere with normal outbound internet, e.g. the Railway deployment.
"""
from .base import SiteAdapter, FunnelStep

OPENCART_DEMO = SiteAdapter(
    site_name="OpenCart Live Demo",
    funnel_steps=[
        FunnelStep("listing", "https://demo.opencart.com/index.php?route=product/category&path=20",
                   wait_for_selector=".product-thumb"),
        FunnelStep("product", "https://demo.opencart.com/index.php?route=product/product&product_id=42",
                   wait_for_selector="#product"),
        FunnelStep("cart", "https://demo.opencart.com/index.php?route=checkout/cart",
                   wait_for_selector="#content"),
        FunnelStep("checkout", "https://demo.opencart.com/index.php?route=checkout/checkout",
                   wait_for_selector="#checkout-form, #content"),
    ],
)
