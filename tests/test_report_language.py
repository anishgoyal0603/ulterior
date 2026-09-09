"""
Tests that the generated report cannot make a claim the project is not
entitled to make.

This is a legal-exposure test, not a formatting test. The tool names real
businesses and assesses them against a real statute; the difference between
"this page exhibits a pattern matching the definition of X" and "this company
violates X" is the difference between a defensible document and a defamation
risk. Wording that careful is exactly the kind that erodes over time as
someone tightens a sentence — so it is asserted here rather than trusted to
a code review.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from report.generator import generate_audit_report

pdfplumber = pytest.importorskip(
    "pdfminer.high_level", reason="pdfminer is needed to read the generated PDF back"
)
from pdfminer.high_level import extract_text  # noqa: E402


SAMPLE = [
    {
        "pattern_code": "DP-08", "pattern_name": "Drip Pricing", "step_name": "cart -> checkout",
        "confidence": 1.0, "layer": 1,
        "evidence": {"evidence_tier": "provable", "hidden_amount": 149.0},
        "explanation": "Price disclosed at 'cart' was Rs 299, final total is Rs 448.",
    },
    {
        "pattern_code": "DP-13", "pattern_name": "Rogue Malware", "step_name": "watch",
        "confidence": 0.5, "layer": 1,
        "evidence": {"evidence_tier": "indicative", "binary_analysis_performed": False},
        "explanation": "Link points to an executable without saying so in its own text.",
    },
]


@pytest.fixture(scope="module")
def report_text(tmp_path_factory):
    out = tmp_path_factory.mktemp("report") / "audit.pdf"
    generate_audit_report("ExampleShop (demo)", SAMPLE, str(out))
    assert out.exists() and out.stat().st_size > 0
    # Collapse the PDF's line wrapping before asserting. A phrase broken
    # across two lines by the layout engine is still present in the document;
    # asserting on raw extracted text would fail for a purely typographic
    # reason and teach whoever hits it to weaken the assertion.
    return re.sub(r"\s+", " ", extract_text(str(out)))


def test_report_never_asserts_a_legal_violation_by_the_audited_site(report_text):
    banned = [
        "violates", "violated", "is in breach", "found guilty",
        "illegal", "unlawful", "non-compliant with the law",
    ]
    lowered = report_text.lower()
    for phrase in banned:
        assert phrase not in lowered, (
            f"report asserts a legal conclusion it has no authority to make: {phrase!r}"
        )


def test_report_states_it_is_not_a_legal_determination(report_text):
    lowered = report_text.lower()
    assert "not a legal determination" in lowered
    assert "no regulatory authority" in lowered


def test_report_shows_the_evidence_tier_of_every_observation(report_text):
    """A 50% indicative observation and a 50% provable one are different
    claims. A confidence figure alone hides that."""
    assert "Evidence tier" in report_text
    assert "provable" in report_text
    assert "indicative" in report_text


def test_report_always_prints_its_own_limitations(report_text):
    """Silence must not read as a clean bill of health."""
    lowered = report_text.lower()
    assert "scope and limitations" in lowered
    assert "behind authentication" in lowered
    assert "no file was downloaded" in lowered


def test_report_survives_an_observation_with_no_tier(tmp_path):
    """Older records predate evidence tiers. The generator must degrade to
    'unstated' rather than raising and failing the whole export."""
    legacy = [{
        "pattern_code": "DP-01", "pattern_name": "False Urgency", "step_name": "listing",
        "confidence": 0.9, "layer": 1, "evidence": {}, "explanation": "Stale counter.",
    }]
    out = tmp_path / "legacy.pdf"
    generate_audit_report("LegacyShop", legacy, str(out))
    assert "unstated" in re.sub(r"\s+", " ", extract_text(str(out)))


def test_table_headers_are_not_broken_mid_word_by_narrow_columns(tmp_path):
    """A layout regression test, deliberately asserting on RAW extracted text.

    Adding the evidence-tier column without re-budgeting the others squeezed
    "Layer" into "La/ye/r" down three lines and split "Evidence tier"
    mid-word. Nothing failed — the PDF still generated, the content was still
    technically present, and every content assertion still passed after
    whitespace normalisation. Only a human looking at the page would have
    noticed, and the whole premise of this report is that a non-technical
    official can verify it by eye.
    """
    out = tmp_path / "layout.pdf"
    generate_audit_report("LayoutCheck", SAMPLE, str(out))
    raw = extract_text(str(out))
    for header in ("Pattern", "Step", "Layer", "Observation"):
        assert header in raw, f"header {header!r} was broken across lines by the layout"
