"""
Layer 2: visual geometry. Uses REAL computed styles and bounding boxes from
the browser (not a vision model guessing) -- contrast ratio is the exact
WCAG 2.1 formula, button size comparison is exact pixel measurement. This
is provable math on rendered output, one tier down from Layer 1's DOM-state
proofs, but still not a "confidence score" in the ML sense.
"""

from dataclasses import dataclass
from typing import List

from .taxonomy import BY_CODE, CORROBORATED, INDICATIVE, clamp_confidence
from .layer1_rules import Violation
from capture.funnel_walker import FunnelTrace

LOW_CONTRAST_THRESHOLD = 3.0   # WCAG AA minimum for large text is 3:1; below this is hard to read
SIZE_RATIO_THRESHOLD = 4.0     # accept >=4x the area of decline, as in extension/src/detect.js
DWARFED_MAX_CONTRAST = 4.5     # ...and only counts while the decline is also below WCAG AA for text
FONT_RATIO_THRESHOLD = 1.5
# A decline is compared with a control NEXT to it. Pairing it with the
# nearest button anywhere on the page compared a "Close" in a cookie banner
# with "Checkout" half a screen away.
MAX_PAIR_DISTANCE_PX = 400


def _distance(a, b) -> float:
    if not a.bbox or not b.bbox:
        return float("inf")
    ax, ay = a.bbox["x"] + a.bbox["width"] / 2, a.bbox["y"] + a.bbox["height"] / 2
    bx, by = b.bbox["x"] + b.bbox["width"] / 2, b.bbox["y"] + b.bbox["height"] / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def detect_interface_interference(trace: FunnelTrace) -> List[Violation]:
    """DP-06: the way to say no is made hard to see next to the way to say yes.

    Every decline control is checked (not just the first one on the page),
    each against the nearest accept control within MAX_PAIR_DISTANCE_PX.

    Signals, mirroring the extension:
      * low contrast -- the decline is below 3:1, the WCAG floor;
      * dwarfed -- the accept is >=4x the decline's area AND the decline is
        below 4.5:1 (a large primary button next to a clearly readable small
        "Cancel" is ordinary design, not interference);
      * font -- the accept's text is >=1.5x the decline's, again only while
        the decline is below 4.5:1.
    One signal is INDICATIVE. Two agreeing signals are CORROBORATED.
    """
    violations = []
    for state in trace.states:
        declines = [b for b in state.buttons if b.role == "decline"]
        if not declines:
            continue
        others = [b for b in state.buttons if b.role != "decline"]
        seen = set()
        for decline in declines:
            if not decline.bbox:
                continue
            near = [b for b in others if _distance(b, decline) <= MAX_PAIR_DISTANCE_PX]
            # An explicitly marked accept if there is one nearby, else the
            # nearest other button.
            pool = [b for b in near if b.role == "accept"] or near
            if not pool:
                continue
            accept = min(pool, key=lambda b: _distance(b, decline))
            key = (decline.text, accept.text)
            if key in seen:
                continue
            seen.add(key)

            contrast = decline.contrast_ratio or 21.0
            reasons = []
            if contrast < LOW_CONTRAST_THRESHOLD:
                reasons.append(f"decline button contrast {decline.contrast_ratio}:1 "
                               f"(below the WCAG minimum of {LOW_CONTRAST_THRESHOLD}:1)")
            if accept.bbox:
                accept_area = accept.bbox["width"] * accept.bbox["height"]
                decline_area = decline.bbox["width"] * decline.bbox["height"]
                if (decline_area > 0 and accept_area / decline_area >= SIZE_RATIO_THRESHOLD
                        and contrast < DWARFED_MAX_CONTRAST):
                    reasons.append(f"accept button is {round(accept_area / decline_area, 1)}x "
                                   f"the visual area of decline")
            if (accept.font_size_px and decline.font_size_px and contrast < DWARFED_MAX_CONTRAST
                    and accept.font_size_px / max(decline.font_size_px, 1) >= FONT_RATIO_THRESHOLD):
                reasons.append(f"accept text {accept.font_size_px}px vs decline "
                               f"{decline.font_size_px}px")
            if not reasons:
                continue
            tier = CORROBORATED if len(reasons) >= 2 else INDICATIVE
            violations.append(Violation(
                pattern_code="DP-06", pattern_name=BY_CODE["DP-06"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(tier, 0.85 if tier == CORROBORATED else 0.5),
                evidence={
                    "evidence_tier": tier,
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
