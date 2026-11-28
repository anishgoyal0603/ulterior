"""Example adapter: the CLEAN (no violations) e-commerce fixture, used to
verify the pipeline does not produce false positives on a compliant site."""
from pathlib import Path
import os
from .base import SiteAdapter, FunnelStep

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "ecommerce_clean")


def _f(name):
    return Path(os.path.join(_FIXTURE_DIR, name)).resolve().as_uri()


ECOMMERCE_CLEAN_DEMO = SiteAdapter(
    site_name="FairMart (demo)",
    funnel_steps=[
        FunnelStep("listing", _f("listing.html")),
        FunnelStep("product", _f("product.html")),
        FunnelStep("cart", _f("cart.html")),
        FunnelStep("checkout", _f("checkout.html")),
    ],
)
