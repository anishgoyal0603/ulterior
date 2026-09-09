# How Ulterior works, end to end

Written to answer three questions in order: what happens when someone runs an
audit, what each part is responsible for, and where the boundaries are that
keep a finding defensible.

---

## 1. The one-paragraph version

Someone pastes a URL (or picks a registered site). The API validates the URL
against an SSRF guard, creates a job row, and hands it to a worker thread. The
worker drives a real Chromium browser through the funnel, capturing a
structured snapshot at each step plus a full-page screenshot. Four detector
layers read those snapshots and emit findings, each carrying its own evidence
and an evidence tier that caps how confident it is allowed to sound.
Everything is redacted for PII, written to the database, and rendered as a PDF
observation report and a dashboard. No step guesses: a finding either quotes
the page text it is pointing at, or it reports arithmetic on numbers the page
itself published.

---

## 2. The request path

```
POST /audits {"target_urls": [...]}  or  {"adapter_name": "..."}
        │
        ├─ app/auth.py ......... API key required; admin keys separated
        ├─ app/middleware.py ... rate limit (5 audits/min), correlation ID, CSP
        ├─ app/schemas.py ...... extra="forbid" — unknown fields are 422, not ignored
        ├─ app/ssrf_guard.py ... scheme, credentials-in-URL, DNS resolution,
        │                        and every resolved IP checked against private
        │                        ranges (defeats DNS rebinding)
        └─ app/tasks.py ........ job row created, then handed to a ThreadPoolExecutor
                                 (in-process on purpose: no Redis, no worker
                                 process, nothing extra to deploy or explain)
                    │
                    ▼
        capture/funnel_walker.py — real Chromium via Playwright
                    │
                    ▼
        detectors/pipeline.py — four layers, then sort by confidence
                    │
                    ▼
        app/privacy.py → database → GET /audits/{id}, dashboard, PDF
```

`GET /coverage` sits outside this path: it renders `detectors/taxonomy.py`
directly, so the API, the dashboard and any slide quoting coverage all read
from the same source and cannot drift apart.

---

## 3. Capture — what a "snapshot" actually contains

`capture/state_extractor.py` builds one `PageState` per funnel step. It is
generic by design: schema.org/JSON-LD first (an enormous share of real
storefronts emit `Product`/`Offer` markup for Google Shopping), then common
DOM conventions. A new site is a ~20-line adapter, not a rewrite.

Each `PageState` carries:

| Field | Why a detector needs it |
|---|---|
| `price`, `line_items` | DP-08 drip pricing is subtraction: first disclosed price vs. final itemised total |
| `checkboxes` (incl. radios, `required`) | DP-02, DP-04, DP-12 — selected-on-load state is a DOM boolean, not an opinion |
| `buttons` (bbox, computed colours, contrast) | DP-06 uses the exact WCAG 2.1 formula on real rendered pixels |
| `disclosure_labels` | DP-09 — is an "Ad" label actually readable? |
| `modals` (blocking, coverage, dismiss controls, **signature**) | DP-04, DP-10, DP-13 |
| `links` (text + href) | DP-05 cancellation routes, DP-13 executables |
| `form_fields` (required only) | DP-04 — what the page *demands* before it proceeds |
| `full_text`, `raw_html` | grounding: every language finding must quote text literally present here |

**The crawler refuses things.** `walk_funnel` dismisses each blocking
interruption it meets — *after* capturing it as evidence, never before — and
records the dismissal. That single behaviour is what makes DP-10 provable
rather than speculative: a popup returning after a recorded refusal is
nagging by the legal definition, while a popup shown once is not and is
deliberately not flagged. It only ever clicks controls whose own text reads
as a dismissal, because clicking anything else risks the crawler *accepting*
an offer and then reporting the consequences as the site's doing.

**Modal signatures normalise digits out.** "Get 10% off" and "Get 15% off"
resolve to the same interruption. Without that, a site could defeat DP-10 by
rotating one number in its own popup.

---

## 4. The four detector layers

| Layer | File | What it can prove |
|---|---|---|
| 1 — page rules | `detectors/layer1_rules.py` | DP-01, DP-02, DP-07, DP-08 + three DPAF extras |
| 1 — flow rules | `detectors/layer1_flow.py` | DP-04, DP-05, DP-10, DP-12, DP-13 |
| 2 — geometry | `detectors/layer2_visual.py` | DP-06, DP-09 — WCAG contrast and pixel measurement |
| 3 — language | `detectors/layer3_language.py` | DP-03, DP-11 — offline always; LLM opt-in |

The split between the two Layer 1 files is not cosmetic. Page rules answer
questions about one snapshot. Flow rules answer questions about a *journey* —
whether a refusal was honoured across page loads, whether the cancel path is
longer than the signup path, whether a recurring charge was disclosed before
or after the commitment. No amount of per-page logic can see those.

### Evidence tiers

Every detector declares one, and `clamp_confidence()` enforces the cap:

- **Provable** (cap 1.0) — arithmetic or DOM state. No judgement, no model.
- **Corroborated** (cap 0.9) — two or more independent signals had to agree.
- **Indicative** (cap 0.6) — consistent with the pattern; needs human review.

Covering all 13 categories creates real pressure to make the table look
uniform. Tiers are the answer to that pressure. DP-12 reaches 1.0 because a
control's selected state on first load is not arguable; DP-13 is capped at
0.6 and states in every finding that no binary analysis was performed.

### Two rules that keep findings honest

1. **Grounding.** Any language finding must quote a span literally present in
   `full_text`. If an LLM's claimed quote is not there, the finding is
   discarded outright — no partial credit for inventing evidence.
2. **No double-counting.** One control produces one finding. A pre-ticked
   *recurring* charge is DP-12 and is skipped by DP-02; a required checkbox is
   read from `checkboxes` and excluded from `form_fields`. Both were real bugs
   that silently inflated counts, and an inflated count is the first thing a
   hostile reviewer takes apart.

---

## 5. Layer 3: offline by default

Language detection used to switch on whether an API key happened to be in the
environment — so the same page could yield different findings on two machines
for reasons nobody could see. Now:

- Offline detectors **always** run. Deterministic, free, nothing leaves the
  box, and they are the ones measured against real data (92.9% confirm-shaming
  against Mathur et al.'s 169 real instances).
- The LLM pass runs only on `{"use_llm": true}` **and** a configured key. It
  only ever *adds*; it cannot overrule an offline finding.
- LLM-contributed findings carry `"source": "anthropic_api"` in their
  evidence, so a reader can discount them independently.

When it does run, the only thing sent is the text of decline/opt-out controls
— typically one or two short strings — passed through `redact()` first. Not
the HTML, not the page text, not screenshots, not URLs, not prices.

---

## 6. Privacy and safety boundaries

- `app/privacy.py` redacts email, phone, card and ID patterns before the
  database write, before any LLM call, before CLI output, and before extension
  storage. Numeric evidence (prices, contrast ratios, pixel sizes) survives —
  that is the actual proof and carries no PII.
- `app/retention.py` purges by age (30 days) and count (500 audits), removing
  database rows *and* screenshot files.
- Errors returned to clients are sanitised to `{detail, correlation_id}`. A
  raw database exception carries the connection string, password included.
- Everything the dashboard renders originates from an audited site — by
  definition untrusted and often actively adversarial. It uses `textContent`
  and explicit escaping, never `innerHTML` on captured content.
- The API key is held in memory in the dashboard and deliberately **not**
  written to `localStorage`: a key the browser never stores cannot be stolen
  from storage by a stored-XSS payload.

---

## 7. What comes out

**The report** (`report/generator.py`) is titled *Observation Report*, not
*Audit Verdict*, and opens by stating it is not a legal determination and
carries no regulatory authority. Every row shows its evidence tier next to its
confidence, and every report ends with its own limitations — what was not
traversed, what DP-09 and DP-13 do and do not check, why an absence check is
not proof of absence. `tests/test_report_language.py` reads the generated PDF
back and fails the build if the word "violates" ever appears, because that
wording is exactly the kind that erodes when someone tightens a sentence.

**The dashboard** (`/dashboard/`) is served from the API's own origin. That
was a deployment fix, not a preference: production config requires a
non-wildcard HTTPS CORS origin, so a separately hosted dashboard needs its own
domain, certificate and CORS entry before it can make one request. Same-origin
removes the entire class of failure, and a content-type-driven CSP keeps the
strict `default-src 'none'` policy on every JSON response while allowing the
dashboard's own scripts.

**The extension** runs the page-decidable rules client-side while shopping:
DP-01, DP-02, DP-03, DP-08, DP-11, DP-12. DP-04, DP-05 and DP-10 are
deliberately absent — they are properties of a multi-step journey, and a
content script guessing at them from one page would be reporting something it
cannot see.

---

## 8. What the test suite actually proves

92 Python tests and 12 Node tests. The ones that carry weight:

| Test file | What would break without it |
|---|---|
| `test_pipeline_e2e.py` | detectors work against a real browser, real fixtures, no mocking |
| `test_all_thirteen_patterns.py` | each new detector fires — and, via the clean-control fixture, that none of them fires on the near-miss version of its own pattern |
| `test_real_world_validation.py` | the 94.1% / 92.9% figures, re-checked against Mathur et al.'s real data on every run so they cannot silently become false |
| `test_report_language.py` | the report never asserts a legal violation, always prints its limitations, and its table is not broken mid-word |
| `test_xss_and_injection.py` | a hostile audited site cannot execute script in the dashboard |
| `test_self_serve_ssrf.py` | pasted URLs cannot reach cloud metadata, localhost, or private ranges |
| `test_production_hardening.py` | production refuses to start on unsafe config |
| `test_api_surface.py` | the static mount does not shadow the JSON API, and relaxed CSP does not leak onto API responses |

The single most valuable one is the clean-control fixture. Positive tests only
prove a detector *can* fire; a detector that fired on everything would pass all
of them.
