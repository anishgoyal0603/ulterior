# Taxonomy Crosswalk — CCPA-13 vs. DPAF-68

> **Status update — all 13 categories now have a detector.** The table below
> is preserved as the record of how the gap was found and closed; the
> "Detector status" column reflects the state *before* the all-13 build.
> Current, authoritative coverage lives in `detectors/taxonomy.py` and is
> served live at `GET /coverage`. What changed:
>
> | Was | Now | Evidence tier |
> |---|---|---|
> | DP-04 Forced Action — no detector | mandatory unrelated-consent controls + inescapable interstitials | provable / corroborated |
> | DP-05 Subscription Trap — no detector | late recurring disclosure, signup-vs-cancel path asymmetry, missing cancellation route | corroborated / indicative |
> | DP-10 Nagging — no detector | crawler dismisses interruptions and detects reappearance after refusal | provable |
> | DP-11 Trick Wording — LLM-only | offline double-negative and inverted-opt-out detection | corroborated |
> | DP-12 SaaS Billing — no detector | pre-selected control (radio or checkbox) with recurring-billing label | provable |
> | DP-13 Rogue Malware — no detector | alert framing + install CTA, and executables behind innocuous anchor text | indicative |
>
> **Two things this build refused to do**, both worth stating because the
> temptation was real:
>
> 1. **Present weak coverage as strong.** Covering all 13 creates pressure to
>    make the table look uniform. Evidence tiers exist so it doesn't: DP-13 is
>    capped at 0.6 and says in every finding that no binary analysis was
>    performed, while DP-12 reaches 1.0 because a checkbox's state on first
>    load is not a matter of opinion.
> 2. **Train a model on AppRay for DP-09.** The annotations were obtained and
>    analysed (876 images, 97 apps, 2,185 instances, 18 categories). Disguised
>    Ad has only 107 instances across 96 images, every image is an Android
>    screenshot at 1440x2960, and the images themselves are not retrievable.
>    A thin single-class model trained on app UI, applied to web checkouts,
>    would not survive one informed question. AppRay's three *largest*
>    categories — ForcedAction (433), Nagging (190), ForcedContinuity/
>    RoachMotel (153) — were used instead as a published specification of
>    what those patterns look like, and DP-04, DP-10 and DP-05 were written
>    from them. No GPU, no training run, and a stronger claim.

Source for the 68-type side: Li, Wang, Nie, Li, Liu, Zhao, Xue, Said,
"A Comprehensive Study on Dark Patterns" (DPAF), arXiv:2412.09147.

This document exists to answer one question honestly: **of the 13 patterns
this project claims to audit against, how many actually have working
detection code?** The answer, before this cross-reference, was 5 of 13 —
not stated anywhere in the README until now.

## Full crosswalk

| CCPA-13 | DPAF-68 equivalent(s) | Detector status |
|---|---|---|
| DP-01 False Urgency | SE-HD, SE-LS, SE-CT, SE-LT, SE-AM, SE-PP | ✅ `detect_fake_urgency` — real-data validated 94.1% |
| DP-02 Basket Sneaking | SN-SI | ✅ `detect_basket_sneaking_and_prechecked` |
| DP-03 Confirm Shaming | SE-CO | ✅ `detect_language_patterns` — real-data validated 92.9% |
| DP-04 Forced Action | FA-FR, FA-FS, FA-AB, FA-SP | ❌ **No detector.** Defined in `taxonomy.py`, never implemented |
| DP-05 Subscription Trap | FA-FC, OB-IA, OB-DE | ❌ **No detector** for the core Hard-to-Cancel case |
| DP-06 Interface Interference | II-FH, II-VP, II-BD, II-SO | ✅ `detect_interface_interference` (II-SO close-button-size not explicitly separated) |
| DP-07 Bait and Switch | SN-DA | ✅ **Implemented** — built directly from CCI's own description of its MakeMyTrip/Goibibo inquiry (see `data/real_world_validation/india_regulatory_cases.py`) |
| DP-08 Drip Pricing | SN-DP | ✅ `detect_drip_pricing` |
| DP-09 Disguised Advertisement | SN-DA | ⚠️ **v1 implemented** — disclosure-label contrast/size check (provable, no CV). Does NOT yet verify visual mimicry of surrounding content; that needs a trained model — see below for why that's not bundled yet |
| DP-10 Nagging | NG | ❌ **No detector.** Needs cross-session/repeated-popup tracking |
| DP-11 Trick Wording | II-TQ, II-CL, II-WL, II-FA | ⚠️ Routes to LLM only — **no offline-stub heuristic** (confirm-shaming has one, trick-wording doesn't) |
| DP-12 SaaS Billing | II-BD (overlap) | ❌ **No detector.** Overlaps with basket-sneaking logic but not wired up |
| DP-13 Rogue Malware | *(not in DPAF-68 at all)* | Lowest priority — not really a checkout-flow pattern |

**Honest summary: 6 of 13 fully implemented** (Bait and Switch added — see
below), 1 partially (LLM-only, no offline fallback), 6 with zero detection
code.

## Real regulatory case grounding — India-specific, not a live crawl

`data/real_world_validation/india_regulatory_cases.py` catalogs 4 real,
publicly reported regulatory findings specific to India's travel/
e-commerce sector: CCPA penalties against 9 platforms (disclosed to the
Rajya Sabha), a CCI inquiry into MakeMyTrip/Goibibo pricing practices,
CCPA notices for basket sneaking/false urgency, and an industry B-Index
report. **This is explicitly NOT a live crawl of any named company** — see
the module's own docstring for why, and the Railway/ToS note above for
the reasoning. It's the same "case resonance" grounding pattern used in
the SIH26165 project: a live audit's output gets compared against
documented precedent, not treated as if it discovered something itself.

**One finding worth stating plainly, because it cuts against an
assumption:** the cited B-Index report ranks MakeMyTrip among the
*safest* travel platforms (9.4) and Cleartrip as the most harmful (85.2).
Don't let a pitch imply MakeMyTrip is a bad actor — the actual public data
says the opposite, and misstating this in front of judges who might
check is a real credibility risk.

**DP-07 Bait and Switch was implemented directly from this research** —
the CCI's own description of the MakeMyTrip/Goibibo inquiry ("false
discounting and misleading price representations") gave an exact,
operationalizable definition: a button/link making a specific, checkable
price claim that the next page doesn't honor. See
`detect_bait_and_switch` in `detectors/layer1_rules.py`.


## DPAF types with no CCPA-13 equivalent — added as "beyond CCPA-13" extras

Three were cheap enough to implement immediately (pure DOM/text rules, no
LLM or CV needed) and are now in `detectors/layer1_rules.py`, tagged
`DPAF-*` rather than `DP-*` so a report never conflates them with the 13
legally-defined categories:

- **OB-IA (Immortal Accounts)** — `detect_immortal_account`: flags an
  account/settings page with no visible account-deletion option. Absence
  check, deliberately scoped to only fire on pages that already look like
  a settings page, so it doesn't fire on every unrelated page.
- **SN-RP (Reference Pricing)** — `detect_reference_pricing`: flags an
  implausibly large claimed discount (>70%) as a fake-original-price
  candidate. Flags for human cross-reference against real price history;
  does not assert fraud outright, since this tool has no access to a
  price-history database.
- **OB-PC (Price Comparison Prevention)** — `detect_price_comparison_prevention`:
  flags CSS/JS that disables text selection or right-click over product
  content, which blocks copying the name/price to compare elsewhere.

All three are covered by tests in `tests/test_pipeline_e2e.py`, including
false-positive guards (a normal 20% discount is not flagged; an account
page WITH a delete option is not flagged; an unrelated page is not flagged).

## What this crosswalk changes about the pitch

Don't say "we detect 13 dark patterns." Say: **"we fully detect 5 of
CCPA's 13 categories today, with a documented path to the remaining 8,
and we've already extended past CCPA's list into 3 additional patterns
found by cross-referencing the most granular published taxonomy (DPAF,
68 types) against our own."** That's a stronger, more defensible claim —
it shows the gap was found by rigorous cross-referencing, not glossed
over, and that the project is actively closing it rather than claiming
completeness it doesn't have.

## Recommended build order for the remaining 7

Cheapest/highest-value first:

1. **DP-05 Subscription Trap (Hard to Cancel)** — click-depth-to-cancel
   counter, reusing the funnel-walker's existing step-tracking.
2. **DP-11 Trick Wording offline stub** — same pattern as confirm-shaming's
   offline stub (double-negative detection is regex-tractable: "don't...
   uncheck", "not... disagree").
3. **DP-04 Forced Action** — detect a required action (share, invite,
   provide contacts) gating access to an unrelated feature.
4. **DP-07 Bait and Switch** — needs the funnel trace to compare the
   *stated* destination of a click against the *actual* resulting page;
   architecturally feasible with what's already captured, just not built.
5. **DP-10 Nagging** — needs repeated-visit tracking (same popup across
   multiple page loads/sessions), a genuinely new capability.
6. **DP-12 SaaS Billing** — lowest remaining priority.

## Disguised Ad (DP-09) — attempted real YOLO, hit a real wall, shipped a provable v1 instead

Tried to add a trained YOLO11/YOLO26 classifier for this category (this
project's own prior recommendation, replacing an earlier suggestion of
YOLOv12x once checked against Ultralytics' current docs — YOLO12 is now
"maintained primarily for benchmarking," not recommended for production).
Blocked by two verified, not assumed, constraints in this project's dev
sandbox: **2.9GB free disk**, and PyTorch's CPU-only wheel index isn't
reachable (not on the network allowlist) — meaning `pip install
ultralytics` pulls the full CUDA-dependent build and fails with `OSError:
No space left on device`. Confirmed by actually running the install, not
by assuming it would fail.

Shipped instead: `detect_disguised_ad_disclosure` in
`detectors/layer2_visual.py` — a provable v1 using the exact same WCAG
contrast/size math already used for decline-button checks, applied to
ad/sponsorship disclosure labels instead. Catches a disclosure that's
technically present in the DOM but practically unreadable (tiny font,
low contrast). Does NOT catch visual mimicry of surrounding content —
that genuinely needs a trained model. AppRay's real labeled dataset
(Zenodo, CC-BY-4.0) is the recommended real training source once GPU/disk
resources are available; the code is structured so a
`detect_disguised_ad_visual_similarity` function can be added alongside
this one later without touching anything else.
