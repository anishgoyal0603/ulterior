# Ulterior by The Odyssey

SIH26199 · automated auditing for India's 13 specified dark patterns.

Automated compliance auditor for India's **Guidelines for Prevention and
Regulation of Dark Patterns, 2023** (Central Consumer Protection Authority).
Walks a real e-commerce, travel, or other consumer-facing web funnel with a
genuine headless browser, and produces an evidence-linked report of which
of the 13 legally-defined dark patterns are present, with a screenshot and
exact quote/measurement backing every single flag.

## Run it — one command

```bash
./run_demo.sh
```

Creates a virtualenv, installs dependencies, fetches the exact Chromium build
Playwright expects, and starts the API. Then open:

| URL | What it is |
|---|---|
| `/demo/` | **Start here.** The deliberately dark-patterned storefront on the left, live audit findings with their evidence on the right. Press *Run the audit* — a real headless browser walks four pages over HTTP; nothing is pre-recorded. Switch the dropdown to the compliant control and it returns zero findings. |
| `/dashboard/` | Operator view: run any registered adapter, violation history, and the live CCPA-13 coverage table with each pattern's evidence tier. |
| `/coverage` | The coverage table as JSON, generated from `detectors/taxonomy.py` — one source of truth for the API, the dashboard and any slide quoting coverage. |
| `/storefront/cart.html` | The demo storefront itself, served over real HTTP so the crawler takes the same code path a live site would. |

No API key is needed in development. `/demo-audit` is unauthenticated on
purpose so a judge or a pilot user can click the link — and is bounded to two
server-defined adapters, with no caller-supplied URL reaching the crawler
through it at all.

## Why this exists

India was the first country in the world to legally define 13 dark
patterns (2023). In June 2025 the CCPA ordered every e-commerce platform to
self-audit within 3 months; 26 platforms — Flipkart, Swiggy, Zomato among
them — declared themselves compliant. **The advisory never specified an
audit method or format.** Published declarations vary from real multi-step
reviews to a one-line "we're compliant" letter. There is a law, a passed
deadline, and self-certifications — and no standard tool to check any of
it. This project is that tool.

## SPA support — click-driven steps, added for headless-commerce storefronts

The original architecture assumed one URL per funnel step (`page.goto()`),
which fits traditional multi-page sites (OpenCart, and this project's own
fixtures) but breaks for SPA storefronts like Saleor, where the cart is a
same-URL drawer with no dedicated route. `FunnelStep` now supports
`click_selector` + `wait_after_click_selector` as an alternative to `url` —
click an element on the current page and wait for the resulting DOM
mutation, no navigation at all. Validated against a local SPA fixture
(`fixtures/spa_ecommerce/`, `capture/adapters/spa_demo.py`) since the real
Saleor demo isn't reachable from this project's development sandbox.

**Testing this surfaced a real bug, not a hypothetical one:** in an SPA,
the DOM persists across steps — nothing resets it the way a fresh page
load does — so the same physical checkbox got flagged as basket-sneaking
**twice**, once per step, because it exists in the DOM the whole time
(just hidden via CSS before the drawer opens). Fixed with trace-wide
deduplication by checkbox identity in `detect_basket_sneaking_and_prechecked`
— confirmed this doesn't affect the original multi-page fixtures (each of
which gets a genuinely fresh DOM per step, so the bug was latent, not
active, until SPA support exposed it).

New real-external-site adapters, config-only, ready to run once deployed
somewhere with normal outbound internet (this dev sandbox is
domain-allowlisted and can't reach any of these):
- `capture/adapters/opencart_live_demo.py` — demo.opencart.com, traditional
  multi-page, zero ToS/legal concern (official public dev demo)
- `capture/adapters/juiceshop_demo.py` — demo.owasp-juice.shop, an Angular
  SPA with hash-routed URLs (own architecture note in the file on why it
  still fits the goto()-per-step model despite being an SPA)

## Taxonomy coverage — all 13, with the evidence tier stated for each

**All 13 CCPA categories now have a detector.** That sentence is only worth
anything alongside the next one: coverage is not uniform, and the project
refuses to present it as if it were.

Every detector declares an **evidence tier**, the tier caps the confidence
that detector may report, and the tier is printed next to every finding in
the report and the dashboard:

| Tier | What it means | Confidence cap |
|---|---|---|
| **Provable** | Arithmetic or DOM state. The page either did this or it didn't; no judgement, no model. | 1.0 |
| **Corroborated** | Two or more independent signals had to agree. Any one alone was too weak. | 0.9 |
| **Indicative** | Consistent with the pattern; needs human confirmation. Absence checks and language heuristics live here. | 0.6 |

`clamp_confidence()` in `detectors/taxonomy.py` enforces the caps, and
`tests/test_all_thirteen_patterns.py` walks every finding the fixture suite
produces to assert no detector ever exceeded its own tier. The live table is
served from the taxonomy itself at **`GET /coverage`**, so the API, the
dashboard and any slide quoting coverage read from one source and cannot
drift apart.

**The claim to make, precisely:** *"All 13 categories are covered, with each
detector's evidence tier stated up front — provable, corroborated, or
indicative. We publish what each one does and does not detect rather than a
single accuracy number."* That is stronger than "we detect 13 dark patterns"
and it survives a hostile question, which the shorter claim does not.

Three additional patterns with no CCPA-13 equivalent (Immortal Accounts,
Reference Pricing, Price Comparison Prevention) came from cross-referencing
Li et al.'s DPAF 68-type taxonomy (arXiv:2412.09147) and are tagged `DPAF-*`
to keep them clearly separate from the 13 legal categories in any report.
Full mapping in `data/real_world_validation/TAXONOMY_CROSSWALK.md`.

### The flow-level detectors, and why they needed new capture

DP-04, DP-05, DP-10, DP-12 and DP-13 live in `detectors/layer1_flow.py`
because they are properties of a **journey**, not of a rendered page — and
that required the capture layer to learn things it had no notion of before:

- **DP-10 Nagging** needed the crawler to actually *refuse*. `walk_funnel`
  now dismisses each blocking interruption it meets (after capturing it as
  evidence) and records the dismissal. A popup that returns at a later step,
  after a recorded refusal, is nagging by the legal definition. A popup shown
  once is not, and is deliberately not flagged. Modal signatures normalise
  digits out, so a site cannot defeat the detector by rotating the discount
  in its own popup.
- **DP-05 Subscription Trap** needed adapters to declare which steps belong
  to the signup path and which to the cancel path (`FunnelStep(role=...)`).
  There is no reliable way to infer that from page content, and guessing
  would produce exactly the unfounded finding this project refuses to emit.
- **DP-12 SaaS Billing** needed radio buttons. A pre-selected paid tier is
  almost always a radio, so a checkbox-only extractor was structurally blind
  to the most common form of the pattern.
- **DP-04 Forced Action** needed the `required` attribute and modal geometry.
  A mandatory marketing consent is provable; "this overlay had no way out" is
  an absence claim and is tiered lower, because absence is only ever as
  reliable as the recogniser looking for it.
- **DP-13 Rogue Malware** is indicative by construction and says so in every
  finding. It detects *presentation* — system-alert framing paired with an
  install CTA, or an executable behind innocuous anchor text. It performs no
  binary analysis and never claims any file is malicious. That restraint is
  deliberate: accusing a business of distributing malware is a different
  order of allegation from accusing it of a pre-ticked checkbox.

## Real-world validation — Mathur et al. (2019)


Every fixture-based test in this project uses text I wrote myself. That's
fine for proving the *architecture* works, but it can't prove the text-
matching layers generalize to real-world phrasing — a hand-built fixture
only ever contains phrasing its author already thought of.

`data/real_world_validation/` contains the actual processed dataset from
Mathur, Acar, Friedman, Lucherini, Mayer, Chetty & Narayanan, "Dark Patterns
at Scale: Findings from a Crawl of 11K Shopping Websites," CSCW 2019
(https://arxiv.org/pdf/1907.07032.pdf, code at
https://github.com/aruneshmathur/dark-patterns) — 1,818 real dark-pattern
instances scraped from real e-commerce checkout pages, each with verbatim
text and a ground-truth "Deceptive?" label. `tests/test_real_world_validation.py`
runs the actual detector regex against this real text on every test run.

**This process found and fixed real bugs, not hypothetical ones.** Initial
detection rates against real data: urgency-language matching 71.7%,
confirm-shaming matching 52.1%. Root causes: missing real-world phrasings
("in high demand", "sell out fast," "23% left in stock") and — surprisingly
impactful — smart/curly apostrophes (’) that a regex expecting a straight
apostrophe (') silently didn't match. After fixing both: **94.1% urgency
detection (721/766)**, **92.9% confirm-shaming detection (157/169)**. The
regression tests assert floors just below these validated numbers so future
edits can't silently regress them.

**What this validates and what it doesn't.** This tests whether Layer 1's
urgency-language regex and Layer 3's confirm-shaming stub recognize
real-world *text* — it does NOT test the funnel-level logic (drip pricing,
basket sneaking, fake-urgency-via-reload) or Layer 2's visual geometry,
because this dataset has no funnel/screenshot data to test those against.
That remains an honest gap: see "Honest scope statement" below.

**A genuinely interesting side-finding from this data**, worth citing in a
pitch: Mathur et al.'s own "Deceptive?" label is not "does this match a dark
pattern," it's "did we independently confirm this instance was fabricated"
(e.g., a stock counter verified to decrement on a fixed schedule rather than
real inventory). Only 17 of 632 real Low-stock Message instances (2.7%) were
confirmed fake this way — and every one of those 17 was found via exactly
the same principle this project's `detect_fake_urgency` uses: urgency
*language* alone is not proof; language *combined with* independently
verified staleness is. That's the real Mathur et al. team, at Princeton,
converging on the identical design decision documented in this project's
own README under "The false-positive trap this project specifically
avoids" — strong independent validation of that architectural choice.

## Related academic work — cite this, don't let a judge cite it first

This space has 6+ years of published research; presenting this project as
if the general problem were unsolved is not accurate and undermines
credibility with anyone who knows the literature:

- **Mathur et al. (2019)** — above. The methodological ancestor of this
  project's whole approach (crawl → detect → verify).
- **Chen et al., "Unveiling the Tricks" (UIGuard, UIST 2023)** and **AidUI
  (Mansur et al., ICSE 2023)** — rule/CV-based detection on mobile UI
  screenshots.
- **AppRay (Chen et al., TOSEM 2026)** — the current state of the art.
  Combines LLM-guided app exploration with a contrastive-learning multi-
  label classifier; achieves micro/macro F1 of 0.89/0.85 across 16 pattern
  types on a 2,185-instance dataset spanning 100 real apps. Code and data:
  https://github.com/chenjshnn/AppRay (MIT license), dataset on Zenodo
  (https://zenodo.org/records/13268006).
- **DPGuard / "50 Shades of Deceptive Patterns" (Shi et al., WWW 2025)** —
  MLLM-based detection with prompt mutation, unifies taxonomy to 21
  categories with explicit security/privacy framing, found dark patterns in
  25.7% of 2,000 mobile apps and 49.0% of websites crawled.

**What's still genuinely this project's own contribution, stated precisely
rather than oversold:** none of the above targets India's CCPA Dark
Patterns Guidelines' specific 13-pattern legal taxonomy, and none of them
frame detection as a *regulatory compliance audit* producing a report a
non-technical CCPA official could verify by eye. AppRay and DPGuard are
more sophisticated at raw detection (multi-label ML classifiers, computer
vision, MLLMs at 2,000+ app/site scale) — this project is deliberately
simpler and rule-first specifically because the goal is a legally
defensible compliance artifact, not a research benchmark. Say this
explicitly rather than implying the detection problem itself is unclaimed
territory.

## Honest scope statement — read this before a judge asks

**"Works on every e-commerce and flight website in production" is not a
real, unqualified claim anyone can honestly make**, and this project
doesn't make it. Two things are true instead, and they're a stronger
pitch than the unqualified version:

1. **A large share of real sites need zero custom code.** Any site
   emitting standard schema.org `Product`/`Offer` markup (extremely common
   — it's how products show up in Google Shopping) gets price/offer
   detection for free, out of the box, via `capture/state_extractor.py`.
   Checkbox pre-tick state, button contrast, and urgency language are read
   from the live DOM/computed styles the same way regardless of site.

2. **A new site that doesn't follow those conventions needs a small,
   focused adapter file** — a list of funnel steps (URLs + optional
   selectors), typically 15-30 lines. See `capture/adapters/demo_ecommerce.py`
   and `demo_flight.py` for the pattern; the flight adapter proves this
   generalizes to a completely different funnel shape (search → seats →
   add-ons → payment vs. listing → cart → checkout) with **zero changes**
   to the detection engine itself.

**What genuinely does NOT scale, for anyone, not just this project:** a
site running Cloudflare/PerimeterX-class bot detection (Amazon, Flipkart,
and most major OTAs in production) will challenge or block an automated
browser regardless of who builds the crawler. That's an access/legal
problem, not a code problem. The honest demo strategy is: run live against
a smaller/less-protected real site or your own fixtures to prove the
engine works end-to-end, and keep pre-recorded traces (Playwright's HAR
record/replay) as a deterministic fallback for the live demo — this is
the same offline-first principle used in the SIH26165 project.

**If deploying this on a hosting platform (e.g. Railway):** most platforms'
Acceptable Use Policies ban "bots or scrapers that violate applicable terms
of service" (Railway's exact wording, checked directly against
railway.com/legal/acceptable-use). The operative condition is *the
target's* ToS, not scraping itself — hosting the API and crawling your own
fixtures or a self-hosted benchmark environment (WebArena's Magento
instance) carries zero risk. Before adding an adapter for any real
third-party site, check that site's own ToS/robots.txt first — this is the
same diligence the anti-bot limitation above already requires, just now
also a hosting-platform compliance question, not only a technical one.

**What's tested with real tools vs. documented as the production path:**
this repo runs on SQLite + an in-process thread pool because that's what
I could actually verify end-to-end without extra infrastructure. Postgres
+ Celery + Redis (see `docker-compose.yml`) is the documented horizontal-
scaling upgrade — `app/tasks.py`'s `submit_audit_job()` interface is
written so that swap touches one file, not the whole codebase.

## Privacy / personal data

### The threat model here is unusual — read this first

This app has **no user accounts**. It collects no emails, passwords, phone
numbers, names, addresses, dates of birth, or payment details from its own
users. There is no signup, no login, no session, no cookie, no
`localStorage` PII, and no password to hash.

But it is a **crawler**, and that creates a different and easily-missed
personal-data risk: *a crawler pointed at a real checkout page — especially
one under a logged-in session — captures whatever is on that page.* That
can include the shopper's name, delivery address, email, phone, masked card
number, and order history. The personal data at risk belongs to whoever's
session the crawler runs under, not to a registered user of this app.

### Data flow map

| Stage | What is captured | Where it goes | Control |
|---|---|---|---|
| **Capture** (`capture/state_extractor.py`) | Full page HTML (`page.content()`), full visible text (`inner_text("body")`), checkbox labels, button text | Held in memory in `PageState` for the duration of one audit | Not persisted — only derived evidence is |
| **Screenshots** (`capture/funnel_walker.py`) | Full-page PNG of every funnel step | `/tmp/dp_evidence/` on disk; path stored in DB | **Highest-risk artifact.** Gitignored; deleted by the deletion endpoints |
| **Persistence** (`app/tasks.py`) | Violation explanations + evidence spans | SQLite/Postgres | **Redacted via `app/privacy.py` before write** |
| **Third-party egress** (`detectors/layer3_language.py`) | **Only decline-button text** — 1–2 short strings per page | Anthropic API | **Redacted before send.** Optional: with no API key, nothing leaves the machine |
| **API responses** (`app/main.py`) | Redacted violations only | HTTP clients | Filesystem paths no longer exposed (`has_screenshot: bool` instead) |
| **PDF reports** (`report/generator.py`) | Redacted explanations + evidence | Local file | Inherits redaction from DB |
| **Browser extension** (`extension/content.js`) | Page text on **every site the user visits** | `chrome.storage.local` | **Redacted before storage;** hostname stored instead of full URL |

### What was fixed

1. **`app/privacy.py` (new)** — redaction for emails, payment card numbers,
   Indian mobile numbers, Aadhaar, PAN, and address-keyed PIN codes.
   Deliberately does *not* touch prices, contrast ratios, or pixel
   dimensions — that numeric evidence is the actual proof of a violation
   and carries no PII.
2. **Redaction applied before DB write** (`app/tasks.py`), **before
   third-party send** (`detectors/layer3_language.py`), **before terminal
   output** (`capture/inspect_site.py`), and **before extension storage**
   (`extension/content.js`).
3. **`screenshot_path` removed from API responses** — replaced with
   `has_screenshot: bool`. The raw path leaked server directory structure
   to every caller.
4. **Extension no longer stores `location.href`** — query strings routinely
   carry session tokens, order ids, and email addresses. Only the hostname
   is retained.
5. **Data deletion added**: `DELETE /audits/{id}` and `DELETE /audits`.
   Both remove database rows *and* the screenshot files on disk.
6. **ORM cascade fixed** on `AuditJob.violations` — deletion previously
   raised `StaleDataError`.

### Checklist items that do not apply, and why

Rather than invent findings: **passwords, cookies, and localStorage PII do
not exist in this project.** There is no auth layer, so there is nothing to
hash (bcrypt/argon2/scrypt is moot), no cookie to set `httpOnly`/`secure`/
`sameSite` on, and no user record to filter out of a response. If you add
authentication later, all of those become live requirements — re-audit at
that point.

### ⚠️ Operational guidance — the control that matters most

Redaction is defence-in-depth, not a guarantee. A screenshot is a bitmap
and is **not** redacted by `app/privacy.py`; regexes cannot catch a name or
a street address rendered as pixels.

**Do not point the crawler at a logged-in session.** Audit anonymous
browsing flows. If you must audit an authenticated funnel, use a
throwaway test account with fake personal details, and run
`DELETE /audits/{id}` as soon as you have the report.

### Retention

There is currently **no automatic retention limit** — audits and
screenshots persist until explicitly deleted. For a deployment, add a
scheduled purge (a cron calling `DELETE /audits`, or a job that removes
records older than N days). This is deliberately left as a documented gap
rather than a half-implemented policy.

## Security

### Secrets handling

No secret is hardcoded anywhere in this codebase. All credentials come from
environment variables:

| Variable | Used by | Client-safe? |
|---|---|---|
| `DATABASE_URL` | `app/db.py` | **No** — server-side only, contains a password |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | `docker-compose.yml` | **No** — server-side only |
| `REDIS_URL` | production Celery path | **No** — server-side only |
| `ANTHROPIC_API_KEY` | `detectors/layer3_language.py` | **No** — server-side only, never reaches a browser |
| `CORS_ALLOW_ORIGINS` | `app/main.py` | Not a secret, but must not be `*` in production |

Setup: `cp .env.example .env`, then fill in real values. `.env` is
gitignored.

**No client-side secret exposure is possible in this project by
construction.** The dashboard (`dashboard/index.html`) is plain HTML/JS with
no build step, no bundler, and no environment-variable injection — there is
no `NEXT_PUBLIC_` / `REACT_APP_` mechanism that could leak a key into the
browser. The browser extension reads no credentials at all; it runs the
Layer 1 rules entirely client-side and calls no authenticated API. If you
later add a React/Next.js frontend, re-audit this: any variable prefixed
`NEXT_PUBLIC_` or `REACT_APP_` is shipped to the browser in plaintext.

### ⚠️ Rotate any previously-committed credentials

`docker-compose.yml` previously contained the literal placeholder password
`change_me_in_production` in three places, and `app/db.py` contained a
hardcoded SQLite path. Both are now environment-driven.

**`git rm --cached` and a new commit do NOT remove a value from git
history** — anyone with repo access can recover it from an earlier commit.
If you ever committed a *real* credential (not just the placeholder above):

1. **Rotate it immediately at the provider** — assume it is compromised.
   Rotating is the only fix that actually works; scrubbing history is
   secondary.
2. Then optionally purge history with
   [`git filter-repo`](https://github.com/newren/git-filter-repo) or the
   BFG Repo-Cleaner, and force-push.
3. If the repo was ever public, treat the credential as compromised even
   after purging — it may already be in scraper caches.

For this project specifically: `change_me_in_production` was never a real
password, so no rotation is needed — but do not reuse that string as one.

### Other hardening applied

- **Error responses are sanitized.** A failing job returns only the
  exception *type* and a job id; the full traceback goes to the server log.
  This matters because a database connection error's message text contains
  the full connection string *including the password*, and SQLAlchemy
  embeds it in traces. Verified by forcing a failure with a planted
  credential and confirming it did not appear in the API response.
- **CORS is no longer a wildcard.** Defaults to localhost; set
  `CORS_ALLOW_ORIGINS` for a real deployment. Methods narrowed to
  `GET`/`POST`, headers to `Content-Type`.
- **Captured evidence is gitignored.** Screenshots and HAR files from
  audited sites can contain session data — `.gitignore` excludes
  `*.har` and the evidence directories.

### Not yet implemented (add before a public deployment)

This is a hackathon prototype, and the audit surfaced these gaps honestly
rather than papering over them:

- **No authentication on the API.** Anyone who can reach it can queue audit
  jobs. Add an API key or OAuth layer before exposing it publicly.
- **No rate limiting.** The crawler is a resource-intensive operation and
  an unauthenticated `POST /audits` loop would be trivial to abuse.
- **SSRF risk if you accept user-supplied URLs.** Adapters are currently
  registered server-side, which is safe. If you ever let users submit
  arbitrary URLs to audit, validate them against an allowlist and block
  internal ranges (`localhost`, `169.254.169.254`, RFC1918) — otherwise the
  crawler becomes a proxy into your own infrastructure.

## Architecture

```
Adapter (URL list, or a real site via a new adapter file)
          │
          ▼
   FUNNEL WALKER (capture/funnel_walker.py)
   Real Playwright browser, walks every step,
   captures DOM + screenshot + computed styles
          │
          ▼
   GENERIC STATE EXTRACTOR (capture/state_extractor.py)
   schema.org first, common attributes as fallback
          │
   ┌──────┼──────┐
   ▼      ▼      ▼
 LAYER1 LAYER2 LAYER3
 rules  visual language
 (DOM   (WCAG  (LLM,
 proof) contrast) grounded)
   └──────┼──────┘
          ▼
   Evidence-linked violations
          │
   ┌──────┼───────┐
   ▼      ▼       ▼
 FastAPI  PDF    Dashboard
  API   report   + browser
                  extension
```

### Why the LLM is optional, and off by default

Layer 3 used to switch on whether an API key happened to be in the
environment. That made the tool's behaviour depend on ambient configuration:
the same page could produce different findings on two machines for reasons
nobody could see. For something generating compliance findings that is a bad
property, so the contract is now explicit:

- **Offline language detectors always run.** Deterministic, free, nothing
  leaves the machine, and they are the ones whose accuracy has actually been
  measured against real data (92.9% on confirm-shaming).
- **The LLM pass is opt-in per audit** (`{"use_llm": true}`) and only ever
  *adds* findings. It cannot remove or overrule an offline one.
- Every LLM-contributed finding carries `"source": "anthropic_api"` in its
  evidence, so a reader can discount them independently.

DP-11 Trick Wording previously had **no offline path at all**, which meant it
silently detected nothing whenever a key was absent — the tool appeared to
cover a category it was not covering. It now has deterministic detection for
double negatives and inverted opt-outs (a pre-ticked box whose label means
"do not"), so the category works with zero configuration.

Most of the 13 CCPA patterns are **mechanically provable**, not predicted:
drip pricing is subtraction (disclosed price vs. final total), a pre-ticked
checkbox is a DOM boolean, a fake countdown is provable by reloading with
real elapsed time and checking if the claimed-urgent value changed. Only
**Confirm Shaming** and **Trick Wording** genuinely require language
understanding. Building this as "run everything through an LLM" would be
both more expensive and less legally defensible than a rule that is
provably correct. State this explicitly in the pitch — it heads off the
"isn't this just an AI wrapper" question before it's asked.

### The false-positive trap this project specifically avoids

An early design mistake worth mentioning to judges if asked about your
process: fake urgency can't be detected by "did the value change on
reload" alone — an **honestly** static stock count (a site that just says
"42 in stock" with no urgency framing) would also fail that test and
create a false positive. The actual rule requires urgency-**framing
language** ("Only X left!", "Hurry!") **combined with** staleness across a
real time delay. See `detectors/layer1_rules.py::detect_fake_urgency` and
the docstring there. The clean fixture (`fixtures/ecommerce_clean/`) exists
specifically to prove this returns zero violations.

## Self-serve audits — the product change, not just a feature

Until now, auditing a new site required writing a Python adapter file —
fine for a hackathon demo, unusable for a paying customer. `POST /audits`
now accepts `target_urls: ["https://...", "..."]` instead of
`adapter_name`, building a funnel on the fly with no code. This is the
single change that matters most for turning this from a developer tool
into something sellable.

**This reopens the SSRF risk documented (but left unfixed) since the
first security audit**, and it's fixed now, not deferred again:
`app/ssrf_guard.py` validates every target URL before it's even queued —
scheme check, credential-in-URL check, and DNS resolution + IP-range
check against every resolved address (not just the hostname string, which
DNS rebinding could route around). Verified against 9 real attack payloads
(cloud metadata endpoint, localhost, RFC1918 ranges, IPv6 loopback,
embedded credentials, wrong scheme) — all rejected; 2 legitimate URLs —
both accepted. 5 more tests exercise this through the real API, not just
the standalone module.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

# Run the test suite (real browser, real fixtures, ~12s)
pytest tests/ -v

# Start the API
uvicorn app.main:app --reload

# Open dashboard/index.html in a browser (calls http://localhost:8000)
```

### Enable real LLM-based Layer 3 (optional)

Works offline out of the box (labelled `offline_stub` in output, safe for
unreliable venue wifi). For real contextual review:

```bash
export ANTHROPIC_API_KEY=your_key_here
```

### Running against a real live site

```bash
# 1. See what's detected for free before writing any code
python -m capture.inspect_site https://example.com/product/123

# 2. Write a small adapter (copy capture/adapters/demo_ecommerce.py),
#    swap file:// paths for real https:// URLs

# 3. Register it in app/registry.py (one line)

# 4. Audit it via POST /audits {"adapter_name": "your_new_adapter"}
```

### Load the browser extension (manual — Chrome only supports this via UI)

1. `chrome://extensions` → enable Developer Mode → "Load unpacked" → select `extension/`
2. Browse any site; the toolbar badge shows a live count of detected patterns
3. Click the extension icon for details on the current page

Note: the extension's JS is syntax-verified (`node --check`) but not
click-tested in a live Chrome session from this build environment —
verify this yourself before a live demo.

## Project structure

```
detectors/
  taxonomy.py          The 13 CCPA patterns, canonical definitions
  layer1_rules.py        Deterministic: drip pricing, basket sneaking, fake urgency
  layer2_visual.py          WCAG contrast + button geometry
  layer3_language.py          LLM + grounding verification (confirm shaming, trick wording)
  pipeline.py                   Orchestrates all three layers
capture/
  state_extractor.py    Generic schema.org + DOM extraction (works on any compliant site)
  funnel_walker.py         Playwright automation, reload-based urgency verification
  inspect_site.py             CLI: see what's free before writing an adapter
  adapters/
    base.py              SiteAdapter interface
    demo_ecommerce.py       Dark-pattern e-commerce demo
    demo_ecommerce_clean.py   Clean e-commerce demo (false-positive check)
    demo_flight.py              Flight booking demo (different funnel shape)
fixtures/               Local HTML test sites (real dark patterns embedded, verified)
app/                     FastAPI backend, async job execution, SQLite persistence
report/generator.py      PDF audit report (CCPA format, Unicode-safe for ₹)
dashboard/index.html    Live dashboard, run audits from the browser
extension/               Manifest V3 browser extension (client-side Layer 1)
tests/test_pipeline_e2e.py   9 tests, real browser, real fixtures, no mocking
docker-compose.yml       Documented Postgres + Celery + Redis production path
```

## Pitch talking points

- **The regulatory hook, stated precisely:** 13 legally-defined patterns,
  a passed compliance deadline, 26 platforms self-certified, zero standard
  audit method. This answers "who needs this" before it's asked.
- **Lead with the tier honesty**, not a single accuracy number: "all 13
  categories covered, each with its evidence tier stated — and the language
  detectors must quote the exact text they're pointing at, or the finding is
  discarded.
- **Show the clean-fixture test living in the same suite as the dark one.**
  A tool that only demos catching violations, with no proof it doesn't
  cry wolf on a compliant site, is a weaker artifact than one that
  demonstrably doesn't.
- **The flight fixture is your generalization proof.** Don't just say "this
  works on other sectors" — show the identical `detectors/pipeline.py`
  catching violations on a completely different funnel shape with zero
  changes.
- **State the anti-bot limitation before a judge raises it.** "We don't
  crawl live Flipkart in this demo because Cloudflare would block any
  automated browser, ours or anyone else's — here's our recorded trace and
  our fixture-based proof of correctness instead." This is candour, not
  a hole in the project.
