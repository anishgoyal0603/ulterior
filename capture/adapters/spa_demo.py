"""
Local SPA-style demo fixture -- validates the click_selector capability
(added for Saleor-style storefronts) against something real, since the
real Saleor demo isn't reachable from this sandbox's network. The cart
drawer here opens via client-side JS with NO URL change, the same
architectural shape as a real headless-commerce SPA cart.
"""
import os
from .base import SiteAdapter, FunnelStep

_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "spa_ecommerce", "index.html")
_URL = "file://" + os.path.abspath(_FIXTURE)

SPA_DEMO = SiteAdapter(
    site_name="SPA Shop (demo)",
    funnel_steps=[
        FunnelStep("listing", url=_URL, wait_for_selector="#add-to-cart-btn"),
        # No url here at all -- pure click-driven SPA step, proving the
        # capability works without any page navigation whatsoever.
        FunnelStep("cart_drawer", click_selector="#add-to-cart-btn",
                   wait_after_click_selector="#cart-drawer.open"),
    ],
)
