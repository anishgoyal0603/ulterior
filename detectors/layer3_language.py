"""
Layer 3: language, for the two CCPA patterns that are properties of WORDING
rather than of DOM state or geometry — Confirm Shaming (DP-03) and Trick
Wording (DP-11).

## Offline by default, LLM by explicit request

This layer used to switch on whether an API key happened to be present in
the environment: key set, LLM; key absent, a "stub". That made the tool's
behaviour depend on ambient configuration, which is a bad property for
something producing compliance findings — the same page could yield
different results on two machines for reasons nobody could see, and the
word "stub" undersold detection validated at 92.9% against real data.

So the contract is now explicit:

  * The offline detectors ALWAYS run. They are deterministic, cost nothing,
    send nothing anywhere, and are the ones whose accuracy has actually been
    measured against a real dataset.
  * The LLM pass runs only when an audit is created with `use_llm=true` AND
    a key is configured. It ADDS findings the offline pass missed; it can
    never remove or overrule one.
  * Every LLM finding carries `"source": "anthropic_api"` in its evidence, so
    a reader can always tell which findings a model contributed to and
    discount them independently if they wish.

## Grounding rule, unchanged

Every verdict MUST quote a verbatim span from the actual page text. If a
claimed quote is not literally present in the source, the verdict is
rejected outright rather than downgraded. A model that invents its evidence
gets no partial credit.
"""

import json
import os
import re
from typing import List, Optional

from .taxonomy import BY_CODE, CORROBORATED, INDICATIVE, clamp_confidence
from .layer1_rules import Violation
from capture.funnel_walker import FunnelTrace
from capture.state_extractor import ButtonInfo, PageState
from app.privacy import redact

SYSTEM_PROMPT = """You are auditing an e-commerce/travel checkout flow for two specific \
dark patterns defined under India's CCPA Dark Patterns Guidelines, 2023:

1. CONFIRM SHAMING (DP-03): an opt-out/decline button worded to guilt, shame, or embarrass \
the user for declining (e.g. "No thanks, I like paying full price").
2. TRICK WORDING (DP-11): confusing phrasing, double negatives, or ambiguity that could cause \
a user to act against their actual intent.

You will be given the text of one or more buttons/links from a single page. Respond ONLY with \
valid JSON in this exact shape:
{
  "violations": [
    {
      "pattern_code": "DP-03 or DP-11",
      "quoted_text": "the EXACT verbatim button/link text that is the violation",
      "confidence": 0.0 to 1.0,
      "explanation": "one sentence"
    }
  ]
}
Return an empty "violations" list if neither pattern is present. Do not include any text outside the JSON."""


# --- DP-03 Confirm Shaming, offline --------------------------------------

GUILT_PATTERNS = [
    r"\bi don'?t (like|want|need|care about|mind|hate|despise)\b",
    r"\bi'?d?\s*(rather|prefer)\s*(to\s*)?(not\s+)?(pay|have|save|win)\b",
    r"\bi don'?t feel lucky\b",
    r"\bi want to pay (more|the full price)\b",
    r"\bi do not want\b",
    r"\baccept (all )?(the )?risk\b",
    r"\bwithout (a )?(protection|insurance|discount)\b",
    r"\bi'?ll (risk|let this offer|be the last)\b",
    r"pay(ing)?\s*full price",
    r"like (paying )?full price",
    r"don'?t (mind )?miss(ing)? out",
    r"don'?t want \d+%",
    r"'?d like \d+% off",
    r"not interested\b",
    r"\bhate (saving|cool)\b",
    r"i'?ve got too much\b",
]

# --- DP-11 Trick Wording, offline ----------------------------------------
#
# Double negatives are the tractable, provable core of this category, and
# they are exactly what a regex is good at. The archetype — "Uncheck this box
# if you do not wish to receive offers" — requires a user to perform a
# negative action to express a negative preference, which is precisely the
# confusion CCPA's definition describes.
#
# Kept deliberately narrow. A vague or merely wordy sentence is NOT trick
# wording, and a detector that fired on ordinary marketing prose would bury
# the real findings in noise and discredit the report.

DOUBLE_NEGATIVE_PATTERNS = [
    r"\bunche?ck\b[^.]{0,60}\b(if|to)\b[^.]{0,40}\b(not|don'?t|do not|no longer|never)\b",
    r"\buntick\b[^.]{0,60}\b(if|to)\b[^.]{0,40}\b(not|don'?t|do not)\b",
    r"\b(do not|don'?t)\b[^.]{0,40}\b(unche?ck|untick|opt out)\b",
    r"\b(not|never)\b[^.]{0,30}\bdisagree\b",
    r"\bopt out\b[^.]{0,40}\b(not|stop|prevent)\b[^.]{0,40}\breceiv",
    r"\bdisagree\b[^.]{0,30}\b(not|never)\b",
    r"\bdecline\b[^.]{0,30}\bnot\b[^.]{0,30}\b(receive|participate)\b",
]

# Wording that means "do not do the thing" — an OPT-OUT. When a control
# carrying this wording arrives already ticked, the pre-tick and the negative
# label point in opposite directions and the user's actual state is the
# opposite of what a glance suggests.
OPT_OUT_LABEL_PATTERNS = [
    r"\bdo not\b", r"\bdon'?t\b", r"\bopt out\b", r"\bunsubscribe\b",
    r"\bno longer\b", r"\bstop (sending|receiving)\b", r"\bexclude me\b",
]


def _offline_confirm_shaming(button_texts: List[str]) -> List[dict]:
    """
    Deterministic DP-03 detection.

    Validated against Mathur et al. (2019) CSCW "Dark Patterns at Scale" real
    checkout-crawl data (169 real Confirmshaming instances from 11K shopping
    sites). Real-data testing moved this from a substring approach at 52.1%
    to this regex approach at 92.9% — see tests/test_real_world_validation.py,
    which re-runs the check on every CI run so the number cannot silently
    regress. The remaining ~7% is long-tail creative copy ("I love chaos!")
    that no regex generalises to; closing that gap is the entire reason the
    optional LLM pass exists.
    """
    found = []
    for text in button_texts:
        normalized = text.replace("’", "'").replace("‘", "'")
        if len(text) > 8 and any(re.search(p, normalized, re.IGNORECASE) for p in GUILT_PATTERNS):
            found.append({
                "pattern_code": "DP-03", "quoted_text": text,
                "confidence": 0.6,
                "explanation": "Decline option is worded to attach guilt or regret to declining "
                               "(regex list validated at 92.9% against Mathur et al. 2019 real data).",
            })
    return found


def _offline_trick_wording(state: PageState) -> List[dict]:
    """
    Deterministic DP-11 detection — the offline capability this project
    previously did not have at all, which left DP-11 dependent on an API key
    being present. Two independent signals:

    (a) A double negative in a control's own label or nearby text: the user
        must take a negative action to express a negative preference.
    (b) An inverted opt-out: a control that is pre-ticked while its label
        means "do not". The tick and the wording point in opposite
        directions, so the user's real state is the opposite of what the
        control appears to say.

    (b) is the stronger of the two — it combines DOM state with wording, so
        two independent things had to agree — and is reported at higher
        confidence accordingly.
    """
    found = []

    for control in state.checkboxes:
        label = control.label_text or ""
        if not label:
            continue

        double_negative = None
        for pattern in DOUBLE_NEGATIVE_PATTERNS:
            match = re.search(pattern, label.replace("’", "'"), re.IGNORECASE)
            if match:
                double_negative = match.group(0)
                break
        if double_negative:
            found.append({
                "pattern_code": "DP-11", "quoted_text": label,
                "confidence": 0.7,
                "explanation": (
                    f"The control's label requires a negative action to express a negative "
                    f"preference (\"{double_negative}\"), so a user reading it quickly is likely "
                    f"to end up with the opposite of their intent."
                ),
            })
            continue

        if control.is_prechecked and any(
            re.search(p, label.replace("’", "'"), re.IGNORECASE) for p in OPT_OUT_LABEL_PATTERNS
        ):
            found.append({
                "pattern_code": "DP-11", "quoted_text": label,
                "confidence": 0.8,
                "explanation": (
                    "This control arrived already ticked while its label is phrased as an "
                    "opt-out, so the tick and the wording point in opposite directions and the "
                    "user's actual state is the opposite of what the control appears to say."
                ),
            })

    # Buttons carry the same problem — "Cancel" on a dialog asking "Don't you
    # want to cancel?" is the classic case.
    for button in state.buttons:
        for pattern in DOUBLE_NEGATIVE_PATTERNS:
            match = re.search(pattern, (button.text or "").replace("’", "'"), re.IGNORECASE)
            if match:
                found.append({
                    "pattern_code": "DP-11", "quoted_text": button.text,
                    "confidence": 0.65,
                    "explanation": (
                        f"Control text contains a double negative (\"{match.group(0)}\"), which "
                        f"obscures what activating it actually does."
                    ),
                })
                break
    return found


def _safe_confidence(candidate: dict) -> float:
    """A model-supplied confidence must never be able to abort the audit.

    This float() sat outside the try/except around the LLM call, so a model
    returning "confidence": null raised TypeError and "confidence": "high"
    raised ValueError -- and the whole audit died at the last step, after the
    browser work was already done, because of a field the model made up.
    """
    try:
        value = float(candidate.get("confidence", 0.6))
    except (TypeError, ValueError):
        return 0.6
    if value != value:            # NaN
        return 0.6
    return min(max(value, 0.0), 1.0)


def _call_anthropic(button_texts: List[str]) -> dict:
    """
    THIRD-PARTY DATA EGRESS — this is the ONLY point in the entire app where
    captured page content leaves the machine, and it now happens only when an
    audit explicitly asked for it.

    What is sent: ONLY the text of buttons/links whose CSS class marks them as
    a decline/opt-out control. Not the page HTML, not the full page text, not
    screenshots, not URLs, not prices, not any user session data. Typically
    1-2 short strings per page.

    Why so little: confirm shaming and trick wording are properties of the
    decline option's WORDING alone. Sending more would give the model no
    additional signal and would widen the egress surface for nothing.

    Each string is passed through redact() first, so if a decline label ever
    contains personalised content ("No thanks Anish, I don't want..."), the
    PII is stripped before transmission.
    """
    import anthropic
    safe_texts = [redact(t) for t in button_texts]
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=500, system=SYSTEM_PROMPT,
        messages=[{"role": "user",
                   "content": "Button/link texts on this page:\n"
                              + "\n".join(f"- {t}" for t in safe_texts)}],
    )
    return json.loads(resp.content[0].text.strip())


def detect_language_patterns(trace: FunnelTrace, use_llm: bool = False) -> List[Violation]:
    """Offline detectors always run; the LLM pass only adds to them."""
    violations: List[Violation] = []
    llm_available = bool(os.environ.get("ANTHROPIC_API_KEY"))
    llm_requested = bool(use_llm)

    for state in trace.states:
        decline_texts = [b.text for b in state.buttons if b.role == "decline"]

        candidates = _offline_confirm_shaming(decline_texts) + _offline_trick_wording(state)
        sources = {id(c): "offline_rules" for c in candidates}

        if llm_requested and llm_available and decline_texts:
            try:
                extra = _call_anthropic(decline_texts).get("violations", [])
                for item in extra:
                    sources[id(item)] = "anthropic_api"
                candidates += extra
            except Exception:
                # A failed API call must never fail the audit -- the offline
                # findings above are already valid on their own.
                pass

        seen = set()
        for candidate in candidates:
            quoted = (candidate.get("quoted_text") or "").strip()
            # GROUNDING: reject anything not literally present on the page.
            if not quoted or quoted.lower() not in state.full_text.lower():
                continue
            code = candidate.get("pattern_code", "DP-03")
            if code not in BY_CODE:
                continue
            identity = (code, quoted.lower())
            if identity in seen:
                continue  # offline and LLM found the same thing -- report once
            seen.add(identity)

            tier = CORROBORATED if code == "DP-11" else INDICATIVE
            violations.append(Violation(
                pattern_code=code, pattern_name=BY_CODE[code].name,
                step_name=state.step_name,
                confidence=clamp_confidence(tier, _safe_confidence(candidate)),
                evidence={
                    "evidence_tier": tier,
                    "quoted_text": quoted,
                    "source": sources.get(id(candidate), "offline_rules"),
                },
                layer=3,
                explanation=candidate.get("explanation", ""),
            ))
    return violations


def run_layer3(trace: FunnelTrace, use_llm: bool = False) -> List[Violation]:
    return detect_language_patterns(trace, use_llm=use_llm)
