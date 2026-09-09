"""
Flow-level detectors: the CCPA patterns that are properties of a JOURNEY
rather than of a single rendered page.

`layer1_rules.py` answers questions about one state, or about a price at
step 1 versus step 4. The five patterns here need something the per-page
rules structurally cannot see:

  DP-04 Forced Action     — what the page DEMANDS before it will proceed
  DP-05 Subscription Trap — the asymmetry between the signup and cancel paths
  DP-10 Nagging           — whether a refusal was honoured across page loads
  DP-12 SaaS Billing      — recurring consent, separated from one-off sneaking
  DP-13 Rogue Malware     — alert framing paired with an install call to action

Every detector states its evidence tier and routes its confidence through
`clamp_confidence()`, so none of them can report more certainty than the
kind of evidence it actually has. Two of the five are genuinely provable
from DOM state; the rest are explicitly not, and say so in their findings.
"""

import re
from typing import List, Optional

from .taxonomy import BY_CODE, PROVABLE, CORROBORATED, INDICATIVE, clamp_confidence
from .vocab import RECURRING_BILLING_TERMS, matches_any as _matches_any, is_recurring_label  # noqa: F401
from .layer1_rules import Violation
from capture.funnel_walker import FunnelTrace
from capture.state_extractor import PageState


# --- Shared vocabulary ----------------------------------------------------
#
# Kept as module-level constants rather than inline literals so the same
# definition is used by every detector that needs it, and so a test can
# assert on the lists directly. Grounded where possible in the category
# descriptions of AppRay's labelled corpus (Chen et al., TOSEM 2026,
# CC-BY-4.0), whose three largest categories — ForcedAction (433 instances),
# Nagging (190) and ForcedContinuity/RoachMotel (153) — are exactly the
# patterns implemented in this module. The corpus is Android app UI, so it
# is used as a specification of what these patterns LOOK like, never as
# training data for a model that would then be applied to the web.

# Purposes that are NOT necessary to complete a purchase or booking. A
# mandatory control demanding one of these is the operative fact for DP-04.
# Deliberately narrow: delivery address, payment details and terms-of-service
# acceptance are all genuinely required to transact and appear nowhere here.
UNRELATED_CONSENT_TERMS = [
    # "promotional?" parses as "promotiona" + optional "l", so it matched
    # "promotional" but never "promotion" or "promotions".
    r"\bmarketing\b", r"\bpromotions?\b", r"\bpromotional\b",
    r"\bnewsletter\b", r"\boffers? and updates?\b",
    r"\bthird[\s-]?part(y|ies)\b", r"\bpartners?\b", r"\bshare (my|your) (data|details|information)\b",
    r"\brefer a friend\b", r"\binvite (your )?(friends?|contacts?)\b",
    r"\baccess (to )?(your )?contacts?\b", r"\bfollow us\b",
    r"\bsms\b.*\boffers?\b", r"\bwhatsapp\b.*\b(offers?|updates?)\b",
    r"\bpersonalised? ads?\b", r"\btargeted ads?\b",
]

# Wording that makes a consent LAWFUL AND TRANSACTION-RELATED even though it
# mentions a term above. A privacy notice explaining that data is shared with
# third parties, or a checkbox acknowledging that delivery partners will make
# contact, are exactly what a compliant shop is required to show -- and both
# were being reported at PROVABLE 1.0 as consent "not required to complete the
# transaction". DP-04 is about consent bundled into a flow for an UNRELATED
# purpose; an acknowledgement of the terms under which the purchase itself
# happens is the opposite of that.
LAWFUL_CONSENT_CONTEXT = [
    r"\bprivacy (policy|notice)\b", r"\bterms (and conditions|of (service|use))\b",
    r"\bi have read\b", r"\backnowledge\b",
    r"\bdeliver(y|ies)\b.*\bpartners?\b", r"\bpartners?\b.*\bdeliver(y|ies)\b",
    r"\bcourier\b", r"\bshipment\b", r"\bfulfil(l)?ment\b",
    r"\brequired by law\b", r"\bgrievance\b",
]

# Framing that impersonates the operating system or a security product.
SYSTEM_ALERT_TERMS = [
    r"\bvirus (detected|found)\b", r"\byour (device|system|pc|phone|computer) is (infected|at risk)\b",
    r"\bmalware detected\b", r"\bsecurity (alert|warning|threat)\b",
    r"\bsystem (alert|warning|error)\b", r"\bcritical (alert|warning|error)\b",
    r"\byour (device|system|pc|phone|computer) (may be|is) (infected|compromised)\b",
    r"\b\d+ (viruses|threats|problems) (found|detected)\b",
    r"\bimmediate action required\b", r"\bwindows (defender|security)\b",
    r"\byour (flash player|browser) is out of date\b",
]

INSTALL_CTA_TERMS = [
    r"\bdownload now\b", r"\binstall now\b", r"\bscan now\b", r"\bclean now\b",
    r"\bfix now\b", r"\brepair now\b", r"\bupdate now\b", r"\bprotect now\b",
    r"\benable now\b",
]

EXECUTABLE_EXTENSIONS = (".exe", ".msi", ".apk", ".dmg", ".pkg", ".bat", ".scr", ".jar")

CANCEL_ROUTE_TERMS = [
    r"\bcancel (my )?(subscription|plan|membership|trial)\b",
    r"\bmanage (my )?(subscription|plan|membership|billing)\b",
    r"\bunsubscribe\b", r"\bend (my )?(subscription|membership|trial)\b",
    r"\bturn off auto[\s-]?renew\b",
]


# --- DP-04 Forced Action --------------------------------------------------

def detect_forced_action(trace: FunnelTrace) -> List[Violation]:
    """
    DP-04: the user cannot complete their actual task without doing something
    unrelated to it.

    Two independent detections, with deliberately different evidence tiers:

    (a) PROVABLE — a control the page itself marks `required` (or
        aria-required) whose label demands consent for a purpose unrelated to
        the transaction: marketing, third-party sharing, contact access,
        referrals. There is no judgement here. The page declares the control
        mandatory in its own markup, and the label states the purpose in its
        own words. Both are quoted in the evidence.

    (b) CORROBORATED — a blocking interstitial covering meaningful screen area
        with NO dismissal control this crawler could find. Tiered lower than
        (a) on purpose: "no way out exists" is a claim about absence, and
        absence is only ever as reliable as the recogniser. A site could use a
        dismissal affordance the DISMISS_PATTERNS list doesn't know about, and
        the honest response to that possibility is a lower tier, not a
        confident finding.
    """
    violations = []

    for state in trace.states:
        # (a) mandatory consent for an unrelated purpose
        mandatory_controls = [
            (cb.label_text, cb.id, "checkbox") for cb in state.checkboxes if cb.required
        ] + [
            (f.label_text, f.name, f.field_type) for f in state.form_fields
        ]
        for label_text, control_id, control_kind in mandatory_controls:
            matched = _matches_any(label_text, UNRELATED_CONSENT_TERMS)
            if not matched:
                continue
            # A lawful notice that happens to use one of those words is not
            # bundled marketing consent. "I have read the Privacy Policy,
            # including how my data is shared with third parties" matched
            # `third parties` and was reported at PROVABLE 1.0 as a purpose
            # "not required to complete the transaction" -- accusing a shop of
            # a dark pattern for showing the disclosure the law requires.
            if _matches_any(label_text, LAWFUL_CONSENT_CONTEXT):
                continue
            violations.append(Violation(
                pattern_code="DP-04", pattern_name=BY_CODE["DP-04"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(PROVABLE, 1.0),
                evidence={
                    "evidence_tier": PROVABLE,
                    "control_id": control_id,
                    "control_kind": control_kind,
                    "label_text": label_text,
                    "matched_purpose": matched,
                    "required_attribute_present": True,
                },
                layer=1,
                explanation=(
                    f"The control '{label_text}' at '{state.step_name}' is marked mandatory "
                    f"in the page's own markup, and its stated purpose ('{matched}') is not "
                    f"required to complete the transaction. Completing the task is "
                    f"conditioned on an unrelated consent."
                ),
            ))

        # (b) inescapable interstitial
        for modal in state.modals:
            if not modal.is_blocking or modal.dismiss_controls:
                continue
            if modal.viewport_coverage < 0.10:
                continue  # a small toast is an annoyance, not a forced action
            if getattr(modal, "contents_unreadable", False):
                # The overlay's content is in an iframe -- an ad, a consent
                # vendor, an embedded widget -- and a crawler cannot read into
                # another document. On a real automation sandbox this fired at
                # CORROBORATED 0.75 against a site whose overlay DID have a
                # close button; the crawler simply could not see it, and one
                # step later clicked its way past the same overlay.
                #
                # This finding's entire content is "there was no way out". You
                # cannot say that about a box you could not open. Skipping is
                # not caution for its own sake: the alternative is publishing
                # that a named site trapped its users, on the strength of the
                # crawler's own blind spot.
                continue
            violations.append(Violation(
                pattern_code="DP-04", pattern_name=BY_CODE["DP-04"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(CORROBORATED, 0.75),
                evidence={
                    "evidence_tier": CORROBORATED,
                    "modal_signature": modal.signature,
                    "viewport_coverage": modal.viewport_coverage,
                    "dismiss_controls_found": [],
                    "quoted_text": modal.text[:200],
                },
                layer=1,
                explanation=(
                    f"A blocking overlay covering {modal.viewport_coverage:.0%} of the viewport "
                    f"at '{state.step_name}' offered no dismissal control this crawler could "
                    f"identify, leaving compliance as the only visible way forward. Absence of "
                    f"a control is inherently harder to prove than its presence — confirm by eye "
                    f"against the step screenshot before relying on this finding."
                ),
            ))
    return violations


# --- DP-05 Subscription Trap ---------------------------------------------

def detect_subscription_trap(trace: FunnelTrace) -> List[Violation]:
    """
    DP-05: easy in, disproportionately hard out. Three detections.

    (a) CORROBORATED — recurring-charge terms that appear only AFTER the step
        presenting the offer. Two signals must agree: the offer step has no
        recurring language, and a later step does. A page that discloses
        "₹499/month, renews automatically" on the offer itself is not flagged,
        which is the correct outcome — clear disclosure is the compliant case.

    (b) CORROBORATED — the cancel path takes measurably more steps than the
        signup path, using the roles the adapter declared. Arithmetic on
        declared data, so not a guess; but an adapter author chooses what to
        label, so it is not pure DOM fact either.

    (c) INDICATIVE — a recurring commitment is offered and no cancellation
        route appears anywhere in the captured funnel. Reported as a prompt to
        check, never as a finding: a cancellation route may well exist behind
        a login this crawler never held credentials for.
    """
    violations = []

    # (a) recurring terms disclosed late
    offer_state, offer_index = _first_commitment_state(trace)
    if offer_state is not None:
        offer_disclosure = _matches_any(offer_state.full_text, RECURRING_BILLING_TERMS)
        if not offer_disclosure:
            for later in trace.states[offer_index + 1:]:
                late_disclosure = _matches_any(later.full_text, RECURRING_BILLING_TERMS)
                if late_disclosure:
                    violations.append(Violation(
                        pattern_code="DP-05", pattern_name=BY_CODE["DP-05"].name,
                        step_name=f"{offer_state.step_name} -> {later.step_name}",
                        confidence=clamp_confidence(CORROBORATED, 0.85),
                        evidence={
                            "evidence_tier": CORROBORATED,
                            "offer_step": offer_state.step_name,
                            "offer_step_disclosed_recurring": False,
                            "disclosure_step": later.step_name,
                            "quoted_text": late_disclosure,
                        },
                        layer=1,
                        explanation=(
                            f"The commitment step '{offer_state.step_name}' contained no "
                            f"recurring-charge disclosure, but '{later.step_name}' states "
                            f"'{late_disclosure}'. The repeating nature of the charge was not "
                            f"disclosed at the point the user was asked to commit."
                        ),
                    ))
                    break

    # (b) path asymmetry
    signup_steps = [n for n, role in trace.step_roles.items() if role == "signup"]
    cancel_steps = [n for n, role in trace.step_roles.items() if role == "cancel"]
    if signup_steps and cancel_steps and len(cancel_steps) > len(signup_steps):
        ratio = len(cancel_steps) / len(signup_steps)
        violations.append(Violation(
            pattern_code="DP-05", pattern_name=BY_CODE["DP-05"].name,
            step_name=f"{signup_steps[0]} -> {cancel_steps[-1]}",
            confidence=clamp_confidence(CORROBORATED, min(0.6 + 0.1 * ratio, 0.9)),
            evidence={
                "evidence_tier": CORROBORATED,
                "signup_step_count": len(signup_steps),
                "cancel_step_count": len(cancel_steps),
                "asymmetry_ratio": round(ratio, 2),
                "signup_steps": signup_steps,
                "cancel_steps": cancel_steps,
            },
            layer=1,
            explanation=(
                f"Starting the subscription took {len(signup_steps)} step(s); cancelling it took "
                f"{len(cancel_steps)} ({ratio:.1f}x more). CCPA's definition of Subscription Trap "
                f"turns on exactly this asymmetry between the ease of subscribing and the "
                f"difficulty of cancelling."
            ),
        ))

    # (c) no cancellation route anywhere
    #
    # Gated on a real PAID commitment, not on recurring words appearing
    # somewhere in the page text. The text test alone fired on an automation
    # sandbox with no subscription product at all: it has a newsletter box in
    # its footer, like nearly every shop alive, and billing vocabulary
    # elsewhere on the page was enough to conclude that a recurring commitment
    # existed with no way to cancel it.
    #
    # _first_commitment_state already knows a newsletter "Subscribe" is not a
    # commitment -- that exclusion was written for branch (a) and simply never
    # reached branch (c). Reusing it is both the correct rule and the one the
    # compliant corpus already covers.
    has_recurring = (
        offer_state is not None
        and any(_matches_any(s.full_text, RECURRING_BILLING_TERMS) for s in trace.states)
    )
    if has_recurring and not cancel_steps:
        route_found = False
        for state in trace.states:
            if _matches_any(state.full_text, CANCEL_ROUTE_TERMS):
                route_found = True
                break
            if any(_matches_any(link.text, CANCEL_ROUTE_TERMS) for link in state.links):
                route_found = True
                break
        if not route_found:
            violations.append(Violation(
                pattern_code="DP-05", pattern_name=BY_CODE["DP-05"].name,
                step_name=trace.states[-1].step_name if trace.states else "unknown",
                confidence=clamp_confidence(INDICATIVE, 0.45),
                evidence={
                    "evidence_tier": INDICATIVE,
                    "recurring_commitment_present": True,
                    "cancellation_route_found": False,
                    "steps_searched": [s.step_name for s in trace.states],
                },
                layer=1,
                explanation=(
                    "A recurring commitment is offered but no cancellation or subscription-"
                    "management route appeared anywhere in the captured funnel. This is an "
                    "absence check over the pages actually visited — a cancellation route may "
                    "exist behind authentication this audit did not traverse. Confirm manually "
                    "before treating it as a finding."
                ),
            ))
    return violations


# A "Subscribe" that signs you up for EMAILS is not a paid commitment, and
# almost every shop on the internet has one in its footer. Without this
# exclusion a newsletter box turned the page into a "commitment step", and
# DP-05 then reported at 0.85 that the recurring nature of a charge had been
# concealed -- on a site with no subscription product at all.
NON_COMMITMENT_SUBSCRIBE = [
    r"\bnewsletter\b", r"\bmailing list\b", r"\bemail(s)? updates?\b",
    r"\bsubscribe (to|for) (our |the )?(newsletter|updates?|emails?|blog|deals?|alerts?)\b",
    r"\bstay (updated|informed|in the loop)\b", r"\bget (our )?updates?\b",
    r"\bnotify me\b", r"\balerts?\b",
]

# A recurring PRICE: an amount tied to a period. This is what a paid plan has
# and a newsletter does not, and it is the only reliable way to tell the two
# apart when both buttons just say "Subscribe".
_RECURRING_PRICE_RE = re.compile(
    r"(?:₹|Rs\.?|INR|US\$|\$|€|£|¥)\s*[\d,]+(?:\.\d{1,2})?\s*"
    r"(?:/|per\s+|a\s+|each\s+)?\s*(?:mo\b|month|yr\b|year|week|annually)"
    r"|\b(?:billed|renews?|charged)\s+(?:monthly|annually|yearly|weekly)\b",
    re.IGNORECASE,
)

COMMITMENT_CTA = [
    r"\bstart (my )?(free )?trial\b", r"\bsubscribe\b", r"\bstart (my )?membership\b",
    r"\bget (started|premium|plus|pro)\b", r"\bupgrade\b", r"\bjoin now\b",
]


def _first_commitment_state(trace: FunnelTrace):
    """The earliest state that asks the user to commit to a PAID plan.

    Identified by the page's own call-to-action text, not by step name, so it
    works on sites whose step names this project never chose -- but a CTA that
    only signs the user up for emails is excluded, because a newsletter box is
    not a subscription trap and is present on nearly every shop.
    """
    for index, state in enumerate(trace.states):
        for button in state.buttons:
            if not _matches_any(button.text, COMMITMENT_CTA):
                continue
            if _matches_any(button.text, NON_COMMITMENT_SUBSCRIBE):
                continue
            # The button rarely carries the word "newsletter". On a real
            # storefront the footer reads:  <h4>Subscribe</h4>  <p>Get our
            # newsletter…</p>  <button>Subscribe</button>. The button text
            # alone is "Subscribe", which is indistinguishable from a paid
            # plan's CTA -- so an ordinary shop with no subscription product
            # was reported for offering a recurring commitment with no way to
            # cancel it.
            #
            # What actually separates the two is a PRICE. A paid plan states
            # what it costs and how often; a newsletter costs nothing. So a
            # recurring price makes it a commitment whatever the surrounding
            # words, and mailing-list language with no recurring price makes
            # it a newsletter.
            text = state.full_text or ""
            if _RECURRING_PRICE_RE.search(text):
                return state, index
            if _matches_any(text, NON_COMMITMENT_SUBSCRIBE):
                continue
            return state, index
    return None, -1


# --- DP-10 Nagging --------------------------------------------------------

def detect_nagging(trace: FunnelTrace) -> List[Violation]:
    """
    DP-10: asking again after being told no.

    (a) PROVABLE — this crawler dismissed an interruption at one step, and the
        SAME interruption (matched by signature, which normalises away
        rotating numbers) appeared again at a later step. A refusal is on
        record and the site ignored it. Nothing about this is inferred: both
        the dismissal and the reappearance are events the crawler performed
        and observed.

    (b) INDICATIVE — three or more distinct blocking interruptions across one
        funnel. Volume, not repetition, so it is not the legal definition;
        reported as context a reviewer may want, at low confidence.

    A prompt shown exactly once is deliberately NOT flagged. A site is
    entitled to ask; nagging begins at asking again.
    """
    violations = []

    dismissed_by_step = {}
    for step_name, signatures in trace.dismissed_modals.items():
        for signature in signatures:
            dismissed_by_step.setdefault(signature, []).append(step_name)

    step_order = {state.step_name: index for index, state in enumerate(trace.states)}

    for state in trace.states:
        for modal in state.modals:
            earlier_dismissals = [
                step for step in dismissed_by_step.get(modal.signature, [])
                if step_order.get(step, 99) < step_order.get(state.step_name, -1)
            ]
            if not earlier_dismissals:
                continue
            violations.append(Violation(
                pattern_code="DP-10", pattern_name=BY_CODE["DP-10"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(PROVABLE, 1.0),
                evidence={
                    "evidence_tier": PROVABLE,
                    "modal_signature": modal.signature,
                    "dismissed_at_steps": earlier_dismissals,
                    "reappeared_at_step": state.step_name,
                    "quoted_text": modal.text[:200],
                },
                layer=1,
                explanation=(
                    f"This interruption was actively dismissed at "
                    f"'{', '.join(earlier_dismissals)}' and reappeared at '{state.step_name}'. "
                    f"A repeated request after an explicit dismissal is the definition of "
                    f"Nagging under the 2023 Guidelines."
                ),
            ))

    blocking_signatures = {
        modal.signature
        for state in trace.states for modal in state.modals if modal.is_blocking
    }
    already_flagged = {v.evidence.get("modal_signature") for v in violations}
    if len(blocking_signatures) >= 3 and not blocking_signatures & already_flagged:
        violations.append(Violation(
            pattern_code="DP-10", pattern_name=BY_CODE["DP-10"].name,
            step_name=trace.states[-1].step_name if trace.states else "unknown",
            confidence=clamp_confidence(INDICATIVE, 0.4),
            evidence={
                "evidence_tier": INDICATIVE,
                "distinct_blocking_interruptions": len(blocking_signatures),
                "signatures": sorted(blocking_signatures),
            },
            layer=1,
            explanation=(
                f"{len(blocking_signatures)} distinct blocking interruptions appeared across this "
                f"funnel. This measures volume rather than repetition after refusal, so it does "
                f"not by itself meet the definition of Nagging — included as reviewer context."
            ),
        ))
    return violations


# --- DP-12 SaaS Billing ---------------------------------------------------

def detect_saas_billing(trace: FunnelTrace) -> List[Violation]:
    """
    DP-12: a repeating charge the user never affirmatively agreed to.

    PROVABLE — a checkbox or radio that is already selected on first page
    load, whose own label carries recurring-billing language. The selection
    state is read from the DOM before any interaction; the billing language is
    quoted from the label. Both facts are in the evidence.

    Kept strictly separate from DP-02 (Basket Sneaking), which covers one-off
    add-ons. `layer1_rules` skips any pre-ticked control this detector claims,
    so a single pre-selected annual plan produces exactly one finding rather
    than two. Radio groups are the common shape here — a pre-selected "Premium
    ₹999/year" radio is the textbook case, and a checkbox-only extractor would
    have missed all of them.
    """
    violations = []
    seen = set()
    for state in trace.states:
        for control in state.checkboxes:
            if not control.is_prechecked:
                continue
            matched = _matches_any(control.label_text, RECURRING_BILLING_TERMS)
            if not matched:
                continue
            identity = (control.id, control.label_text)
            if identity in seen:
                continue  # same physical control across SPA steps — see DP-02's dedupe note
            seen.add(identity)
            violations.append(Violation(
                pattern_code="DP-12", pattern_name=BY_CODE["DP-12"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(PROVABLE, 1.0),
                evidence={
                    "evidence_tier": PROVABLE,
                    "control_id": control.id,
                    "control_kind": control.input_type,
                    "label_text": control.label_text,
                    "matched_billing_term": matched,
                    "selected_on_first_load": True,
                },
                layer=1,
                explanation=(
                    f"The {control.input_type} '{control.label_text}' was already selected on "
                    f"page load at '{state.step_name}', and its label commits the user to a "
                    f"recurring charge ('{matched}'). A repeating payment was opted into on the "
                    f"user's behalf rather than affirmatively agreed to."
                ),
            ))
    return violations


# --- DP-13 Rogue Malware --------------------------------------------------

def detect_rogue_malware_framing(trace: FunnelTrace) -> List[Violation]:
    """
    DP-13: a prompt dressed up as a system or security alert to push an install.

    This detector is INDICATIVE by construction and says so in every finding.
    What it can see is PRESENTATION: whether a page frames something as an
    operating-system or antivirus warning and pairs it with an install call to
    action, or offers an executable download behind innocuous anchor text.
    What it cannot see, and never claims, is whether any file is actually
    malicious — this project performs no binary analysis, no hash lookup and
    no sandbox detonation.

    That limitation is stated rather than hidden because DP-13 is the one
    category where an overconfident automated finding would be genuinely
    defamatory: accusing a business of distributing malware is a different
    order of allegation from accusing it of a pre-ticked checkbox.
    """
    violations = []
    for state in trace.states:
        # (1) alert framing + install CTA, scoped to a single element so the
        #     two signals must genuinely co-occur rather than merely appear on
        #     the same long page.
        candidates = [(m.text, m.signature, "modal") for m in state.modals]
        for text, signature, source in candidates:
            alert_match = _matches_any(text, SYSTEM_ALERT_TERMS)
            cta_match = _matches_any(text, INSTALL_CTA_TERMS)
            if not (alert_match and cta_match):
                continue
            violations.append(Violation(
                pattern_code="DP-13", pattern_name=BY_CODE["DP-13"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(INDICATIVE, 0.6),
                evidence={
                    "evidence_tier": INDICATIVE,
                    "source": source,
                    "signature": signature,
                    "alert_framing_quote": alert_match,
                    "install_cta_quote": cta_match,
                    "binary_analysis_performed": False,
                },
                layer=1,
                explanation=(
                    f"An interruption at '{state.step_name}' uses system/security-alert framing "
                    f"('{alert_match}') together with an install call to action "
                    f"('{cta_match}'). This describes the PRESENTATION only. No file was "
                    f"downloaded, inspected or analysed, and no claim is made that any software "
                    f"here is malicious."
                ),
            ))

        # (2) executable download behind non-executable-sounding anchor text
        for link in state.links:
            href_lower = link.href.lower().split("?")[0]
            if not href_lower.endswith(EXECUTABLE_EXTENSIONS):
                continue
            anchor = link.text.strip().lower()
            announces_itself = any(
                word in anchor for word in ("download", "install", ".exe", ".apk", "setup", "installer")
            )
            if announces_itself:
                continue  # an honestly labelled download is not a dark pattern
            violations.append(Violation(
                pattern_code="DP-13", pattern_name=BY_CODE["DP-13"].name,
                step_name=state.step_name,
                confidence=clamp_confidence(INDICATIVE, 0.5),
                evidence={
                    "evidence_tier": INDICATIVE,
                    "anchor_text": link.text,
                    "href": link.href,
                    "binary_analysis_performed": False,
                },
                layer=1,
                explanation=(
                    f"The link '{link.text}' at '{state.step_name}' points to an executable "
                    f"({link.href}) without saying so in its own text, so a user cannot tell "
                    f"from the link what activating it will do. No claim is made about the "
                    f"contents of that file."
                ),
            ))
    return violations


def run_layer1_flow(trace: FunnelTrace) -> List[Violation]:
    violations = []
    violations += detect_forced_action(trace)
    violations += detect_subscription_trap(trace)
    violations += detect_nagging(trace)
    violations += detect_saas_billing(trace)
    violations += detect_rogue_malware_framing(trace)
    return violations
