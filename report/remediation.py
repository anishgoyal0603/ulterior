"""
What a developer can change so a page no longer shows each pattern.

One entry per pattern in the 2023 Guidelines. Each says what to change in
plain terms a store's developer can act on, and how to check the change.
These are suggestions for the business to consider, not legal advice: the
business decides what its pages must do.
"""

REMEDIATION = {
    "DP-01": (
        "False Urgency",
        "Show a countdown or 'only N left' only when it is true and comes from real data: "
        "a sale end time from the backend, a stock count from inventory. Do not restart timers "
        "on reload and do not show fixed scarcity numbers.",
        "Reload the page twice and compare: the timer must keep counting down, and stock numbers "
        "must match the inventory system.",
    ),
    "DP-02": (
        "Basket Sneaking",
        "Leave every optional add-on (insurance, donation, warranty, protection plan) unticked "
        "by default. Add it to the cart only after the shopper actively selects it.",
        "Add one product and open the cart: the total must equal the product price plus fees "
        "the shopper has been told about, with no option already selected.",
    ),
    "DP-03": (
        "Confirm Shaming",
        "Word the decline option neutrally ('No thanks', 'Skip') and give it the same size and "
        "visibility as the accept option. Remove guilt or fear phrasing.",
        "Read both buttons aloud: neither should insult or blame the shopper for declining.",
    ),
    "DP-04": (
        "Forced Action",
        "Let shoppers finish the purchase without installing an app, creating an account or "
        "agreeing to marketing. Make marketing consent a separate, unticked choice.",
        "Complete a guest checkout with marketing consent left unticked.",
    ),
    "DP-05": (
        "Subscription Trap",
        "Show the price, billing frequency and the date the trial converts before payment. "
        "Provide an online cancel path that takes no more steps than signing up.",
        "Sign up for a test subscription, then cancel it online without contacting support.",
    ),
    "DP-06": (
        "Interface Interference",
        "Give accept and decline options comparable size, colour and contrast. Do not hide the "
        "close or decline control or place it where it is easy to miss.",
        "Check the decline control is at least as readable as accept (WCAG AA contrast, 4.5:1).",
    ),
    "DP-07": (
        "Bait and Switch",
        "Make sure the product, variant and price advertised are the ones in the cart. If an "
        "item is unavailable, say so plainly instead of substituting another.",
        "Click an advertised offer through to the cart and compare title, variant and price.",
    ),
    "DP-08": (
        "Drip Pricing",
        "Show the full price, including every unavoidable fee (platform, convenience, handling), "
        "at the first point a price is shown. Optional extras can be added later, clearly labelled.",
        "Compare the price on the product page with the checkout total for the same item with "
        "no extras chosen: the only difference should be taxes or delivery the shopper was told about.",
    ),
    "DP-09": (
        "Disguised Advertisement",
        "Label sponsored or paid placements clearly ('Sponsored', 'Ad') with readable text size "
        "and contrast, next to the item.",
        "Check every sponsored tile has a visible label with at least 4.5:1 contrast.",
    ),
    "DP-10": (
        "Nagging",
        "Once a shopper closes a pop-up or declines a request, do not show it again in the same "
        "session. Respect 'not now' choices.",
        "Close the pop-up, browse three more pages: it should not return.",
    ),
    "DP-11": (
        "Trick Wording",
        "Write checkbox labels as simple positive statements ('Send me offers by email') with "
        "no double negatives, and leave them unticked.",
        "Ask someone unfamiliar with the page what ticking the box does; they should answer "
        "correctly on first reading.",
    ),
    "DP-12": (
        "SaaS Billing",
        "Tell customers before any recurring charge or price increase, show the renewal amount "
        "and date, and do not charge for a plan or seats the customer did not choose.",
        "Review the billing page and renewal emails for amount, date and how to stop renewal.",
    ),
    "DP-13": (
        "Rogue Malware",
        "Remove fake virus, system or security warnings and any prompt that pressures a download "
        "or payment through fear.",
        "Search the site's templates and ad slots for warning-style pop-ups and remove them.",
    ),
}


def checklist_for(codes):
    """Unique pattern codes in the order given -> list of (code, name, fix, check)."""
    seen, out = set(), []
    for code in codes:
        if code in REMEDIATION and code not in seen:
            seen.add(code)
            name, fix, check = REMEDIATION[code]
            out.append((code, name, fix, check))
    return out
