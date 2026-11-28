"""
What each of the 13 patterns could actually be checked on, for one audit.

A report that lists only findings invites the reader to treat silence as a
clean result. Silence only means "not observed" when the pages a pattern
needs were actually in the walk: drip pricing cannot be seen without a
checkout total, basket sneaking without a cart. So every audit records, for
every pattern, one of:

  observed            at least one finding
  not_observed        the pages it needs were inspected and nothing was found
  partially_assessed  only part of the pattern can be checked by this tool
                      (no finding, and the report says which part)
  not_assessed        the pages it needs were not reached; the reason is given

The page requirements are deliberately simple and visible here, so a reader
can check them against the walk itself.
"""
from typing import Iterable, List

from detectors.taxonomy import DARK_PATTERNS

_SUBSCRIPTION_WORDS = ("subscri", "free trial", "trial", "auto-renew", "autorenew", "membership",
                       "cancel", "renews")
_BILLING_WORDS = ("per month", "/month", "/mo", "monthly", "per year", "/year", "annual", "billing",
                  "plan", "seat", "renew")


def _text(states) -> str:
    return " ".join((getattr(s, "full_text", "") or "").lower() for s in states)


def assess_coverage(trace, observed_codes: Iterable[str]) -> List[dict]:
    states = list(getattr(trace, "states", []) or [])
    observed = set(observed_codes or [])
    n = len(states)
    any_page = n > 0
    priced = [s for s in states if getattr(s, "price", None) is not None]
    totals = [s for s in states if getattr(s, "line_items", None)]
    with_boxes = [s for s in states if getattr(s, "checkboxes", None)]
    # DP-03 and DP-06 judge a DECLINE option (its wording, its prominence
    # next to the accept option). A page with buttons but no decline option
    # gave those detectors nothing to evaluate -- that is "not assessed",
    # not "not observed".
    def _declines(s):
        return [b for b in (getattr(s, "buttons", []) or []) if getattr(b, "role", "") == "decline"]
    with_buttons = [s for s in states if _declines(s)]
    # A decline AND something else to compare it with: two declines and no
    # accept option gave DP-06 nothing to measure.
    with_pairs = any(_declines(s) and any(getattr(b, "role", "") != "decline"
                                          for b in (getattr(s, "buttons", []) or []))
                     for s in states)
    # DP-08 runs only when the LAST priced page states a total (see
    # detect_drip_pricing). Anything less and the detector returned early,
    # yet the report used to say "Not observed".
    from detectors.layer1_rules import _STATES_A_TOTAL
    drip_checked = len(priced) >= 2 and bool(_STATES_A_TOTAL.search(getattr(priced[-1], "full_text", "") or ""))
    reloads = bool(getattr(trace, "reload_pairs", None))
    roles = set((getattr(trace, "step_roles", {}) or {}).values())
    text = _text(states)

    def need(cond, reason_if_not):
        return (True, "") if cond else (False, reason_if_not)

    rules = {
        "DP-01": need(any_page and reloads,
                      "No page was reloaded to test whether a timer or stock count is real."
                      if any_page else "No page was captured."),
        "DP-02": need(bool(with_boxes or totals), "No cart or checkout page (options or line items) was reached."),
        "DP-03": need(bool(with_buttons), "No decline option (\"No thanks\", \"Skip\", ...) was found to read."),
        "DP-04": need(any_page, "No page was captured."),
        "DP-05": need("cancel" in roles or "signup" in roles or any(w in text for w in _SUBSCRIPTION_WORDS),
                      "No subscription, trial or cancellation page was in scope."),
        "DP-06": need(with_pairs, "No decline option next to an accept option was found to compare."),
        "DP-07": need(len(priced) >= 2, "Fewer than two priced pages, so an advertised price could not be "
                                        "compared with a later one."),
        "DP-08": need(drip_checked,
                      "No first price followed by a page stating a total (cart or checkout) was captured."),
        "DP-09": need(any_page, "No page was captured."),
        "DP-10": need(n >= 2, "Only one page was captured, so a repeated prompt could not be seen."),
        "DP-11": need(bool(with_boxes), "No page with checkboxes or consent options was captured."),
        "DP-12": need(any(w in text for w in _BILLING_WORDS) or bool(with_boxes and "plan" in text),
                      "No plan, renewal or billing page was in scope."),
        "DP-13": need(any_page, "No page was captured."),
    }
    partial = {
        "DP-09": "Checks only whether ad labels are readable, not whether an ad imitates ordinary content.",
        "DP-13": "Checks how warnings and download prompts are presented; no file is downloaded or analysed.",
    }

    out = []
    for p in DARK_PATTERNS:
        assessed, reason = rules.get(p.code, (any_page, "No page was captured."))
        if p.code in observed:
            status, note = "observed", ""
        elif not assessed:
            status, note = "not_assessed", reason
        elif p.code in partial:
            status, note = "partially_assessed", partial[p.code]
        else:
            status, note = "not_observed", ""
        out.append({"code": p.code, "name": p.name, "status": status, "note": note})
    return out


STATUS_LABEL = {
    "observed": "Observed",
    "not_observed": "Not observed",
    "partially_assessed": "Partially assessed",
    "not_assessed": "Not assessed",
}
