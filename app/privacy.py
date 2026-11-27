"""
PII redaction for captured page content.

WHY THIS EXISTS — the threat model for THIS app is unusual and worth stating
plainly, because it isn't the usual "we store user emails" problem:

This app has no user accounts. It collects no emails, passwords, phone
numbers, or payment details from its own users. But it is a CRAWLER, and a
crawler pointed at a real checkout page — especially one with a logged-in
session — will capture whatever is on that page. That can include the
shopper's name, delivery address, email, phone, masked card number, and
order history. That content then flows into:

  - evidence spans stored in the database
  - explanation strings rendered into PDF reports
  - text sent to a third-party LLM API (Layer 3)
  - full-page screenshots written to disk

So the personal data at risk here belongs to whoever's session the crawler
runs under, not to a registered user of this app. Everything captured is
passed through redact() before it is stored, sent externally, or rendered.

Patterns cover the formats most likely to appear on an Indian e-commerce or
travel checkout page. This is defence-in-depth, not a guarantee: the
primary control is still "do not point the crawler at a logged-in session."
"""

import re

REDACTED = "[REDACTED]"

# Order matters: card numbers before generic long-digit runs, so a card
# isn't first partially eaten by the phone-number pattern.
_PATTERNS = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[EMAIL_REDACTED]"),

    # Payment card numbers: 13-19 digits, optionally separated by spaces/hyphens.
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[CARD_REDACTED]"),

    # Aadhaar: 12 digits, commonly written in 4-4-4 groups.
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), "[ID_REDACTED]"),

    # Indian PAN: 5 letters, 4 digits, 1 letter.
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[ID_REDACTED]"),

    # Indian mobile numbers: 10 digits starting 6-9, allowing internal spaces
    # or hyphens (e.g. "98765 43210"), with optional +91 / 0 prefix.
    # Not preceded by a letter or digit: without that, the last ten digits of
    # a SKU or order id ("SKU9876543210") were redacted as a phone number.
    (re.compile(r"(?:\+?91[\s-]?|(?<![A-Za-z0-9])0|(?<![A-Za-z0-9]))[6-9](?:[\s-]?\d){9}\b"),
     "[PHONE_REDACTED]"),

    # Indian PIN code preceded by an address-ish keyword (avoids nuking any
    # random 6-digit number such as an order id shown on the page).
    (re.compile(r"(?i)\b(pin|pincode|postal code)\b[\s:-]*\d{6}\b"), "[POSTAL_REDACTED]"),
]


def redact(text: str) -> str:
    """Redact PII from a string. Safe to call on None/empty."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_list(items):
    return [redact(i) for i in items] if items else items


def _redact_value(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def redact_evidence(evidence: dict) -> dict:
    """
    Recursively redact every string inside an evidence dict -- at any depth,
    including dicts inside lists (line items such as
    [{"name": "Deliver to ...", "price": 50}]) -- before it is persisted or
    returned via the API. Numeric evidence (prices, contrast ratios, pixel
    sizes) is left untouched: it carries no PII and is the actual proof.
    """
    if not isinstance(evidence, dict):
        return evidence
    return _redact_value(evidence)
