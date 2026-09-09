"""
Layer 2: visual geometry. Uses REAL computed styles and bounding boxes from
the browser (not a vision model guessing) -- contrast ratio is the exact
WCAG 2.1 formula, button size comparison is exact pixel measurement. This
is provable math on rendered output, one tier down from Layer 1's DOM-state
proofs, but still not a "confidence score" in the ML sense.
"""

from dataclasses import dataclass
from typing import List

from .taxonomy import BY_CODE, CORROBORATED, clamp_confidence
from .layer1_rules import Violation
from capture.funnel_walker import FunnelTrace

LOW_CONTRAST_THRESHOLD = 3.0   # WCAG AA minimum for large text is 3:1; below this is hard to read
SIZE_RATIO_THRESHOLD = 2.0     # accept button >2x the area of decline button


def detect_interface_interference(trace: FunnelTrace) -> List[Violation]:
    violations = []
    for state in trace.states:
        accepts = [b for b in state.buttons if b.role == "accept"]
        declines = [b for b in state.buttons if b.role == "decline"]
        if not accepts or not declines:
            continue

        accept, decline = accepts[0], declines[0]

        reasons = []
        if decline.contrast_ratio < LOW_CONTRAST_THRESHOLD:
            reasons.append(f"decline button contrast {decline.contrast_ratio}:1 "
                            f"(below WCAG AA {LOW_CONTRAST_THRESHOLD}:1 minimum)")

        if accept.bbox and decline.bbox:
            accept_area = accept.bbox["width"] * accept.bbox["height"]
            decline_area = decline.bbox["width"] * decline.bbox["height"]
            if decline_area > 0 and accept_area / decline_area >= SIZE_RATIO_THRESHOLD:
                reasons.append(f"accept button is {round(accept_area/decline_area, 1)}x "
                                f"the visual area of decline")

        if accept.font_size_px and decline.font_size_px:
            if accept.font_size_px / max(decline.font_size_px, 1) >= 1.5:
                reasons.append(f"accept text {accept.font_size_px}px vs decline "
                                f"{decline.font_size_px}px")

        if reasons:
            violations.append(Violation(
                pattern_code="DP-06", pattern_name=BY_CODE["DP-06"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(CORROBORATED, 0.9),
                evidence={
                    "evidence_tier": CORROBORATED,
                    "accept_text": accept.text, "decline_text": decline.text,
                    "accept_contrast": accept.contrast_ratio, "decline_contrast": decline.contrast_ratio,
                    "reasons": reasons,
                },
                layer=2,
                explanation=(f"'{accept.text}' is visually prominent while "
                             f"'{decline.text}' is suppressed: " + "; ".join(reasons)),
            ))
    return violations


DISCLOSURE_LOW_CONTRAST_THRESHOLD = 4.5   # WCAG AA for small text (stricter than the 3:1 used for large button text)
DISCLOSURE_SMALL_FONT_THRESHOLD_PX = 10.0


def detect_disguised_ad_disclosure(trace: FunnelTrace) -> List[Violation]:
    """
    DP-09 Disguised Advertisement -- v1, provable slice only.

    This project deliberately does NOT bundle a trained CV/YOLO model for
    this category (see README: attempted, blocked by this project's own
    dev sandbox having 2.9GB free disk and no reachable CPU-only PyTorch
    wheel index -- a real, verified constraint, not a policy choice). A
    full disguised-ad detector needs computer vision to judge whether an
    ad's visual STYLE mimics surrounding organic content, and that remains
    unbuilt.

    What IS provable without any model: an ad/sponsorship disclosure label
    that exists in the DOM but is deliberately hard to read -- tiny font,
    low contrast -- using the EXACT SAME WCAG math already used for the
    decline-button check in detect_interface_interference. A disclosure
    that's technically present but practically invisible is not a real
    disclosure, and that's checkable today with zero ML.
    """
    violations = []
    for state in trace.states:
        for label in state.disclosure_labels:
            reasons = []
            if label.contrast_ratio < DISCLOSURE_LOW_CONTRAST_THRESHOLD:
                reasons.append(f"contrast {label.contrast_ratio}:1 "
                                f"(below WCAG AA {DISCLOSURE_LOW_CONTRAST_THRESHOLD}:1 for small text)")
            if label.font_size_px and label.font_size_px < DISCLOSURE_SMALL_FONT_THRESHOLD_PX:
                reasons.append(f"font size {label.font_size_px}px "
                                f"(below {DISCLOSURE_SMALL_FONT_THRESHOLD_PX}px)")
            if reasons:
                violations.append(Violation(
                    pattern_code="DP-09", pattern_name=BY_CODE["DP-09"].name,
                    step_name=state.step_name,
                    confidence=clamp_confidence(CORROBORATED, 0.6),
                    evidence={"evidence_tier": CORROBORATED, "disclosure_text": label.text, "reasons": reasons,
                              "contrast_ratio": label.contrast_ratio, "font_size_px": label.font_size_px},
                    layer=2,
                    explanation=(f"Ad/sponsorship disclosure '{label.text}' is present in the DOM "
                                 f"but practically unreadable: " + "; ".join(reasons) + ". "
                                 f"NOTE: this only detects a hard-to-read disclosure -- it does not "
                                 f"(yet) verify visual mimicry of surrounding content, which needs "
                                 f"a trained CV model this project does not currently bundle."),
                ))
    return violations


def run_layer2(trace: FunnelTrace) -> List[Violation]:
    violations = []
    violations += detect_interface_interference(trace)
    violations += detect_disguised_ad_disclosure(trace)
    return violations
