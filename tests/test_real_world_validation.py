"""
Regression tests against REAL scraped dark-pattern text (Mathur et al. 2019,
CSCW, "Dark Patterns at Scale" -- 1,818 instances from an actual crawl of
11K shopping websites). Every other test in this project uses fixtures I
built myself; this is the only place actual real-world adversarial text is
checked against the detectors, and it's what caught real regex gaps
(curly-apostrophe handling, missing urgency phrasings) that synthetic
fixtures never surfaced.

These tests assert MINIMUM detection rates, not exact ones -- the real data
will not change, but future edits to the pattern lists should not be able
to silently regress below the validated floor without a test failure.
"""
import csv
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.state_extractor import URGENCY_PATTERNS
from detectors.layer3_language import _offline_confirm_shaming

DATA_PATH = Path(__file__).parent.parent / "data" / "real_world_validation" / "mathur2019_dark_patterns.csv"


def _normalize_quotes(s):
    return s.replace("\u2019", "'").replace("\u2018", "'")


@pytest.fixture(scope="module")
def real_rows():
    if not DATA_PATH.exists():
        pytest.skip(f"Real-world validation data not present at {DATA_PATH}")
    with open(DATA_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_dataset_matches_paper_reported_totals(real_rows):
    """Sanity check that this really is the paper's full dataset, not a
    corrupted or partial extract -- the paper reports 1,818 total instances."""
    assert len(real_rows) == 1818


def test_urgency_language_detection_rate_on_real_data(real_rows):
    urgency_rows = [
        r for r in real_rows
        if r["Pattern Type"] in ("Low-stock Message", "High-demand Message", "Limited-time Message")
        and r["Pattern String"].strip()
    ]
    matched = sum(
        1 for r in urgency_rows
        if any(re.search(p, _normalize_quotes(r["Pattern String"]), re.IGNORECASE) for p in URGENCY_PATTERNS)
    )
    rate = matched / len(urgency_rows)
    # Validated floor at time of writing: 94.1% (721/766). Assert a small
    # margin below that so minor wording drift in future test data edits
    # doesn't cause spurious failures, while still catching a real regression.
    assert rate >= 0.90, f"Urgency detection rate dropped to {rate:.1%} (floor: 90%)"


def test_confirm_shaming_detection_rate_on_real_data(real_rows):
    cs_rows = [r for r in real_rows if r["Pattern Type"] == "Confirmshaming"]
    raw = _offline_confirm_shaming([r["Pattern String"] for r in cs_rows])
    detected = len(raw)
    rate = detected / len(cs_rows)
    # Validated floor: 92.9% (157/169).
    assert rate >= 0.88, f"Confirm-shaming detection rate dropped to {rate:.1%} (floor: 88%)"


def test_confirm_shaming_never_flags_neutral_button_text():
    """Negative control: ordinary, non-manipulative button text must not
    trigger the guilt-trip heuristic. Real data gives us positives; this
    guards the false-positive side using plausible neutral copy."""
    neutral_texts = [
        "Continue to checkout", "Add to cart", "View cart", "Apply coupon",
        "Sign in", "Create account", "Save for later", "Remove item",
    ]
    raw = _offline_confirm_shaming(neutral_texts)
    assert raw == [], f"False positive(s) on neutral text: {raw}"


def test_deceptive_confirmed_low_stock_examples_are_all_caught(real_rows):
    """Mathur et al.'s own methodology flags a SUBSET of Low-stock Message
    instances as 'Deceptive: Yes' -- specifically the ones they independently
    verified were fabricated (e.g. decrementing on a fixed schedule rather
    than real inventory, per their own comments field). These are the
    highest-value true positives to catch, since they're independently
    confirmed fake, not just pattern-matched. Assert we catch essentially
    all of them -- this is a smaller, higher-confidence set than the full
    urgency-language test above."""
    confirmed_fake = [
        r for r in real_rows
        if r["Pattern Type"] == "Low-stock Message" and r["Deceptive?"] == "Yes"
    ]
    assert len(confirmed_fake) > 0, "Expected some confirmed-deceptive examples in the dataset"
    matched = sum(
        1 for r in confirmed_fake
        if any(re.search(p, _normalize_quotes(r["Pattern String"]), re.IGNORECASE) for p in URGENCY_PATTERNS)
    )
    rate = matched / len(confirmed_fake)
    assert rate >= 0.95, f"Missed confirmed-fake low-stock examples: {rate:.1%} caught (floor 95%)"
