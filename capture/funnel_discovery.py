"""
Automatic funnel discovery: give it ONE url, and it finds its own way from a
product page to the checkout page, capturing state at every stage.

Why this exists: an adapter (a hand-written list of URLs per site) is precise
but does not scale — you cannot write one for every shop on the internet. The
self-serve API accepted a LIST of URLs, which only helped a person who already
knew every URL in the funnel. Nobody knows that about a site they have not
already studied. Discovery closes the gap between "paste a link" and "audit
the checkout", which is the difference between a tool and a demo.

## The safety rail that matters most

**This module will never click a control that could complete a purchase.**

The whole point is to walk a REAL shop, and a real shop's checkout page has a
button that spends money. `NEVER_CLICK` below is checked before every single
click, and it wins over any intent match. Discovery stops at the checkout page
and reports what it saw there; it does not press the last button.

Getting that wrong would not be a bug, it would be placing an order on a
stranger's card. So the check is not a filter applied at the end — it is the
first thing `_find_control` asks about every candidate.

## What it does not do

It does not solve bot protection, and it does not try to. A site behind
Cloudflare or PerimeterX will serve a challenge page and discovery will
truthfully report that it found no product control. That is an access
question and a terms-of-service question, not an engineering one, and the
honest answer is to audit sites you are allowed to audit.
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from playwright.sync_api import sync_playwright

from .funnel_walker import FunnelTrace, _dismiss_interruptions, DEFAULT_EVIDENCE_DIR
from .navigation_guard import install_navigation_guard
from .page_ready import wait_until_rendered, looks_empty
from .state_extractor import extract_page_state


# --- The one list that must never be got wrong ---------------------------
#
# Any control whose text matches ANY of these is never clicked, whatever else
# it looks like. Ordered roughly by how expensive a mistake would be.
NEVER_CLICK = [
    r"\bpay\b", r"\bpay now\b", r"\bmake payment\b", r"\bconfirm (and )?pay\b",
    r"\bplace (the )?order\b", r"\bconfirm order\b", r"\bcomplete (the )?(order|purchase)\b",
    r"\bbuy now\b",              # on many sites this skips the cart and buys
    r"\bsubscribe\b", r"\bstart (my )?(free )?trial\b", r"\bupgrade\b",
    r"\bdonate\b", r"\bpre-?order\b", r"\bbid\b",
    r"\bdelete\b", r"\bremove\b", r"\bcancel my\b",
    r"\bsign (in|up)\b", r"\blog ?in\b", r"\bregister\b",   # never touch credentials
]

# Ordered stages. Each is a name plus the intents that move you INTO it.
# Discovery walks these in order and skips any stage it cannot find a control
# for, so a URL that is already a cart page still reaches checkout.
# Vocabulary widened after testing against the storefronts people actually
# audit (OpenCart, Saleor, Shopify themes, the automation sandboxes). The
# earlier list was written against our own fixtures and missed the wording
# real shops use -- "Add to Basket", "Go to Bag", "Continue to checkout" --
# so discovery stopped at the first page on sites that were perfectly
# walkable.
STAGES = [
    ("product", [
        r"\bview (product|details|item)\b", r"\bshop now\b", r"\bsee details\b",
        r"\bview more\b", r"\bproduct details\b", r"\bmore details\b",
        r"\bquick view\b", r"\bselect options\b", r"\bview item\b",
    ]),
    ("cart", [
        r"\badd to (cart|bag|basket|trolley|tote)\b", r"\badd item\b",
        r"\badd to my (cart|bag|basket)\b", r"\badd\b(?=.*\bcart\b)",
        r"\bview (cart|bag|basket)\b", r"\bgo to (cart|bag|basket)\b",
        r"\bmy (cart|bag|basket)\b", r"\bopen (cart|bag|basket)\b",
        r"\bshopping (cart|bag|basket)\b",
    ]),
    ("checkout", [
        r"\b(proceed to )?check\s?out\b", r"\bcontinue to (checkout|payment|delivery|shipping)\b",
        r"\bgo to checkout\b", r"\bsecure checkout\b", r"\bcheckout now\b",
        r"\bproceed\b", r"\bcontinue\b",
    ]),
]

# A checkout page often needs one more hop to reveal the full fee breakdown
# (delivery, platform fee) that DP-08 exists to catch. This stage is tried
# once, and only with intents that cannot commit anything.
FINAL_STAGE = ("order_summary", [
    r"\bcontinue to (payment|summary|review)\b", r"\breview (your )?order\b",
    r"\border summary\b", r"\bnext\b",
])


@dataclass
class DiscoveryLog:
    """A record of how discovery went, honest about what it could not do.

    This is surfaced with the audit rather than kept in a server log: a report
    that quietly examined one page, when the user believed it examined a whole
    funnel, would be worse than one that found nothing.
    """
    reached: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"stages_reached": self.reached, "stages_not_found": self.skipped,
                "notes": self.notes}


def _is_forbidden(text: str) -> Optional[str]:
    """Returns the matched forbidden phrase, or None. Checked before every click."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    for pattern in NEVER_CLICK:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0)
    return None


def _note_forbidden_controls(page, log: DiscoveryLog) -> int:
    """Record the purchase-committing controls on this page that we did not press.

    Without this, two very different situations produce the same report: a
    checkout page where the walk correctly stopped one button short of paying,
    and a Cloudflare challenge page where the walk saw nothing at all. Both
    would read as "could not go further". Naming the button we refused is the
    difference between "we reached the end of the safe path" and "we were
    blocked", and only one of those is a limitation.

    Returns how many were found, so the caller can avoid diagnosing bot
    protection on a page that plainly was a checkout.
    """
    try:
        candidates = page.query_selector_all("a[href], button, input[type=submit], [role=button]")
    except Exception:
        return 0

    seen = set()
    for element in candidates:
        try:
            if not element.is_visible():
                continue
            text = (element.inner_text() or "").strip()
            if not text:
                text = (element.get_attribute("value")
                        or element.get_attribute("aria-label") or "").strip()
            if not text or len(text) > 60:
                continue
            forbidden = _is_forbidden(text)
            if not forbidden or text.lower() in seen:
                continue
            seen.add(text.lower())
            log.notes.append(
                f"Refused to click '{text}' - matches the never-click list "
                f"('{forbidden}'), which exists so this tool can never place an "
                f"order, start a subscription, or sign in as somebody."
            )
        except Exception:
            continue
    return len(seen)


def _find_control(page, intents: List[str], log: DiscoveryLog):
    """First visible link or button matching an intent AND not forbidden.

    Intents are tried in order, so `add to cart` beats a generic `continue`
    on a page that has both.
    """
    try:
        candidates = page.query_selector_all("a[href], button, input[type=submit], [role=button]")
    except Exception:
        return None

    for pattern in intents:
        for element in candidates:
            try:
                if not element.is_visible():
                    continue
                text = (element.inner_text() or "").strip()
                if not text:
                    text = (element.get_attribute("value")
                            or element.get_attribute("aria-label") or "").strip()
                if not text or len(text) > 60:
                    continue
                if not re.search(pattern, text.lower()):
                    continue

                if _is_forbidden(text):
                    # Matched what we wanted AND something we must never press.
                    # Skipped silently here on purpose: _note_forbidden_controls
                    # already recorded every refusal on this page when it was
                    # captured, so logging again would just duplicate it.
                    continue
                return element, text
            except Exception:
                continue
    return None


def walk_discovered_funnel(start_url: str, screenshot_dir: str = DEFAULT_EVIDENCE_DIR,
                           reload_delay_seconds: float = 2.0, headless: bool = True,
                           site_name: Optional[str] = None) -> FunnelTrace:
    """Walk from one URL to the checkout, discovering each step.

    Returns the same FunnelTrace every detector already understands, so no
    detector needed changing to support this — discovery produces states, and
    states are all the detectors ever consumed.
    """
    import os
    os.makedirs(screenshot_dir, exist_ok=True)

    log = DiscoveryLog()
    refused_controls = 0
    trace = FunnelTrace(site_name=site_name or (start_url.split("/")[2] if "//" in start_url else start_url))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        # Discovery clicks links it found on the page, so the URLs it visits
        # were never seen by the pre-flight check. A page can offer
        # <a href="http://10.0.0.5:5432/">Proceed to checkout</a>, which
        # matches the checkout intent and is not on the never-click list.
        guard = install_navigation_guard(
            page, allow_file=start_url.startswith("file://"))

        def capture(step_name: str, reload_check: bool = False):
            state = extract_page_state(page, step_name)
            trace.states.append(state)
            trace.step_roles[step_name] = None
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", step_name)[:40]
            shot = os.path.join(screenshot_dir, f"discovered_{safe}.png")
            try:
                page.screenshot(path=shot, full_page=True)
                trace.screenshots[step_name] = shot
            except Exception:
                pass

            nonlocal refused_controls
            refused_controls += _note_forbidden_controls(page, log)

            dismissed = _dismiss_interruptions(page, state)
            if dismissed:
                trace.dismissed_modals.setdefault(step_name, []).extend(dismissed)

            # The urgency check needs the same page loaded twice with real time
            # in between. Only worth doing where scarcity claims actually live.
            if reload_check:
                time.sleep(reload_delay_seconds)
                try:
                    page.reload()
                    wait_until_rendered(page)
                    trace.reload_pairs[step_name] = (
                        state, extract_page_state(page, step_name))
                except Exception:
                    pass
            return state

        try:
            page.goto(start_url, timeout=30000)
            # A React storefront serves an empty shell and fills it in after
            # hydration. Capturing 600ms in records a page with no price and
            # no buttons, and the audit then reports "no findings" -- a clean
            # bill of health for a site the crawler never actually saw.
            wait_until_rendered(page)
        except Exception as exc:
            log.notes.append(f"Could not load the starting URL: {type(exc).__name__}")
            trace.discovery = log.as_dict()
            browser.close()
            return trace

        capture("entry", reload_check=True)
        log.reached.append("entry")

        for stage_name, intents in STAGES + [FINAL_STAGE]:
            found = _find_control(page, intents, log)
            if not found:
                log.skipped.append(stage_name)
                continue
            element, label = found
            try:
                element.click(timeout=8000)
                wait_until_rendered(page)
            except Exception as exc:
                log.notes.append(
                    f"Found '{label}' for the {stage_name} step but the click "
                    f"failed ({type(exc).__name__})."
                )
                log.skipped.append(stage_name)
                continue

            log.notes.append(f"Clicked '{label}' to reach {stage_name}.")
            capture(stage_name, reload_check=(stage_name == "product"))
            log.reached.append(stage_name)

        if looks_empty(page):
            log.notes.append(
                "The page rendered almost no text. On a modern storefront this "
                "usually means the content is drawn by JavaScript that had not "
                "finished, or a challenge page was served instead. Treat the "
                "findings below as covering an empty page, not a clean one."
            )

        if len(trace.states) == 1:
            if refused_controls:
                # Do not diagnose bot protection on a page that was obviously a
                # real checkout -- the walk ended because it was supposed to.
                #
                # The reason is spelled out per control rather than asserted:
                # this said "the next control commits a purchase" on
                # saucedemo.com, where the refused control was a LOGIN button.
                # Refusing to sign in and refusing to pay are both correct and
                # they are not the same fact, and a report that states the
                # wrong one is wrong even when the behaviour was right.
                log.notes.append(
                    "Only the starting page could be examined. The controls that "
                    "would have gone further are on the never-click list -- each "
                    "one is named above with the rule it matched. Findings below "
                    "cover that one page only."
                )
            else:
                log.notes.append(
                    "Only the starting page could be examined. On a real shop this "
                    "usually means bot protection served a challenge page, or the "
                    "controls are rendered by JavaScript this crawler did not wait "
                    "long enough for. Findings below cover that one page only."
                )

        browser.close()

    for note in guard.blocked:
        if note not in log.notes:
            log.notes.append(note)
    trace.discovery = log.as_dict()
    return trace
