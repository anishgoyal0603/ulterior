"""
The 13 dark patterns named in India's Guidelines for Prevention and
Regulation of Dark Patterns, 2023 (Central Consumer Protection Authority,
notified 30 Nov 2023). Each entry records: the legal name, a plain-language
definition, which detection layer can actually catch it, and — added in the
"all 13" build — an EVIDENCE TIER stating how strong a claim this detector
is entitled to make.

This mapping is itself a design decision worth stating in the pitch: most
of these 13 patterns are provable with arithmetic or DOM inspection, not
prediction. Building this as "run everything through an LLM" would both
cost more and be less legally defensible than it needs to be.

## Why evidence tiers exist

Covering all 13 categories creates an obvious temptation: present a weak
signal with the same authority as a proven one, so the coverage table looks
uniform. That is exactly the failure mode that would get a finding thrown
out. So every detector declares its tier up front, the tier caps the
confidence it is allowed to report, and the tier is printed next to every
finding in the report.

  PROVABLE     — arithmetic or DOM state. The page either did or did not do
                 this; there is no judgement call and no model involved.
                 Example: a checkbox's `checked` property on first load.
                 Confidence 1.0. Defensible in a dispute without expert
                 testimony.

  CORROBORATED — two or more independent signals had to agree before this
                 fired (e.g. urgency LANGUAGE *and* a value that provably
                 did not change across a reload). Any single signal alone
                 would be too weak. Confidence capped at 0.9.

  INDICATIVE   — a pattern consistent with the category, which a human
                 should confirm. Absence checks and language heuristics live
                 here. Reported as "exhibits a pattern matching the
                 definition of X", never as a finding of violation.
                 Confidence capped at 0.6.

Nothing in this codebase is allowed to report a confidence above its tier's
cap; `clamp_confidence()` enforces it and a test asserts it.
"""

from dataclasses import dataclass
from typing import Optional

# --- Evidence tiers -------------------------------------------------------

PROVABLE = "provable"
CORROBORATED = "corroborated"
INDICATIVE = "indicative"

TIER_MAX_CONFIDENCE = {
    PROVABLE: 1.0,
    CORROBORATED: 0.9,
    INDICATIVE: 0.6,
}

TIER_CLAIM_LANGUAGE = {
    PROVABLE: "Directly observed in the page's own DOM state or arithmetic.",
    CORROBORATED: "Multiple independent signals agreed; single signals were insufficient.",
    INDICATIVE: "Consistent with this pattern; requires human confirmation before use.",
}


def clamp_confidence(tier: str, confidence: float) -> float:
    """No detector may report more certainty than its evidence tier allows.
    Called by every detector so the rule cannot be forgotten in one place."""
    return round(min(confidence, TIER_MAX_CONFIDENCE.get(tier, 0.6)), 2)


@dataclass
class DarkPattern:
    code: str
    name: str
    description: str
    primary_layer: int          # 1 = rules, 2 = visual, 3 = language
    tier: str = INDICATIVE      # best tier any detector for this pattern achieves
    coverage_note: str = ""     # what IS and IS NOT detected — printed in reports


DARK_PATTERNS = [
    DarkPattern("DP-01", "False Urgency",
                "Falsely stating or implying limited time/quantity to rush a decision "
                "(e.g. a countdown or stock counter that doesn't actually change).",
                primary_layer=1, tier=CORROBORATED,
                coverage_note="Requires BOTH urgency language AND a value proven stale "
                              "across a reload with real elapsed time. Genuine, honestly "
                              "changing scarcity messaging is not flagged."),
    DarkPattern("DP-02", "Basket Sneaking",
                "Adding items, donations, insurance, or warranties to the cart without "
                "the user's explicit consent.",
                primary_layer=1, tier=PROVABLE,
                coverage_note="Detects one-off chargeable add-ons pre-ticked on first "
                              "page load. Recurring/subscription pre-selection is "
                              "reported separately as DP-12."),
    DarkPattern("DP-03", "Confirm Shaming",
                "Wording an opt-out/decline option to guilt or shame the user into "
                "compliance (e.g. 'No, I don't care about saving money').",
                primary_layer=3, tier=INDICATIVE,
                coverage_note="Offline regex validated at 92.9% against Mathur et al. "
                              "(2019) real data. Optional LLM pass raises recall on "
                              "novel phrasing; every claim must quote verbatim page text."),
    DarkPattern("DP-04", "Forced Action",
                "Compelling a user to take an unrelated action (follow a page, share "
                "data, refer a friend) to complete their original task.",
                primary_layer=1, tier=PROVABLE,
                coverage_note="Detects (a) mandatory consent controls for marketing or "
                              "third-party data sharing bundled into a required flow, and "
                              "(b) blocking interstitials with no dismissal control. Does "
                              "not judge whether a requirement is commercially reasonable."),
    DarkPattern("DP-05", "Subscription Trap",
                "Making it easy to subscribe/pay but disproportionately hard to "
                "cancel — buried flows, excessive steps, hidden links.",
                primary_layer=1, tier=CORROBORATED,
                coverage_note="Detects (a) recurring charges disclosed only after the "
                              "commitment step, (b) cancel paths measurably longer than "
                              "the signup path, and (c) recurring offers with no "
                              "cancellation route anywhere in the captured funnel."),
    DarkPattern("DP-06", "Interface Interference",
                "Visual design that manipulates choice: a prominent 'Accept' next to "
                "a barely visible or low-contrast 'Decline'.",
                primary_layer=2, tier=CORROBORATED,
                coverage_note="Exact WCAG 2.1 contrast math and real rendered pixel "
                              "geometry from the browser, not a vision model's estimate."),
    DarkPattern("DP-07", "Bait and Switch",
                "Advertising one outcome (price, product, offer) and delivering a "
                "materially different one at the point of commitment.",
                primary_layer=1, tier=CORROBORATED,
                coverage_note="Only fires when a control makes a SPECIFIC checkable claim "
                              "(a price) that the resulting page contradicts. A vague "
                              "'Continue' cannot be bait-and-switched by this definition."),
    DarkPattern("DP-08", "Drip Pricing",
                "Not disclosing the full price upfront; fees revealed incrementally "
                "through the checkout funnel.",
                primary_layer=1, tier=PROVABLE,
                coverage_note="Arithmetic comparison of first disclosed price against "
                              "final itemised total, with a 2% rounding tolerance."),
    DarkPattern("DP-09", "Disguised Advertisement",
                "Content styled to look like organic/editorial content but is "
                "actually a paid advertisement.",
                primary_layer=2, tier=CORROBORATED,
                coverage_note="Detects disclosure labels that are present but practically "
                              "unreadable (WCAG contrast/size). Does NOT detect visual "
                              "mimicry of surrounding content — that needs a trained CV "
                              "model this project deliberately does not bundle."),
    DarkPattern("DP-10", "Nagging",
                "Repeated, disruptive requests for the same action after the user "
                "has already declined it.",
                primary_layer=1, tier=PROVABLE,
                coverage_note="The crawler actively dismisses each interruption it meets, "
                              "then records whether the SAME interruption reappears later "
                              "in the funnel. Reappearance after dismissal is the proof; "
                              "a prompt shown once is not flagged."),
    DarkPattern("DP-11", "Trick Wording",
                "Confusing language, double negatives, or ambiguous phrasing that "
                "causes the user to act against their actual intent.",
                primary_layer=3, tier=CORROBORATED,
                coverage_note="Offline detection of double negatives and inverted "
                              "opt-out controls (a pre-ticked box whose label means "
                              "'do not'). Optional LLM pass adds novel-phrasing recall."),
    DarkPattern("DP-12", "SaaS Billing",
                "Charging for a paid tier/add-on without clear, affirmative consent, "
                "often via pre-selected upgrade options.",
                primary_layer=1, tier=PROVABLE,
                coverage_note="A radio or checkbox pre-selected on first load whose own "
                              "label carries recurring-billing language. Separated from "
                              "DP-02 so one-off and recurring sneaking are never conflated."),
    DarkPattern("DP-13", "Rogue Malware",
                "Fake system alerts or warnings designed to trick a user into "
                "installing unwanted software.",
                primary_layer=2, tier=INDICATIVE,
                coverage_note="Detects the PRESENTATION pattern only — system-alert or "
                              "infection framing paired with a download/install call to "
                              "action, or an executable download disguised as content. "
                              "This tool performs NO binary analysis and never asserts "
                              "that any file is actually malicious."),
]

BY_CODE = {p.code: p for p in DARK_PATTERNS}


def tier_of(code: str) -> str:
    """Evidence tier for a pattern code. DPAF-* extras are always indicative —
    they sit outside the legally-defined 13 by construction."""
    pattern: Optional[DarkPattern] = BY_CODE.get(code)
    return pattern.tier if pattern else INDICATIVE


def coverage_summary() -> dict:
    """Machine-readable coverage table — served by the API at /coverage so the
    dashboard and the pitch quote the same numbers from the same source, and
    nobody has to remember to update a slide."""
    by_tier = {PROVABLE: [], CORROBORATED: [], INDICATIVE: []}
    for p in DARK_PATTERNS:
        by_tier[p.tier].append(p.code)
    return {
        "total_patterns": len(DARK_PATTERNS),
        "implemented": len(DARK_PATTERNS),
        "by_tier": {tier: sorted(codes) for tier, codes in by_tier.items()},
        "tier_definitions": TIER_CLAIM_LANGUAGE,
        "tier_max_confidence": TIER_MAX_CONFIDENCE,
        "patterns": [
            {"code": p.code, "name": p.name, "tier": p.tier,
             "primary_layer": p.primary_layer, "coverage_note": p.coverage_note}
            for p in DARK_PATTERNS
        ],
    }
