"""
Walks a real browser (Playwright) through a SiteAdapter's funnel steps,
capturing a PageState at each one, plus a screenshot as evidence. Handles
the "reload and compare" check needed to distinguish genuine urgency
messaging from fabricated urgency (see state_extractor.py's URGENCY_PATTERNS
docstring for why staleness alone is not sufficient evidence).
"""

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import List, Optional

from playwright.sync_api import sync_playwright

from .adapters.base import SiteAdapter
from .navigation_guard import install_navigation_guard
from .page_ready import wait_until_rendered
from .state_extractor import (
    extract_page_state, PageState, MODAL_SELECTORS, modal_signature, _looks_like_dismiss,
)

# Where screenshot evidence is written by default.
#
# This was the literal string "/tmp/dp_evidence", which is fine on Linux and
# macOS and BROKEN ON WINDOWS: Python resolves "/tmp/..." against the current
# drive, so it becomes C:\tmp\dp_evidence — and creating a directory at the
# root of C: needs administrator rights on a normal Windows install. Every
# audit would have failed with a PermissionError before capturing a single
# page, on the machine of anyone who cloned this and ran it on Windows.
#
# tempfile.gettempdir() gives the right answer on all three platforms
# (%LOCALAPPDATA%\Temp, /tmp, or whatever TMPDIR says).
DEFAULT_EVIDENCE_DIR = os.path.join(tempfile.gettempdir(), "dp_evidence")


@dataclass
class FunnelTrace:
    site_name: str
    states: List[PageState] = field(default_factory=list)
    # step_name -> (first_capture, second_capture_after_delay) for urgency checks
    reload_pairs: dict = field(default_factory=dict)
    screenshots: dict = field(default_factory=dict)  # step_name -> file path
    # step_name -> [modal signatures the crawler actively dismissed at that step].
    # This is what turns DP-10 (Nagging) from a guess into a proof: an
    # interruption that reappears at a LATER step, after this crawler already
    # dismissed it, is a repeat request after a refusal -- which is the legal
    # definition of the pattern. Without a record of the dismissal there is
    # nothing to distinguish nagging from a prompt simply being shown once.
    dismissed_modals: dict = field(default_factory=dict)
    # step_name -> declared path role ("signup" / "cancel" / None), copied from
    # the adapter so detectors can measure DP-05 path asymmetry without
    # needing the adapter object itself.
    step_roles: dict = field(default_factory=dict)
    # Set only by capture/funnel_discovery.py: which stages it reached, which
    # it could not find, and why. Reported WITH the findings rather than kept
    # in a log, because an audit that quietly examined one page when the user
    # believed it walked a funnel is worse than one that found nothing.
    discovery: dict = field(default_factory=dict)
    # Requests the navigation guard refused mid-walk (a redirect to an
    # internal address, a link pointing at one). Surfaced rather than logged:
    # a walk that came back thin because it was blocked must not read the same
    # as one that came back thin because the site was clean.
    blocked_requests: List[str] = field(default_factory=list)


def walk_funnel(adapter: SiteAdapter, screenshot_dir: str = DEFAULT_EVIDENCE_DIR,
                 reload_delay_seconds: float = 2.0, headless: bool = True,
                 dismiss_interruptions: bool = True,
                 allow_file_urls: Optional[bool] = None) -> FunnelTrace:
    os.makedirs(screenshot_dir, exist_ok=True)
    trace = FunnelTrace(site_name=adapter.site_name)

    # The pre-flight SSRF check validated the URLs we were GIVEN. It cannot
    # validate a redirect, because the redirect target does not exist yet. The
    # guard below re-checks every request the browser actually makes.
    if allow_file_urls is None:
        allow_file_urls = any((step.url or "").startswith("file://")
                              for step in adapter.funnel_steps)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        guard = install_navigation_guard(page, allow_file=allow_file_urls)

        for step in adapter.funnel_steps:
            if step.url:
                page.goto(step.url)
                if step.wait_for_selector:
                    page.wait_for_selector(step.wait_for_selector, timeout=5000)
                # Wait for the page to stop changing rather than for a guessed
                # number of milliseconds -- a hydrated storefront renders
                # nothing at all in 300ms.
                wait_until_rendered(page)

            # SPA support: click an element on the CURRENT page instead of
            # navigating, then wait for the resulting DOM mutation (e.g. a
            # cart drawer sliding in). This is what Saleor-style storefronts
            # need -- their cart has no dedicated URL to page.goto() to.
            # A step can have url, click_selector, or both (goto then click).
            if step.click_selector:
                page.click(step.click_selector, timeout=5000)
                if step.wait_after_click_selector:
                    page.wait_for_selector(step.wait_after_click_selector, timeout=5000)
                page.wait_for_timeout(300)

            state = extract_page_state(page, step.name, adapter.price_selector_override)
            trace.states.append(state)
            trace.step_roles[step.name] = step.role

            # Filename hardening: site_name comes from a server-registered
            # adapter, not from client input, so this is defence-in-depth
            # rather than a live path-traversal fix. Still worth doing --
            # an adapter is a plain Python file a teammate might add, and
            # a site_name containing "../" would otherwise let a screenshot
            # escape the evidence directory.
            safe_site = re.sub(r"[^A-Za-z0-9._-]", "_", adapter.site_name)[:80]
            safe_step = re.sub(r"[^A-Za-z0-9._-]", "_", step.name)[:40]
            shot_path = os.path.join(screenshot_dir, f"{safe_site}_{safe_step}.png")
            # Confirm the resolved path really is inside the evidence dir.
            if not os.path.abspath(shot_path).startswith(os.path.abspath(screenshot_dir) + os.sep):
                raise ValueError("Refusing to write screenshot outside the evidence directory")
            page.screenshot(path=shot_path, full_page=True)
            trace.screenshots[step.name] = shot_path

            # Dismiss interruptions AFTER the state capture, never before --
            # the modal must appear in the captured evidence, or a later
            # nagging finding would cite a screenshot that doesn't show it.
            if dismiss_interruptions:
                dismissed = _dismiss_interruptions(page, state)
                if dismissed:
                    trace.dismissed_modals.setdefault(step.name, []).extend(dismissed)

            if step.reload_for_urgency_check:
                time.sleep(reload_delay_seconds)
                page.reload()
                if step.wait_for_selector:
                    page.wait_for_selector(step.wait_for_selector, timeout=5000)
                page.wait_for_timeout(300)
                second_state = extract_page_state(page, step.name, adapter.price_selector_override)
                trace.reload_pairs[step.name] = (state, second_state)

        if guard.blocked:

            trace.blocked_requests = list(guard.blocked)

        browser.close()

    return trace


def _dismiss_interruptions(page, state: PageState) -> List[str]:
    """Click the dismissal control on every visible blocking interruption and
    return the signatures of the ones actually dismissed.

    This is the crawler behaving like a user who says no. It matters for two
    detectors: DP-10 needs a refusal on record before a reappearance counts as
    nagging, and DP-04 needs to establish that an interruption genuinely had
    no way out rather than one this crawler simply failed to find.

    Deliberately conservative: only controls whose own text reads as a
    dismissal are clicked. Clicking anything else risks the crawler
    *accepting* an offer -- adding an item to a basket, consenting to
    marketing -- and then reporting the consequences as the site's doing.
    Failures are swallowed per-modal; a stubborn overlay must never abort a
    capture run that has already produced valid evidence.
    """
    target_signatures = {
        m.signature for m in state.modals if m.is_blocking and m.dismiss_controls
    }
    if not target_signatures:
        return []

    dismissed = []
    for el in page.query_selector_all(MODAL_SELECTORS):
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or "").strip()
            signature = modal_signature(
                text, el.get_attribute("id") or "", el.get_attribute("class") or ""
            )
        except Exception:
            continue
        if signature not in target_signatures or signature in dismissed:
            continue

        for ctl in el.query_selector_all("button, a, [role=button], .close, .dismiss"):
            try:
                ctl_text = (ctl.inner_text() or "").strip() or \
                    (ctl.get_attribute("aria-label") or "").strip()
                if not _looks_like_dismiss(ctl_text):
                    continue
                ctl.click(timeout=1500)
                page.wait_for_timeout(200)
                dismissed.append(signature)
                break
            except Exception:
                continue
    return dismissed
