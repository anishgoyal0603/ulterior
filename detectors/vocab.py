"""
Shared detection vocabulary.

This module exists to solve one concrete problem: `layer1_rules` (DP-02
Basket Sneaking) and `layer1_flow` (DP-12 SaaS Billing) both need to know
whether a control's label commits the user to a REPEATING charge, because
whichever of them claims a given pre-ticked control, the other must not also
report it. If either module imported that knowledge from the other, the two
would import each other in a cycle. Putting the vocabulary in a leaf module
that neither depends on keeps one definition and no cycle.

Everything here is plain text matching with no state, so it is also the
cheapest part of the system to unit-test directly.
"""

import re
from typing import List, Optional

# Terms establishing that a charge repeats rather than happening once.
RECURRING_BILLING_TERMS = [
    r"\bauto[\s-]?renew(s|al|ing)?\b",
    r"\brecurring\b",
    r"\bsubscription\b",
    r"\bsubscribe\b",
    r"\bper\s+(month|year|week)\b",
    r"/\s*(mo|month|yr|year|week)\b",
    r"\bmonthly\b", r"\byearly\b", r"\bannually\b",
    r"\bbilled\s+(monthly|annually|yearly)\b",
    r"\bfree trial\b", r"\btrial (then|converts)\b",
    r"\bthen\s*[₹$]\s?[\d,]+",
]


def matches_any(text: str, patterns: List[str]) -> Optional[str]:
    """Return the first matched span, or None.

    Returning the SPAN rather than a boolean is deliberate: every finding this
    project emits must be able to quote the exact text that triggered it, and
    a bare True gives a report nothing to show. Curly quotes are normalised
    first — a real, measured source of missed matches against live retail copy
    (see state_extractor.py, where the same fix moved confirm-shaming
    detection from 52% to 92.9% on the Mathur et al. dataset).
    """
    if not text:
        return None
    normalised = text.replace("’", "'").replace("‘", "'")
    for pattern in patterns:
        match = re.search(pattern, normalised, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def is_recurring_label(text: str) -> bool:
    """Whether a control's own label commits the user to a repeating charge."""
    return matches_any(text, RECURRING_BILLING_TERMS) is not None
