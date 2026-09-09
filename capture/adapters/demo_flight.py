"""
Example adapter: a flight-booking funnel (different shape from e-commerce:
search -> results -> seats -> addons -> payment). Demonstrates the adapter
pattern generalizes beyond retail without touching the detection engine.
"""
import os
from .base import SiteAdapter, FunnelStep

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "flight_dark")


def _f(name):
    return "file://" + os.path.abspath(os.path.join(_FIXTURE_DIR, name))


FLIGHT_DARK_DEMO = SiteAdapter(
    site_name="SkyBook (demo)",
    funnel_steps=[
        FunnelStep("results", _f("results.html"), reload_for_urgency_check=True),
        FunnelStep("seats", _f("seats.html")),
        FunnelStep("addons", _f("addons.html")),
        FunnelStep("payment", _f("payment.html")),
    ],
)
