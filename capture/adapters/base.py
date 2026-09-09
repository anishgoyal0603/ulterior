"""
SiteAdapter — the ONLY thing that needs writing to onboard a new website.

An adapter is deliberately thin: a name, a list of funnel steps (URL +
human-readable step name + optional wait-for-selector for JS-heavy pages),
and OPTIONAL selector overrides if the site doesn't follow schema.org or
the common data-price/data-name conventions state_extractor.py already
understands out of the box.

This is the actual answer to "make it work for every e-commerce and
flight website": a new target site is a ~15-line file like the ones in
this folder, not a rewrite of the detection engine. What does NOT scale
this way — and no tool from any vendor solves this either — is a site
that actively blocks automated browsers (Cloudflare/PerimeterX challenge
pages). That's a access/legal problem, not a code problem, and is called
out explicitly in the README rather than glossed over.
"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class FunnelStep:
    name: str                       # e.g. "listing", "cart", "checkout"
    url: Optional[str] = None       # None for an action-only SPA step (see below)
    wait_for_selector: Optional[str] = None
    reload_for_urgency_check: bool = False   # capture twice, with a delay, to
                                              # test whether a "live" counter
                                              # actually changes

    # --- SPA support (added for Saleor-style same-URL state changes) ---
    # A traditional step navigates to `url`. An SPA step instead CLICKS an
    # element on the CURRENT page and waits for a resulting DOM mutation --
    # e.g. "click Add to Cart, wait for the cart drawer to appear" -- with
    # no URL change at all. Set click_selector instead of url for this case;
    # funnel_walker only navigates when url is set, and only clicks when
    # click_selector is set. A step may need neither if it's purely a wait
    # (rare) but must set at least one to do anything.
    click_selector: Optional[str] = None
    # Selector to wait for AFTER the click -- typically the drawer/modal
    # that the click is expected to open. Distinct from wait_for_selector,
    # which still applies to the initial page load if url is also set.
    wait_after_click_selector: Optional[str] = None

    # --- Path roles (added for DP-05 Subscription Trap) ---
    # "signup"  -- a step on the path to STARTING a paid/recurring commitment
    # "cancel"  -- a step on the path to ENDING one
    # None      -- an ordinary funnel step, counted in neither path
    #
    # DP-05's core legal idea is asymmetry: trivially easy to start,
    # disproportionately hard to stop. Measuring that needs the adapter to
    # say which steps belong to which path -- there is no reliable way to
    # infer it from page content, and guessing would produce exactly the
    # kind of unfounded finding this project refuses to emit. An adapter
    # that labels neither path simply gets no asymmetry finding.
    role: Optional[str] = None


@dataclass
class SiteAdapter:
    site_name: str
    funnel_steps: List[FunnelStep] = field(default_factory=list)
    price_selector_override: Optional[str] = None
