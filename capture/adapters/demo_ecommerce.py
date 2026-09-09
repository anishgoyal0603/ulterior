"""
Example adapter: the local dark-pattern e-commerce demo fixture.
This is the TEMPLATE for onboarding any real e-commerce site — swap the
file:// paths for real https:// URLs and adjust wait_for_selector if the
site is JS-heavy (e.g. React SPA cart that renders after an XHR).
"""
import os
from .base import SiteAdapter, FunnelStep

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "ecommerce_dark")


def _f(name):
    return "file://" + os.path.abspath(os.path.join(_FIXTURE_DIR, name))


ECOMMERCE_DARK_DEMO = SiteAdapter(
    site_name="ShopMart (demo)",
    funnel_steps=[
        FunnelStep("listing", _f("listing.html"), reload_for_urgency_check=True),
        FunnelStep("product", _f("product.html"), reload_for_urgency_check=True),
        FunnelStep("cart", _f("cart.html")),
        FunnelStep("checkout", _f("checkout.html")),
    ],
)
