"""
Layer 1: deterministic rules. Every function here returns a Violation only
when the evidence is mechanically provable — a price delta, a checkbox's
actual DOM state, a countdown that provably didn't advance. No model, no
confidence interval, no argument possible in a legal dispute.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re
from types import SimpleNamespace

from .taxonomy import BY_CODE, PROVABLE, CORROBORATED, INDICATIVE, clamp_confidence
from .vocab import BENEFIT_RE, DECLINE_CHOICE_RE, EMI_RE, PAID_ADDON_RE, is_recurring_label
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
    variants = {str(whole), f"{whole:,}", _indian_grouping(whole)}
    if abs(amount - whole) < 0.005:
        variants |= {f"{whole}.00", f"{whole:,}.00", _indian_grouping(whole) + ".00"}
    else:
        # ₹1,299.50 / ₹1,23,456.50: the paise go on the TRUNCATED rupees with
        # the same grouping. (Rounding first gave "1,300.00" and never the
        # figure the page printed.)
        # Both parts from ONE rounding, so a boundary float (99.995) cannot
        # give rupees from one value and paise from another.
        rupees_s, paise = f"{amount:.2f}".split(".")
        rupees = int(rupees_s)
        variants = {f"{rupees}.{paise}", f"{rupees:,}.{paise}", f"{_indian_grouping(rupees)}.{paise}"}
    variants = sorted(variants, key=len, reverse=True)
    haystack = text.replace("\u00a0", " ")
    number = "|".join(re.escape(v) for v in variants)
    # As a PRICE: with a currency mark before it (or "/-" after it), and not
    # part of a longer number. A bare substring test let "4,570 reviews", a
    # PIN code or a phone number "disclose" a total of 457.
    pattern = (rf"(?:[₹$€£]|\b(?:rs\.?|inr|usd|eur|gbp)\b)\s*(?:{number})(?![\d]|[.,]\d)"
               rf"|(?<![\d.,])(?:{number})\s*/-")
    return re.search(pattern, haystack, re.IGNORECASE) is not None


def _indian_grouping(n: int) -> str:
    """1234567 -> 12,34,567 (how Indian shops write it)."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


# The largest number of rows that can plausibly be ONE order's fee
# breakdown. Above this, the "itemisation" is a product catalogue.
_MAX_BREAKDOWN_ROWS = 12

# The word a page uses when it is telling you what you will pay.
_STATES_A_TOTAL = re.compile(
    r"\b(total|subtotal|sub-total|amount payable|grand total|order total|"
    r"you pay|payable|order summary|amount to pay)\b", re.IGNORECASE)


# "Subtotal" is the price before fees. Taking it as the total made DP-08 blind
# to exactly the checkouts it exists for: Subtotal ₹299, Delivery ₹49,
# Total ₹348 -> "total" 299, no difference, no finding.
_SUBTOTAL = re.compile(r"\b(sub[\s-]?total|item(s)? total|cart total|bag total)\b", re.IGNORECASE)
_FINAL_TOTAL = re.compile(
    r"\b(grand total|order total|total amount|total payable|amount payable|net payable|"
    r"you pay|to pay|payable|total)\b", re.IGNORECASE)
# The labels that name the amount actually charged. They outrank a bare
# "Total", which also heads "Total items" and similar rows.
_PAYABLE = re.compile(
    r"\b(grand total|order total|total amount|total payable|amount payable|net payable|"
    r"amount to pay|you pay|to pay)\b", re.IGNORECASE)
# Rows that contain the word "total" but are not the amount owed. Indian
# checkouts print "Total Savings ₹150" or "Total Discount ₹130" BELOW the order
# total; reading that as the last total turned a ₹457 order into ₹150 and
# silenced DP-08 on exactly the checkouts it exists for.
_NOT_A_TOTAL = re.compile(
    r"\b(savings?|saved|save|discounts?|cash\s*back|off|rewards?|points|coins|m\.?r\.?p\.?|"
    r"you\s+saved|total\s+(items?|qty|quantity))\b", re.IGNORECASE)


# A FEE row: a fee noun ("Delivery fee", "Platform charge", "Convenience
# fee") or a row that is only a fee word ("Delivery", "GST"). Bare nouns
# inside a product name ("Packing Cubes", "Service Kit") are not fees.
_FEE_WORDS = re.compile(
    r"\b(fees?|charges?|surcharge|tax(es)?|gst|cess)\b"
    r"|^\s*(delivery|shipping|handling|convenience|platform|packaging|packing|cod|service|booking|processing)"
    r"(\s+(and|&)\s+\w+)?\s*$", re.IGNORECASE)


def _stated_total(state: "PageState") -> Optional[float]:
    """The figure the page itself puts next to the word "total".

    This is the number the shop tells the shopper they will pay, and it is the
    only honest basis for a drip-pricing comparison. Everything else -- the
    first price on the page, the sum of whatever rows a scanner found -- is
    the crawler's inference, and inferences are what produced a PROVABLE
    accusation built from a shipping banner and a product shelf.

    Ranked: an explicit payable label ("Amount payable", "Order total") beats
    a bare "Total", which beats a "Subtotal". Within a rank the LAST one on
    the page wins. Savings, discount and promotional-threshold rows are never
    totals ("Free delivery on total orders over ₹500" is a condition).
    """
    from capture.state_extractor import _PROMO_THRESHOLD_RE
    lines = [ln.strip() for ln in (state.full_text or "").splitlines() if ln.strip()]
    best = {}                     # rank -> amount (last seen)
    for i, raw_line in enumerate(lines):
        # Parenthetical notes are not the label: "Order Total (₹100 off):
        # ₹457" is a total of ₹457. Testing the whole line for "off" skipped
        # it, and reading its first amount gave ₹100.
        line = re.sub(r"\([^)]*\)", " ", raw_line)
        is_sub = bool(_SUBTOTAL.search(line))
        if not is_sub and not _FINAL_TOTAL.search(line):
            continue
        if _NOT_A_TOTAL.search(line) or _PROMO_THRESHOLD_RE.search(line):
            continue
        amount = parse_money(line)
        if (not amount and i + 1 < len(lines) and not _STATES_A_TOTAL.search(lines[i + 1])
                and not _NOT_A_TOTAL.search(lines[i + 1]) and not _PROMO_THRESHOLD_RE.search(lines[i + 1])):
            # <div><span>Total</span><span>₹457</span></div> renders as two
            # lines: the label on one, the amount on the next. Never a
            # savings line ("Total" over "You saved ₹150").
            amount = parse_money(re.sub(r"\([^)]*\)", " ", lines[i + 1]))
        if not amount:
            continue
        rank = 1 if is_sub else (3 if _PAYABLE.search(line) else 2)
        best[rank] = amount
    # A subtotal is never preferred to a real total ("Subtotal ₹299 ... Total
    # ₹457" is a ₹457 order). But a cart drawer that states ONLY a subtotal
    # is still the shop's own figure for the basket, and better than a
    # number the crawler picked off the page.
    return best[max(best)] if best else None


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


def _named_as_a_charge(name: str, text: str) -> bool:
    """Was this fee named on the first page as something the shopper pays?

    Whole words only, and a mention preceded by "free" / "no" does not count:
    "Free 2-day delivery" on the product page does not disclose a ₹49
    "Delivery" charge at checkout -- it says the opposite."""
    name = (name or "").strip().lower()
    if not name or not text:
        return False
    lowered = text.lower()
    for m in re.finditer(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", lowered):
        before = lowered[max(0, m.start() - 25):m.start()]
        if re.search(r"\b(free|no|zero|waived)\b[^.\n]*$", before):
            continue
        return True
    return False


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
                        and li["price"] > 0 and not _named_as_a_charge(li["name"], first.full_text)]
        # The product itself is not an undisclosed charge. At checkout it is
        # usually renamed ("Cotton Shirt (Size M)"), so its name never matches
        # the product page; it is recognised by costing the price that page
        # showed. Only one row is removed -- two rows at that price means the
        # second is something else.
        # Only when the product row was NOT already recognised by name, and
        # never a fee row: on a ₹49 item with a ₹49 delivery fee, the fee was
        # being removed as "the product" and DP-08 went silent.
        def _at_product_price(li):
            return abs(li["price"] - first.price) <= max(first.price * 0.02, 1)
        product_named = any(_at_product_price(li) and li not in undisclosed for li in last.line_items)
        if not product_named:
            for li in undisclosed:
                if _at_product_price(li) and not _FEE_WORDS.search(li["name"]):
                    undisclosed.remove(li)
                    break

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
                label = (cb.label_text or "").replace("\u2019", "'")
                amount = parse_money(label)
                # A credit: the minus TOUCHES the currency ("-₹50"), or opens
                # the text / a bracket ("- ₹50", "(- ₹50)"). A dash used as
                # punctuation ("Carry Bag - ₹15") is not a minus.
                if re.search(r"(?:^|[\s(])[-\u2212](?=[₹$€£]|rs\.?\s?\d|inr)|(?:^|\()\s*[-\u2212]\s+(?=[₹$€£]|rs|inr)",
                             label.strip(), re.IGNORECASE):
                    amount = None
                is_paid_addon = bool(PAID_ADDON_RE.search(label)) and (amount or 0) > 0
                # Pre-ticked to GIVE something (wallet balance, cashback, a
                # coupon) or to record a preference: not an added charge --
                # unless it names a paid extra with a price ("Extended
                # Warranty ₹299 (T&C apply)", "I agree to add insurance ₹199").
                if BENEFIT_RE.search(label) and not is_paid_addon:
                    continue
                # A radio group is a required choice (shipping speed, payment
                # method, size). Its default is basket sneaking only when the
                # same group offers an explicit "no" the shop passed over.
                if getattr(cb, "input_type", "checkbox") == "radio" and not any(
                        other is not cb and getattr(other, "input_type", "") == "radio"
                        and getattr(other, "group", "") == getattr(cb, "group", "")
                        and getattr(cb, "group", "")
                        and DECLINE_CHOICE_RE.search(other.label_text or "")
                        for other in state.checkboxes):
                    continue
                # A positive amount in any currency form ("₹199", "Rs. 199",
                # "INR 199"); ₹0 and credits ("-₹50") are not charges.
                matches_charge = (amount is not None and amount > 0) \
                    or any(name and name in label.lower() for name in chargeable_names) \
                    or bool(PAID_ADDON_RE.search(label))
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
        first_text = _straight_quotes(first.full_text)
        second_text = _straight_quotes(second.full_text)

        # Each claim is compared with ITSELF across the reload. Joining every
        # value into one string ("00:04:59 2") made the clock unparseable and
        # silently dropped every finding on a page with a timer AND a stock
        # count -- the commonest combination there is.
        clocks, counts = [], []
        for p in first.urgency_phrases_found:
            v1 = _extract_urgent_value(first_text, p)
            if not v1:
                continue
            v2 = _extract_urgent_value(second_text, p)
            (clocks if ":" in v1 else counts).append((p, v1, v2))

        kind, first_value, second_value = "", "", ""
        for p, v1, v2 in clocks:
            s1, s2 = _clock_to_seconds(v1), _clock_to_seconds(v2)
            if s1 is not None and s2 is not None and s2 >= s1:
                kind, first_value, second_value = "countdown", v1, v2
                break
        if not kind:
            for p, v1, v2 in counts:
                if v2 and v1 == v2:
                    kind, first_value, second_value = "count", v1, v2
                    break
        if not kind:
            continue

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
            pass          # established above: it did not go down

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
# "04h 22m 18s", "4 hrs 22 mins", "08m 12s" -- how Flipkart, Myntra and
# Amazon write their timers.
_UNIT_CLOCK_RE = re.compile(
    # Seconds REQUIRED (as parseCountdown in extension/src/core.js): an
    # honest "ends in 04h 22m" cannot tick within a 2-second reload, and
    # "02h 15m" is usually a flight time.
    r"\b(?:(\d{1,2})\s*h(?:rs?|ours?)?\s*(\d{1,2})\s*m(?:ins?|inutes?)?\s*(\d{1,2})\s*s(?:ecs?|econds?)?"
    r"|(\d{1,2})\s*m(?:ins?|inutes?)?\s*(\d{1,2})\s*s(?:ecs?|econds?)?)\b", re.IGNORECASE)
# A clock that names a TIME OF DAY is a fixed closing time, not a countdown:
# "Sale ends at 11:59 PM", "Offer ends 23:59 IST". Unchanged across a reload
# is exactly what a closing time should be.
_WALL_CLOCK_AFTER = re.compile(r"^\s*(a\.?m\.?|p\.?m\.?|ist|utc|gmt|hrs\b|o'?clock)", re.IGNORECASE)
# "11:59 tonight" is a closing time; "ends in 00:04:59 tonight" is a timer.
_DAY_WORD_AFTER = re.compile(r"^\s*(tonight|today|tomorrow)\b", re.IGNORECASE)
_IN_BEFORE = re.compile(r"\bin\s*:?\s*$", re.IGNORECASE)
_WALL_CLOCK_BEFORE = re.compile(r"\b(at|until|till|by|before|on)\s*$", re.IGNORECASE)


def _find_clock(span: str) -> str:
    """The first countdown-looking clock in this span, normalised to
    H:MM:SS / MM:SS, or "" when there is none (time-of-day clocks skipped)."""
    found = []
    for m in _CLOCK_RE.finditer(span):
        found.append((m.start(), m.end(), m.group(0).replace(" ", "")))
    for m in _UNIT_CLOCK_RE.finditer(span):
        if m.group(1) is not None:
            h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            found.append((m.start(), m.end(), f"{h}:{mi:02d}:{se:02d}"))
        else:
            found.append((m.start(), m.end(), f"{int(m.group(4))}:{int(m.group(5)):02d}"))
    for start, end, value in sorted(found):
        if _WALL_CLOCK_AFTER.search(span[end:end + 12]) or _WALL_CLOCK_BEFORE.search(span[:start]):
            continue
        if _DAY_WORD_AFTER.search(span[end:end + 12]) and not _IN_BEFORE.search(span[:start]):
            continue
        return value
    return ""
_COUNT_RE = re.compile(r"\b\d{1,6}\b")


def _straight_quotes(text: str) -> str:
    """The urgency patterns were matched on text with curly quotes made
    straight; values must be read from the same text, or "Don’t miss out"
    matches and then yields nothing."""
    return (text or "").replace("\u2019", "'").replace("\u2018", "'")


def _clock_to_seconds(value: str):
    """'00:04:59' -> 299. Returns None if it is not a clock."""
    parts = (value or "").strip().split(":")
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
#
# "Hurry" is not a deadline either: "Hurry! 07:15 DEL -- Only 3 seats left"
# put a departure time within reach of the word, and it was read as a frozen
# countdown.
_DEADLINE_PATTERNS = (
    "offer ends", "sale ends", "limited time", "ends in", "expires",
    "deal ends",
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
    # EVERY occurrence, not just the first: a header "Limited time offer"
    # with no value used to hide a frozen timer further down the page.
    for m in re.finditer(pattern, text, re.IGNORECASE):
        # A clock may sit just past the phrase ("Offer ends in: 00:04:59"), so
        # look a little way ahead -- but ONLY when this phrase claims a
        # deadline. See _DEADLINE_PATTERNS: next to a seats-left message, a
        # clock is a departure time.
        if _is_deadline_pattern(pattern):
            clock = _find_clock(text[m.start():m.end() + 24])
            if clock:
                return clock
        # A COUNT, though, must be inside the phrase itself ("only 2 left").
        # Reading ahead for it swept up any number that happened to follow --
        # "Hurry, offer ends 15 August" yielded "15" and a real sale with a
        # real calendar date was reported as static fake scarcity. A date is
        # not a depleting quantity, and the scarcity patterns that matter all
        # bind the number to a depletion word inside the match.
        count = _count_in(m.group(0))
        if count:
            return count
    return ""


def _urgency_value_kind(text: str, patterns: List[str]) -> str:
    """"countdown" if any urgency claim carries a clock, else "count"/""."""
    kind = ""
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if _is_deadline_pattern(pattern) and _find_clock(text[m.start():m.end() + 24]):
                return "countdown"
            if _count_in(m.group(0)):
                kind = "count"
    return kind


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
        # Whole words, and the figure must be written as money: "canvas: 2000"
        # or "was 2000 employees" are not reference prices.
        was_matches = re.findall(
            # \b only after the word-ending labels: after "M.R.P." the dot is
            # already a non-word character, so a \b there never matched and
            # the standard Indian "M.R.P. ₹2,499" was never read.
            r"(?:\b(?:was|mrp|original price|list price)\b|\bm\.r\.p\.?)[:\s]*"
            r"(?:[₹$€£]|\brs\.?|\binr\b)\s?([\d,]+(?:\.\d+)?)",
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
        html = (state.raw_html or "").lower()
        # The old test was "user-select: none" anywhere AND "user-select"
        # anywhere -- always true together, so every page that styles a
        # button or a toggle as unselectable (very common) was reported. Only
        # a block that targets the PRICE or PRODUCT NAME, or a page-wide
        # right-click block, is what this pattern describes.
        targets_price = re.search(
            r"(?:class|id)\s*=\s*[\"'][^\"']*(?:price|product[-_ ]?(?:name|title))[^\"']*[\"'][^>]*"
            r"style\s*=\s*[\"'][^\"']*user-select\s*:\s*none"
            r"|\.(?:[\w-]*price[\w-]*|product[-_]?(?:name|title))[^{]*\{[^}]*user-select\s*:\s*none", html)
        blocks_context_menu = re.search(
            r"<body[^>]*oncontextmenu\s*=\s*[\"']\s*return\s+false"
            r"|document\.oncontextmenu\s*=|addEventListener\(\s*[\"']contextmenu[\"'][^)]*preventdefault", html)
        if targets_price or blocks_context_menu:
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
        controls = list(current.buttons) + [
            SimpleNamespace(text=getattr(link, "text", "") or "") for link in (current.links or [])]
        promises = []
        for btn in controls:
            text = btn.text or ""
            # "Save ₹500 on orders above ₹4,999" promises a discount on a
            # condition, not a price; comparing ₹500 with the next page's
            # price was a false accusation.
            if re.search(r"\b(save|off|cashback|discount|above|over|min(imum)?|upto|up to|from)\b",
                         text, re.IGNORECASE):
                continue
            # An add-on's own price ("Add 2-year warranty ₹49"), an EMI or a
            # price lock is not a promise about the NEXT page's price: the
            # crawler followed "Add to cart", not the warranty button.
            if PAID_ADDON_RE.search(text) or EMI_RE.search(text) or re.search(
                    r"\b(lock|freeze|hold)\s+(the\s+|this\s+)?(price|fare)\b", text, re.IGNORECASE):
                continue
            claimed_prices = re.findall(
                r"(?:[₹$€£]|\brs\.?|\binr\b)\s?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
            if len(claimed_prices) != 1:
                continue      # no price, or several: not one checkable promise
            try:
                promises.append((btn, float(claimed_prices[0].replace(",", ""))))
            except ValueError:
                continue
        # The crawler does not record WHICH control it followed. On a results
        # page with three fares ("Book ₹4,199", "Book ₹4,599", "Book ₹5,050"),
        # following the ₹4,599 one and landing on ₹4,599 reported the OTHER
        # two as bait and switch. Only a step with a single priced promise
        # says which promise the next page must keep.
        if len({claimed for _, claimed in promises}) != 1:
            continue
        for btn, claimed in promises[:1]:
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
