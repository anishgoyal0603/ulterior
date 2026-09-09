"""
Layer 1: deterministic rules. Every function here returns a Violation only
when the evidence is mechanically provable — a price delta, a checkbox's
actual DOM state, a countdown that provably didn't advance. No model, no
confidence interval, no argument possible in a legal dispute.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re

from .taxonomy import BY_CODE, PROVABLE, CORROBORATED, INDICATIVE, clamp_confidence
from .vocab import is_recurring_label
from capture.funnel_walker import FunnelTrace
from capture.state_extractor import PageState, format_money, parse_money


@dataclass
class Violation:
    pattern_code: str
    pattern_name: str
    step_name: str
    confidence: float          # 1.0 for Layer 1 -- provable, not estimated
    evidence: dict
    layer: int
    explanation: str


def _mentions_amount(text: str, amount: float) -> bool:
    """Does this page state this figure, in any of the ways a shop writes it?

    ₹1299 / ₹1,299 / 1299.00 / ₹ 1,299.00 are the same number to a reader, so
    a disclosure check that only matched one of them would call a page silent
    about a total it printed plainly.
    """
    if not text or not amount:
        return False
    whole = int(round(amount))
    variants = {str(whole), f"{whole:,}", f"{amount:.2f}", f"{whole:,}.00"}
    haystack = re.sub(r"[\s\u00a0]", "", text)
    return any(re.sub(r"[\s\u00a0]", "", v) in haystack for v in variants)


# The largest number of rows that can plausibly be ONE order's fee
# breakdown. Above this, the "itemisation" is a product catalogue.
_MAX_BREAKDOWN_ROWS = 12

# The word a page uses when it is telling you what you will pay.
_STATES_A_TOTAL = re.compile(
    r"\b(total|subtotal|sub-total|amount payable|grand total|order total|"
    r"you pay|payable|order summary|amount to pay)\b", re.IGNORECASE)


def _stated_total(state: "PageState") -> Optional[float]:
    """The figure the page itself puts next to the word "total".

    This is the number the shop tells the shopper they will pay, and it is the
    only honest basis for a drip-pricing comparison. Everything else -- the
    first price on the page, the sum of whatever rows a scanner found -- is
    the crawler's inference, and inferences are what produced a PROVABLE
    accusation built from a shipping banner and a product shelf.
    """
    for line in (state.full_text or "").splitlines():
        if _STATES_A_TOTAL.search(line):
            amount = parse_money(line)
            if amount:
                return amount
    return None


def _is_an_order_breakdown(first: "PageState", last: "PageState") -> bool:
    """Is the final step's itemisation this order's costs, or the shop's shelf?

    This is the guard that decides whether DP-08 -- a PROVABLE, 1.0-confidence,
    publicly-named accusation -- may use a summed list of rows as "the total".
    Getting it wrong in the permissive direction is the single most damaging
    thing this codebase can do, and it did it: pointed at a React storefront,
    the crawler read the HOME PAGE product grid at both steps, summed nine
    products on the shelf to $324.98, compared that against a "free shipping
    over $75" banner it had mistaken for the price, and reported the shop for
    concealing $249.98 in charges. Every number in that sentence was furniture.

    Three conditions, all necessary:

      1. The page says what the total IS. A checkout states "Total"; a product
         grid never does. This is the strongest signal and the cheapest.
      2. Few enough rows to be one order's costs, not a catalogue.
      3. The rows are not the same rows the FIRST page showed. Identical
         itemisation across two steps means nothing was added to a basket --
         the crawler is looking at the same site furniture twice.
    """
    if not last.line_items or len(last.line_items) > _MAX_BREAKDOWN_ROWS:
        return False
    if not _STATES_A_TOTAL.search(last.full_text or ""):
        return False
    first_names = {(i.get("name") or "").strip().lower() for i in first.line_items}
    last_names = {(i.get("name") or "").strip().lower() for i in last.line_items}
    if first_names and first_names == last_names:
        return False
    return True


def detect_drip_pricing(trace: FunnelTrace) -> List[Violation]:
    """Compare the earliest disclosed price to the final checkout/payment total.
    A materially higher final total that wasn't disclosed at the first step
    is drip pricing -- DP-08 -- by definition, not by estimation."""
    violations = []
    priced_states = [s for s in trace.states if s.price is not None]
    if len(priced_states) < 2:
        return violations

    first, last = priced_states[0], priced_states[-1]

    # Drip pricing is a claim about ONE purchase measured at two points: what
    # you were quoted, and what you are finally asked to pay. If the last page
    # never states a total, there is no "finally asked to pay" -- there is a
    # number the crawler picked off a page, and comparing it to an earlier
    # number produces a difference that means nothing.
    #
    # Without this, a product grid whose first price happened to exceed the
    # entry price was reported as concealed charges. The guard on the ITEMISED
    # sum was not enough, because the bogus figure can be the page's own price
    # rather than the sum of its rows. A checkout states a total; a shelf does
    # not, and the detector has no business speaking about a shelf.
    if not _STATES_A_TOTAL.search(last.full_text or ""):
        return violations
    # Order of trust: what the page SAYS the total is, then a plausible
    # itemisation, then the page's own price.
    #
    # Both weaker sources have already produced false accusations. A real
    # listing page yielded 36 line items -- one per product on the shelf --
    # which summed to many times anything a shopper would pay; and a React
    # storefront's home grid summed to $324.98 against a "free shipping over
    # $75" banner mistaken for the price. The page's stated total is the one
    # figure the shop itself asserts, so it comes first.
    #
    # Two earlier attempts at this guard were wrong, and both were caught by
    # fixtures rather than by reasoning:
    #
    #   * rejecting an itemisation that overshoots the page's price silenced
    #     DP-08 entirely -- a drip-priced checkout is PRECISELY where the
    #     components exceed the headline figure, so the guard would have
    #     removed the detector it was protecting (compliant corpus caught it);
    #   * rejecting an itemisation identical to the previous step's assumed a
    #     fresh DOM per page. In a single-page app the DOM persists, so
    #     identical rows are normal, and the rule silenced a real finding
    #     (the SPA fixture caught it).
    itemised = sum(li["price"] for li in last.line_items) if last.line_items else None
    final_total = _stated_total(last)
    if final_total is None:
        final_total = last.price
        if itemised is not None and _is_an_order_breakdown(first, last):
            final_total = itemised

    if final_total and first.price and final_total > first.price * 1.02:  # >2% tolerance for rounding
        hidden = round(final_total - first.price, 2)
        # The page's own currency. Printing a rupee sign on a dollar
        # checkout would make every figure in the finding suspect.
        cur = last.price_currency or first.price_currency or ""
        undisclosed = [li for li in last.line_items if li["name"] not in ("", None)
                        and li["price"] > 0 and li["name"].lower() not in first.full_text.lower()]

        # DP-08 is about charges the shop did NOT disclose up front -- that is
        # what "drip" means. The list above already works out which additions
        # were absent from the first page, and the detector then reported the
        # violation whether or not that list was empty.
        #
        # So a shop doing exactly the right thing was reported at PROVABLE 1.0.
        # A page reading "₹640. Delivery ₹40. Total at checkout will be ₹680"
        # has concealed nothing, and every one of those charges appeared in the
        # itemised total it promised. Being told that is a proven dark pattern
        # is worse than being told nothing: it is the tool calling an honest
        # seller deceptive at its highest confidence.
        #
        # Two ways a shop can have disclosed the increase, either sufficient:
        #   * every added line item is named on the first page, or
        #   * the first page states the final total itself.
        if not undisclosed:
            return violations

        stated_total = _mentions_amount(first.full_text, final_total)
        if stated_total:
            return violations

        violations.append(Violation(
            pattern_code="DP-08", pattern_name=BY_CODE["DP-08"].name,
            step_name=f"{first.step_name} -> {last.step_name}",
            confidence=clamp_confidence(PROVABLE, 1.0),
            evidence={
                "evidence_tier": PROVABLE,
                "disclosed_price": first.price, "final_total": final_total,
                "hidden_amount": hidden,
                "undisclosed_line_items": [li["name"] for li in undisclosed],
            },
            layer=1,
            explanation=(f"Price disclosed at '{first.step_name}' was "
                         f"{format_money(first.price, cur)}, but the final total at "
                         f"'{last.step_name}' is {format_money(final_total, cur)} -- "
                         f"{format_money(hidden, cur)} in charges were not shown upfront."),
        ))
    return violations


def detect_basket_sneaking_and_prechecked(trace: FunnelTrace) -> List[Violation]:
    """DP-02 (Basket Sneaking) and DP-12 (SaaS Billing)-style pre-ticked
    add-ons: any checkbox that is ALREADY CHECKED on first page load, before
    the user has touched anything, and that corresponds to a chargeable
    line item, is provable evidence of consent that was never actually given.

    Deduplicated across the whole trace by (checkbox id, label text) -- a
    real bug surfaced by testing the new SPA click_selector capability:
    in an SPA, the DOM persists across steps (no fresh navigation resets
    it), so the SAME physical checkbox element gets captured -- and would
    get flagged -- at every step where it's present, not just once. The
    old multi-page-navigation model never hit this because each step got
    a genuinely fresh DOM. Deduping by identity, not by step, is the
    correct fix regardless of SPA vs multi-page."""
    violations = []
    seen = set()
    for state in trace.states:
        # `if name` matters: one line item with an empty data-name put "" into
        # this set, and `"" in anything` is True, so EVERY pre-ticked control on
        # the page -- "Remember me", "Save this address" -- became a PROVABLE
        # 1.0 basket-sneaking finding. One malformed attribute on the shop's
        # side turned into a page full of accusations.
        chargeable_names = {li["name"].lower() for li in state.line_items
                            if li["price"] > 0 and (li.get("name") or "").strip()}
        for cb in state.checkboxes:
            if cb.is_prechecked:
                # A pre-ticked RECURRING commitment belongs to DP-12 (SaaS
                # Billing), not here. Without this guard a single pre-selected
                # annual plan would be reported twice -- once as basket
                # sneaking, once as SaaS billing -- silently doubling the
                # violation count of every audit that meets one. Inflated
                # counts are the first thing a hostile reviewer takes apart.
                if is_recurring_label(cb.label_text):
                    continue
                matches_charge = any(name in cb.label_text.lower() for name in chargeable_names) \
                    or re.search(r"[₹$€£¥]", cb.label_text) or "insurance" in cb.label_text.lower() \
                    or "protection" in cb.label_text.lower()
                if matches_charge:
                    identity = (cb.id, cb.label_text)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    violations.append(Violation(
                        pattern_code="DP-02", pattern_name=BY_CODE["DP-02"].name,
                        step_name=state.step_name,
                        confidence=clamp_confidence(PROVABLE, 1.0),
                        evidence={"evidence_tier": PROVABLE,
                                  "checkbox_id": cb.id, "label_text": cb.label_text},
                        layer=1,
                        explanation=(f"Checkbox '{cb.label_text}' was already CHECKED on page load "
                                     f"at '{state.step_name}', before any user interaction -- this "
                                     f"is a chargeable add-on consented to on the user's behalf."),
                    ))
    return violations


def detect_fake_urgency(trace: FunnelTrace) -> List[Violation]:
    """DP-01: urgency framing whose claimed VALUE did not move across a reload
    with real elapsed time.

    Three cases, and they are not equally strong. Treating them as one was a
    bug that made this detector accuse honest shops:

    * A COUNTDOWN that shows the same clock after a real delay is fabricated,
      and provably so -- a real countdown must have decreased. CORROBORATED.

    * A SCARCITY COUNT that has not moved is much weaker. A shop that really
      does have two left legitimately still has two left a second later, and
      most shops are not selling something every second. Reporting that at
      0.9 as "fabricated" accuses a truthful seller. INDICATIVE, and worded as
      something a human should check rather than something proven.

    * Urgency language with NO value at all -- "Hurry!", "Limited time offer"
      -- proves nothing and is no longer reported. It used to be: the old
      comparison re-matched the same regex on both loads, so for a phrase
      pattern the two sides were equal by construction and every page saying
      "Hurry" twice produced a 0.9 finding. "In stock: 42 units" -- the honest
      way to state availability -- was reported as a fabricated countdown.

    Deciding what NOT to report is the whole job here. A false positive in
    this tool is a public accusation that a named company is being deceptive.
    """
    violations = []
    for step_name, (first, second) in trace.reload_pairs.items():
        if not first.urgency_phrases_found:
            continue

        first_value = " ".join(sorted(set(
            v for v in (_extract_urgent_value(first.full_text, p)
                        for p in first.urgency_phrases_found) if v
        )))
        second_value = " ".join(sorted(set(
            v for v in (_extract_urgent_value(second.full_text, p)
                        for p in second.urgency_phrases_found) if v
        )))

        # No value at all -> the page made a mood claim, not a factual one.
        # There is nothing to prove stale.
        if not first_value:
            continue

        kind = _urgency_value_kind(first.full_text, first.urgency_phrases_found)

        if kind == "countdown":
            # Do NOT require the two clocks to be byte-identical. A fake timer
            # restarts from its starting value on every load, and the two
            # captures happen at slightly different offsets into the page's
            # life, so a reset timer reads 00:04:58 then 00:04:59 -- close, but
            # not equal, and an equality test missed it. Equality also can't
            # tell "reset" from "frozen".
            #
            # The real question is simpler and timing-proof: a genuine
            # countdown must have gone DOWN over real elapsed time. One that
            # did not has no deadline behind it.
            first_seconds = _clock_to_seconds(first_value)
            second_seconds = _clock_to_seconds(second_value)
            if first_seconds is None or second_seconds is None:
                continue
            if second_seconds < first_seconds:
                continue  # it really is counting down -- honest
        elif first_value != second_value:
            continue      # the quantity moved, so it is not a stale claim

        if kind == "countdown":
            tier, confidence = CORROBORATED, 1.0
            explanation = (
                f"Page shows a countdown ({first.urgency_phrases_found}) reading "
                f"'{first_value}', and after a reload with real elapsed time it "
                f"read '{second_value}' -- it did not go down. A countdown that "
                f"does not count down is not measuring a real deadline."
            )
        elif kind == "count":
            tier, confidence = INDICATIVE, 1.0
            explanation = (
                f"Page pairs urgency language ({first.urgency_phrases_found}) with "
                f"a quantity of '{first_value}' that was unchanged after a reload. "
                f"This is consistent with a fabricated scarcity claim, but a shop "
                f"that genuinely has {first_value} in stock would also show this -- "
                f"a human should confirm before treating it as a finding."
            )
        else:
            continue

        violations.append(Violation(
            pattern_code="DP-01", pattern_name=BY_CODE["DP-01"].name,
            step_name=step_name,
            confidence=clamp_confidence(tier, confidence),
            evidence={
                "evidence_tier": tier,
                "claim_kind": kind,
                "urgency_language": first.urgency_phrases_found,
                "first_load_value": first_value,
                "second_load_value_after_delay": second_value,
            },
            layer=1,
            explanation=explanation,
        ))
    return violations


# A countdown reads as a clock; a scarcity claim reads as a bare count. They
# are DIFFERENT strengths of evidence and DP-01 now treats them differently,
# so they are extracted separately.
_CLOCK_RE = re.compile(r"\b\d{1,3}\s*:\s*\d{2}(?:\s*:\s*\d{2})?\b")
_COUNT_RE = re.compile(r"\b\d{1,6}\b")


def _clock_to_seconds(value: str):
    """'00:04:59' -> 299. Returns None if it is not a clock."""
    parts = value.split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        numbers = [int(x) for x in parts]
    except ValueError:
        return None
    seconds = 0
    for n in numbers:
        seconds = seconds * 60 + n
    return seconds


# Only these urgency phrases ASSERT A DEADLINE. The distinction matters more
# than it looks: a real flight results page reads
#
#     "Hurry! Only 3 seats left    07:15 DEL   02h 15m   09:30 BOM"
#
# and 07:15 is when the aircraft departs, not a countdown. Looking ahead for a
# clock after ANY urgency phrase turned that departure time into a "countdown",
# and a departure time is identical on every reload -- so every airline seat
# page carrying a seats-left message was reported at CORROBORATED 0.9 for
# "a countdown that does not count down". A clock is only a countdown when the
# words next to it claim a deadline.
_DEADLINE_PATTERNS = (
    "offer ends", "sale ends", "limited time", "ends in", "expires",
    "deal ends", "hurry",
)

# Digits that belong to a PRICE are not a scarcity count. "Rs 7,475 Only 2
# seats left" was yielding the value "2 7" -- the 7 being the first digit of
# the fare -- which is meaningless as evidence and can differ between loads
# purely because the price changed.
_CURRENCY_LEAD = re.compile(r"(?:[₹$€£]|\brs\.?|\binr\b)\s*[\d,]*$", re.IGNORECASE)


def _is_deadline_pattern(pattern: str) -> bool:
    lowered = pattern.lower()
    return any(word in lowered for word in _DEADLINE_PATTERNS)


def _count_in(span: str) -> str:
    """The scarcity count inside this span, ignoring any currency amount.

    Takes the LAST qualifying number, because scarcity phrasing puts the count
    immediately before the depletion word ("only 3 seats left"), while a price
    that leaked into the span sits at the front.
    """
    best = ""
    for m in re.finditer(r"\b(\d{1,4})\b", span):
        before = span[:m.start()]
        if _CURRENCY_LEAD.search(before):
            continue                      # part of a price
        if m.start() > 0 and span[m.start() - 1] == ",":
            continue                      # a comma group: the 475 of 7,475
        best = m.group(1)
    return best


def _extract_urgent_value(text: str, pattern: str) -> str:
    """The VALUE an urgency claim is making, or "" when it is making none.

    This used to return the matched span itself, which made DP-01's central
    comparison vacuous: for a phrase pattern like `hurry` the span is the
    literal word "hurry" on every load, so first == second was true BY
    CONSTRUCTION and any page saying "Hurry" twice was reported at 0.9 as
    having a fabricated countdown. An honest "Sale ends Sunday. Hurry!" was
    indistinguishable from a fake timer.

    DP-01's premise is "a value that PROVABLY did not change". A claim with no
    value in it cannot support that premise, so it now yields "" and the
    caller emits nothing.

    The window extends a little past the match because the number often sits
    just after the phrase ("Offer ends in: 00:04:59", "Only 2 left").
    """
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return ""
    # A clock may sit just past the phrase ("Offer ends in: 00:04:59"), so
    # look a little way ahead -- but ONLY when this phrase claims a deadline.
    # See _DEADLINE_PATTERNS: next to a seats-left message, a clock is a
    # departure time.
    if _is_deadline_pattern(pattern):
        clock = _CLOCK_RE.search(text[m.start():m.end() + 24])
        if clock:
            return clock.group(0).replace(" ", "")
    # A COUNT, though, must be inside the phrase itself ("only 2 left").
    # Reading ahead for it swept up any number that happened to follow --
    # "Hurry, offer ends 15 August" yielded "15" and a real sale with a real
    # calendar date was reported as static fake scarcity. A date is not a
    # depleting quantity, and the scarcity patterns that matter all bind the
    # number to a depletion word inside the match.
    return _count_in(m.group(0))


def _urgency_value_kind(text: str, patterns: List[str]) -> str:
    """"countdown" if any urgency claim carries a clock, else "count"/""."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        if _is_deadline_pattern(pattern) and _CLOCK_RE.search(text[m.start():m.end() + 24]):
            return "countdown"
        if _count_in(m.group(0)):
            return "count"
    return ""


def detect_reference_pricing(trace: FunnelTrace) -> List[Violation]:
    """
    DPAF SN-RP (Reference Pricing) -- no CCPA-13 equivalent, added from
    cross-referencing Li et al. 2024's 68-type DPAF taxonomy against the
    13 CCPA categories this project targets. A fake inflated "original"
    price makes a real (or non-existent) discount look larger than it is.

    Provable slice only: an implausibly large claimed discount (>70%) is
    flagged for human cross-reference against the item's actual price
    history -- this tool cannot verify the "was" price was ever real
    without historical data, so it flags for review rather than asserting
    fraud outright. That distinction is stated in the evidence, not hidden.
    """
    violations = []
    for state in trace.states:
        # look for a strikethrough/"was" price and a "now" price on the same page
        was_matches = re.findall(r"(?:was|mrp|original)[:\s]*[₹$]?\s?([\d,]+(?:\.\d+)?)",
                                  state.full_text, re.IGNORECASE)
        now_price = state.price
        if was_matches and now_price:
            try:
                was_price = float(was_matches[0].replace(",", ""))
            except ValueError:
                continue
            if was_price > now_price * 1.0:
                discount_pct = (was_price - now_price) / was_price * 100
                if discount_pct > 70:
                    violations.append(Violation(
                        pattern_code="DPAF-SN-RP", pattern_name="Reference Pricing (beyond CCPA-13)",
                        step_name=state.step_name,
                        confidence=clamp_confidence(INDICATIVE, 0.5),
                        evidence={"evidence_tier": INDICATIVE, "claimed_was_price": was_price, "claimed_now_price": now_price,
                                  "claimed_discount_pct": round(discount_pct, 1)},
                        layer=1,
                        explanation=(f"Claimed discount of {discount_pct:.0f}% "
                                     f"({format_money(was_price, state.price_currency)} -> "
                                     f"{format_money(now_price, state.price_currency)}) is "
                                     f"implausibly large -- flagged for human "
                                     f"cross-reference against real price history, not asserted as fraud."),
                    ))
    return violations


def detect_immortal_account(trace: FunnelTrace) -> List[Violation]:
    """
    DPAF OB-IA (Immortal Accounts) -- no CCPA-13 equivalent. A site that
    offers account creation/settings but no visible account-deletion path
    on the same page is a documented dark pattern: easy to opt in,
    deliberately hard or impossible to opt out.

    This is an ABSENCE check, not a presence check -- only fires on pages
    that look like an account/settings page in the first place (contains
    "account" or "settings" in the page text), so it doesn't fire on every
    unrelated page that simply lacks a delete-account link.
    """
    violations = []
    delete_terms = ["delete account", "close account", "delete my account", "remove account"]
    settings_indicators = ["account settings", "my account", "profile settings"]
    for state in trace.states:
        low = state.full_text.lower()
        looks_like_account_page = any(term in low for term in settings_indicators)
        has_delete_option = any(term in low for term in delete_terms)
        if looks_like_account_page and not has_delete_option:
            violations.append(Violation(
                pattern_code="DPAF-OB-IA", pattern_name="Immortal Accounts (beyond CCPA-13)",
                step_name=state.step_name,
                confidence=clamp_confidence(INDICATIVE, 0.4),
                evidence={"evidence_tier": INDICATIVE,
                          "page_type": "account/settings", "delete_option_found": False},
                layer=1,
                explanation=("Page presents as an account/settings page but no account-deletion "
                              "link was found in the visible text -- possible Immortal Account "
                              "pattern (easy to opt in, no visible way to opt out)."),
            ))
    return violations


def detect_price_comparison_prevention(trace: FunnelTrace) -> List[Violation]:
    """
    DPAF OB-PC (Price Comparison Prevention) -- no CCPA-13 equivalent.
    Some sites disable text selection (CSS user-select:none, or a
    contextmenu-blocking script) specifically over the price/product-name
    element, making it harder for a shopper to copy the name into a
    search engine to compare prices elsewhere.
    """
    violations = []
    for state in trace.states:
        if "user-select: none" in state.raw_html.lower() or "user-select:none" in state.raw_html.lower():
            if "oncontextmenu" in state.raw_html.lower() or "user-select" in state.raw_html.lower():
                violations.append(Violation(
                    pattern_code="DPAF-OB-PC", pattern_name="Price Comparison Prevention (beyond CCPA-13)",
                    step_name=state.step_name,
                    confidence=clamp_confidence(INDICATIVE, 0.35),
                    evidence={"evidence_tier": INDICATIVE, "css_or_js_blocking_selection": True},
                    layer=1,
                    explanation=("Page CSS/JS disables text selection or right-click, which can "
                                  "prevent a shopper from copying the product name/price to "
                                  "compare prices on another site."),
                ))
    return violations


def detect_bait_and_switch(trace: FunnelTrace) -> List[Violation]:
    """
    DP-07 Bait and Switch -- the first working detector for this category
    (see TAXONOMY_CROSSWALK.md: this was previously defined in taxonomy.py
    but had zero implementation). Built directly from the CCI's own
    description of its MakeMyTrip/Goibibo inquiry: "false discounting and
    misleading price representations" -- a specific price/product promised
    by a step's own link/button text that does not match what the next
    page actually shows.

    Provable slice only: this requires the button/link text to make a
    SPECIFIC, checkable claim (a price or a named product), not a vague
    promise. A generic "Continue" link making no specific claim cannot be
    bait-and-switched by this definition, and isn't flagged.
    """
    violations = []
    for i in range(len(trace.states) - 1):
        current, nxt = trace.states[i], trace.states[i + 1]
        for btn in current.buttons:
            claimed_prices = re.findall(r"[₹$]\s?([\d,]+(?:\.\d+)?)", btn.text)
            if not claimed_prices:
                continue
            try:
                claimed = float(claimed_prices[0].replace(",", ""))
            except ValueError:
                continue
            if nxt.price is not None and abs(nxt.price - claimed) > max(claimed * 0.02, 1):
                violations.append(Violation(
                    pattern_code="DP-07", pattern_name=BY_CODE["DP-07"].name,
                    step_name=f"{current.step_name} -> {nxt.step_name}",
                    confidence=clamp_confidence(CORROBORATED, 0.7),
                    evidence={
                        "evidence_tier": CORROBORATED,
                        "button_text": btn.text, "claimed_price": claimed,
                        "actual_price_on_next_page": nxt.price,
                    },
                    layer=1,
                    explanation=(f"Link/button '{btn.text}' specifically promised "
                                 f"{format_money(claimed, nxt.price_currency)}, but the resulting "
                                 f"page '{nxt.step_name}' shows "
                                 f"{format_money(nxt.price, nxt.price_currency)} -- "
                                 f"the promised outcome does not match what was delivered."),
                ))
    return violations


def run_layer1(trace: FunnelTrace) -> List[Violation]:
    violations = []
    violations += detect_drip_pricing(trace)
    violations += detect_basket_sneaking_and_prechecked(trace)
    violations += detect_fake_urgency(trace)
    # "Beyond CCPA-13" additions from cross-referencing Li et al. 2024's
    # 68-type DPAF taxonomy (arXiv:2412.09147). Tagged DPAF-* rather than
    # DP-* so they're clearly distinguished from the core 13 legally-defined
    # categories in any report -- informative extras, not CCPA compliance flags.
    violations += detect_reference_pricing(trace)
    violations += detect_immortal_account(trace)
    violations += detect_price_comparison_prevention(trace)
    violations += detect_bait_and_switch(trace)
    return violations
