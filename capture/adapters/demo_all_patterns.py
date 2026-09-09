"""
Adapters for the fixtures added in the "all 13 CCPA patterns" build.

Each one exercises detectors that the original four fixtures structurally
could not reach, because those patterns are properties of a JOURNEY: a
recurring charge disclosed one step too late, a popup that returns after
being dismissed, a cancel path measured against a signup path. A single page
cannot demonstrate any of them.

`role="signup"` / `role="cancel"` on a step is what lets DP-05 measure path
asymmetry. There is no reliable way to infer which steps belong to which
path from page content, and guessing would produce exactly the sort of
unfounded finding this project refuses to emit — so the adapter states it,
and an adapter that states nothing simply gets no asymmetry finding.
"""
import os
from .base import SiteAdapter, FunnelStep

_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")


def _f(folder, name):
    return "file://" + os.path.abspath(os.path.join(_FIXTURES, folder, name))


FORCED_ACTION_DEMO = SiteAdapter(
    site_name="TravelGo (forced-action demo)",
    funnel_steps=[
        FunnelStep("booking", _f("forced_action", "signup.html")),
    ],
)


SUBSCRIPTION_TRAP_DEMO = SiteAdapter(
    site_name="StreamBox (subscription-trap demo)",
    funnel_steps=[
        # One step to subscribe...
        FunnelStep("offer", _f("subscription_trap", "offer.html"), role="signup"),
        FunnelStep("payment", _f("subscription_trap", "payment.html")),
        # ...four to cancel. The 4:1 ratio is the finding.
        FunnelStep("account", _f("subscription_trap", "account.html"), role="cancel"),
        FunnelStep("help", _f("subscription_trap", "help.html"), role="cancel"),
        FunnelStep("billing_faq", _f("subscription_trap", "billing.html"), role="cancel"),
        FunnelStep("contact_support", _f("subscription_trap", "contact.html"), role="cancel"),
    ],
)


SAAS_BILLING_DEMO = SiteAdapter(
    site_name="ToolSuite (saas-billing demo)",
    funnel_steps=[
        FunnelStep("plans", _f("saas_billing", "plans.html")),
    ],
)


NAGGING_DEMO = SiteAdapter(
    site_name="DealBazaar (nagging demo)",
    funnel_steps=[
        FunnelStep("home", _f("nagging", "page1.html")),
        FunnelStep("electronics", _f("nagging", "page2.html")),
        FunnelStep("laptops", _f("nagging", "page3.html")),
    ],
)


ROGUE_ALERT_DEMO = SiteAdapter(
    site_name="FreeMoviesHub (rogue-alert demo)",
    funnel_steps=[
        FunnelStep("watch", _f("rogue_alert", "download.html")),
    ],
)


CLEAN_CONTROL_DEMO = SiteAdapter(
    site_name="HonestShop (clean control)",
    funnel_steps=[
        FunnelStep("checkout", _f("clean_control", "checkout.html")),
    ],
)
