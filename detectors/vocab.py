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


# Paying for ONE purchase in instalments is not a subscription. "No-cost EMI
# ₹1,083/month" matched "/month" and a pre-selected payment option was
# reported as a recurring SaaS charge at PROVABLE.
EMI_RE = re.compile(r"\b(emi|emis|instal+ments?|pay\s*later|no[\s-]cost)\b", re.IGNORECASE)

# Pre-ticked controls that GIVE the shopper something or record a preference,
# rather than adding a charge (kept in step with BENEFIT_RE in
# extension/src/core.js). "Use my ₹50 wallet balance" is pre-ticked to save
# the shopper money; reporting it as basket sneaking accused shops of the
# opposite of what they did.
BENEFIT_RE = re.compile(
    r"\b(wallet|balance|cash\s*back|coupon|discount|offer\s+applied|points|super\s*coins|credits?|"
    r"rewards?|save|savings|use\s+my|apply|terms|agree|remember\s+me|keep\s+me|signed\s+in|notify|"
    r"whats\s*app|updates|newsletter|gst\s+invoice|billing\s+address|same\s+as)\b"
    r"|वॉलेट|कैशबैक|कूपन|छूट|शर्तें|सहमत", re.IGNORECASE)

# Wording that names a paid extra (kept in step with PAID_ADDON_RE in core.js).
PAID_ADDON_RE = re.compile(
    r"\b(insur(e|ance)|protect(ion|ed)?|warranty|secure\s+(my|your)\s+(trip|order|booking)|"
    r"donat(e|ion)|contribut(e|ion)|charity|tip|round\s+up|priority|express|gift\s*wrap|membership|"
    r"cover(age)?|assurance|guarantee|extended|meal|add[\s-]?on|fast\s*forward|premium)\b"
    r"|बीमा|दान|सहयोग\s*राशि|सुरक्षा\s*कवच", re.IGNORECASE)

# A radio option that says "no" to the extra ("No insurance", "I'll skip").
# A radio group is a REQUIRED choice: one of shipping / payment / size has to
# be selected, so a default there is not basket sneaking. It only is when the
# group offers an explicit "no" and the shop selected the paid option instead.
DECLINE_CHOICE_RE = re.compile(
    r"\b(no|don'?t|do not|skip|without|not\s+(now|required|needed)|i'?ll\s+take|none|free)\b", re.IGNORECASE)


def is_recurring_label(text: str) -> bool:
    """Whether a control's own label commits the user to a repeating charge.
    Instalments of a single purchase (EMI, pay later) are not."""
    if text and EMI_RE.search(text):
        return False
    return matches_any(text, RECURRING_BILLING_TERMS) is not None
