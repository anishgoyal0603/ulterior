# Ulterior — Dark Pattern Auditor — Project Handoff Document

**Purpose of this document:** paste this entire file into a new chat to resume work
with full context. It covers what exists, what's verified vs. assumed, what's
blocked and why, and exactly what to do next. Written to stand alone — no need
to re-read the original conversation.

---

## 1. What this project is

**Ulterior** is an automated compliance-auditing platform that scans e-commerce
and travel/flight/hotel booking websites for violations of **India's Guidelines
for Prevention and Regulation of Dark Patterns, 2023** (13 legally-defined
patterns, enforced by the CCPA — Central Consumer Protection Authority).

**The regulatory hook (why this matters, not just what it does):** in June
2025, the CCPA ordered every e-commerce platform to self-audit within 3 months.
26 major platforms (Flipkart, Swiggy, Zomato, etc.) declared themselves
compliant — but the CCPA never specified an audit method or format. There is a
law, a passed deadline, self-certifications, and **no standard tool to verify
any of it.** This project is that tool.

**Origin:** built as a candidate idea for SIH26199 (AICTE "Student Innovation —
Technology ideas in tertiary sectors" open slot) and separately being pitched
at **Udyam Udaan 2026** (DAVV School of Commerce Entrepreneurship Cell startup
pitch summit, 19 Sept 2026). A separate, unrelated project (an NLP engine for
Oil India safety-report triage, SIH26165) was built earlier in the same
conversation thread — not part of this zip, mentioned here only so a new chat
doesn't confuse the two.

**Team name: Ulterior** — chosen because every dark pattern is, definitionally,
a button or countdown hiding an ulterior motive behind an innocent surface.

---

## 2. What's ACTUALLY built and verified (not aspirational)

Every claim below was tested with real tools in this project's development
sandbox — not assumed. Where something is unverified, it's flagged as such
explicitly in section 4.

### 2.1 Detection engine — three tiers

| Layer | What it does | File(s) |
|---|---|---|
| **Layer 1** (deterministic rules) | Drip pricing, basket sneaking/pre-ticked checkboxes, fake urgency (language + reload-verified staleness), bait-and-switch (price promised vs. delivered), reference pricing, immortal accounts, price comparison prevention | `detectors/layer1_rules.py` |
| **Layer 2** (visual/geometry) | Interface interference (WCAG contrast + button size), disguised-ad disclosure-label readability (v1, no CV) | `detectors/layer2_visual.py` |
| **Layer 3** (language/LLM) | Confirm-shaming + trick-wording via Anthropic API, **mandatory grounding** (any LLM claim that isn't a verbatim quote from the source page is rejected outright), offline heuristic stub when no API key is set | `detectors/layer3_language.py` |

**Design philosophy, stated repeatedly and enforced in code:** "a rule that's
provably correct beats a model that's 85% confident." Most of the 13 CCPA
patterns are provable by arithmetic or DOM inspection, not prediction. Only 2
of 13 genuinely need language understanding.

### 2.2 CCPA-13 taxonomy coverage — the honest number

**7 of 13 fully implemented** (up from 5 partway through the build), 1 partial,
5 with zero detection code. Full breakdown, with reasoning for every gap, in
`data/real_world_validation/TAXONOMY_CROSSWALK.md`:

| Code | Pattern | Status |
|---|---|---|
| DP-01 | False Urgency | ✅ Implemented — 94.1% on real data |
| DP-02 | Basket Sneaking | ✅ Implemented |
| DP-03 | Confirm Shaming | ✅ Implemented — 92.9% on real data |
| DP-06 | Interface Interference | ✅ Implemented |
| DP-07 | Bait and Switch | ✅ Implemented (built from a real CCI inquiry into MakeMyTrip/Goibibo) |
| DP-08 | Drip Pricing | ✅ Implemented |
| DP-09 | Disguised Advertisement | ⚠️ v1 only (disclosure-label readability; no visual-mimicry CV yet) |
| DP-11 | Trick Wording | ⚠️ LLM-only, no offline fallback |
| DP-04 | Forced Action | ❌ Not implemented |
| DP-05 | Subscription Trap | ❌ Not implemented |
| DP-10 | Nagging | ❌ Not implemented |
| DP-12 | SaaS Billing | ❌ Not implemented |
| DP-13 | Rogue Malware | ❌ Not implemented (lowest priority — not really a checkout-flow pattern) |

**Plus 3 "beyond CCPA-13" patterns**, added by cross-referencing the DPAF
68-type academic taxonomy (arXiv:2412.09147) against CCPA's 13: Immortal
Accounts, Reference Pricing, Price Comparison Prevention. Tagged `DPAF-*`,
not `DP-*`, so they're never confused with the legally-defined 13 in a report.

### 2.3 Capture layer

- Real Playwright/Chromium browser automation (`capture/funnel_walker.py`,
  `capture/state_extractor.py`)
- Generic extraction via schema.org/JSON-LD + common DOM conventions — works
  on any compliant site with **zero custom code**
- Adapter pattern: onboarding a new traditional multi-page site is a ~20-line
  config file (`capture/adapters/`)
- **SPA support**: `FunnelStep` supports `click_selector` (click-and-wait for
  DOM mutation) as an alternative to `url` (navigate), for same-URL-drawer
  storefronts like Saleor. Validated against a local fixture
  (`fixtures/spa_ecommerce/`) since the real Saleor demo isn't reachable from
  the dev sandbox.
- **Self-serve mode** (the actual product-viability feature): `POST /audits`
  accepts `target_urls: [...]` instead of a pre-registered `adapter_name` —
  anyone can paste a URL, no code required. This is the single change that
  matters most for "sellable, not just a demo."
- **SSRF protection** (`app/ssrf_guard.py`): every self-serve URL is validated
  — scheme check, credential-in-URL check, DNS resolution + IP-range check
  against every resolved address (defeats DNS rebinding). Verified against 9
  real attack payloads (cloud metadata endpoint, localhost, RFC1918 ranges,
  IPv6 loopback) — all correctly rejected.
- CLI inspector (`capture/inspect_site.py`) — point at any real URL, see what
  the generic extractor detects for free before writing an adapter.

### 2.4 Backend

- FastAPI (`app/main.py`), SQLite dev / Postgres prod path
- **Auth**: API key required, read/admin key separation enforced server-side
  (a read key gets 403 on destructive routes, not 200)
- **Fail-fast config** (`app/config.py`): production refuses to start without
  API keys, without TLS on the DB connection, or with wildcard/HTTP-only CORS
- **Rate limiting**: 5 audits/min, 3 deletes/hour, 60/min default (in-memory —
  documented limitation, doesn't share state across multiple workers yet)
- **Security headers**: CSP, X-Frame-Options, HSTS (prod only), correlation
  IDs on every response
- **Sanitized errors**: verified with a planted fake password inside a forced
  exception — client got only `{"detail": "...", "correlation_id": "..."}`,
  nothing leaked
- **PII redaction** (`app/privacy.py`): applied before DB write, before LLM
  send, before CLI output, before extension storage. Verified with a real
  planted email/phone through a real browser capture.
- **Data retention**: age-based (30 days) + count-based (500 max) purge,
  removes DB rows AND screenshot files from disk
- **XSS hardening**: a real exploitable bug was found (dashboard/extension
  rendering audited-page content via `innerHTML`) and fixed — dashboard now
  escapes everything, extension rewritten to use `textContent`/`createElement`
  exclusively

### 2.5 Frontend

- Dashboard (`dashboard/index.html`) — vanilla HTML/JS/Chart.js, XSS-hardened
- Browser extension (Manifest V3, `extension/`) — client-side Layer 1 rules,
  runs live while shopping. Recently upgraded:
  - LRU cache (500-entry cap) to avoid re-scanning unchanged content on every
    DOM mutation
  - Urgency/confirm-shaming regex synced with the real-data-validated backend
    lists (previously stale at ~72%/52% detection; now 94.1%/92.9%)
  - Curly-quote (') normalization — a real, high-impact bug fix

### 2.6 Testing — 53 Python tests + 7 Node tests, all passing

- `tests/test_pipeline_e2e.py` — real browser, real fixtures, no mocking
- `tests/test_real_world_validation.py` — tested against **Mathur et al. 2019's
  actual CSCW dataset** (1,818 real scraped dark-pattern instances from 11K
  shopping sites) — found and fixed real regex gaps this way
- `tests/test_production_hardening.py` — config fail-fast, headers, rate limits
- `tests/test_xss_and_injection.py` — proved a real XSS exploit, then fixed it
- `tests/test_self_serve_ssrf.py` — SSRF guard through the real API
- `tests/test_extension_cache.js` — LRU eviction, real pattern validation

### 2.7 Real-world data grounding (not live-scraped — see section 4 for why)

- `data/real_world_validation/mathur2019_dark_patterns.csv` — the real Mathur
  et al. dataset, used for validation (94.1% urgency, 92.9% confirm-shaming
  detection on real text)
- `data/real_world_validation/india_regulatory_cases.py` — 4 real, publicly
  reported regulatory findings (CCPA penalties disclosed to the Rajya Sabha, a
  CCI inquiry into MakeMyTrip/Goibibo, CCPA notices, an industry B-Index
  report). **Important finding documented here**: MakeMyTrip ranks among the
  *safest* travel platforms per this report (B-Index 9.4) while Cleartrip is
  the most harmful (85.2) — don't let a pitch imply otherwise.
- `data/real_world_validation/TAXONOMY_CROSSWALK.md` — full CCPA-13 vs.
  DPAF-68 mapping, gap analysis, prioritized build order

### 2.8 Infrastructure connected

- **Railway**: connected via MCP. Project **`ulterior-darkpattern-auditor`**
  created (ID `fd02cb30-0721-437c-9ca9-c81014f40d2a`), workspace
  `54e67f43-7fbc-4f1f-b4d7-80eb2372c459`. **Not yet deployed** — blocked on
  needing a GitHub repo (see section 4).
- **Hugging Face, Supabase, Neon**: connected, not yet used in the codebase.
  Hugging Face is earmarked for pulling YOLO11/YOLO26 weights and searching
  for existing fine-tuned models once GPU resources are available.
- **Datadog**: suggested, not connected.

---

## 3. Prior art this project builds on — cite these, don't let a judge cite them first

- **Mathur et al., "Dark Patterns at Scale" (CSCW 2019)** — the actual dataset
  used for validation. GPL-3.0 code (not copied — reimplemented independently).
- **AppRay (Chen et al., TOSEM 2026)** — SOTA mobile dark-pattern detection,
  MIT-licensed code, dataset on Zenodo (CC-BY-4.0). Not yet integrated (see
  section 4).
- **DPGuard / "50 Shades of Deceptive Patterns" (Shi et al., WWW 2025)** —
  MLLM-based detection, 21-category taxonomy with security framing.
- **DPAF (Li et al., arXiv:2412.09147)** — 68-type granular taxonomy, used for
  the crosswalk/gap analysis in section 2.2.

**This project's actual differentiation, stated precisely**: none of the above
targets India's CCPA-13 legal taxonomy specifically, or frames detection as a
regulatory compliance audit producing a report a non-technical official could
verify by eye. The prior art is more sophisticated at raw detection (ML/CV at
scale); this project is deliberately simpler and rule-first because the goal
is a legally defensible artifact, not a research benchmark.

---

## 4. What's blocked, and exactly why — verified constraints, not excuses

Every item here was actually tested, not assumed:

1. **No live crawl of any real commercial site has been performed.**
   MakeMyTrip/Goibibo/Vistara/demo.opencart.com/demo.saleor.io/
   demo.owasp-juice.shop are all unreachable from the project's dev sandbox
   (`x-deny-reason: host_not_allowed` — confirmed via direct curl test). This
   is a sandbox network allowlist issue, not a policy choice — **the Railway
   deployment, once live, will have normal outbound internet and can actually
   do this.**
2. **Computer vision (YOLO) for Disguised-Ad detection was attempted and
   blocked**: `pip install ultralytics` failed with `OSError: No space left on
   device` (2.9GB free disk in the sandbox), and PyTorch's CPU-only wheel index
   isn't reachable (would pull the full CUDA build otherwise). A provable v1
   (disclosure-label contrast/size) was shipped instead — see section 2.2.
3. **Railway deployment is created but not live** — `create-deployment`
   requires a GitHub repo already connected, and there's no GitHub write
   connector available. **This is the single most important next action** —
   see section 5.
4. **AppRay's real dataset (Zenodo, 504MB+510MB zips) hasn't been analyzed** —
   `zenodo.org` isn't reachable from the sandbox, and the files are too large
   to pull into a chat context regardless. Need just the JSON annotation file
   (`all_images_patterns.json`), not the full zip.
5. **`docker-compose.yml` (Postgres/Redis/Celery production path) is written
   but never actually run** — no Docker in this sandbox.

---

## 5. Prioritized next steps

### Immediate (blocks everything else)
1. **Push the code to GitHub.** From the extracted zip:
   ```bash
   cd darkpattern-auditor
   git init && git add . && git commit -m "initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```
   Then, in the new chat, provide the repo name (`owner/repo`) so
   `Railway:create-deployment` can be called against the existing project
   (ID above) — this actually deploys it and makes the app live.
2. **Set Railway environment variables** once deployed — see `.env.example`
   for the full list (`API_KEYS`, `DATABASE_URL`, `CORS_ALLOW_ORIGINS`, etc.).
   Production **refuses to start** without these — that's intentional.
3. **Send just `all_images_patterns.json`** extracted from AppRay's
   `AppRay-Dark.zip` (Zenodo link: https://zenodo.org/records/13268006) —
   needed to check real per-category instance counts before any GPU spend.

### Product / business (for Udyam Udaan and beyond)
4. Confirm Udyam Udaan team (3–5 DAVV UTD students) and revenue model (B2B
   SaaS subscription vs. per-audit fee) — needed to finish the pitch deck's
   Business Model / Revenue Model / Financial Requirements slides.
5. **Get one real pilot user** — a consumer-rights NGO, law school clinic, or
   professor. This matters more than any additional detector.
6. **Legal review of finding language before anything goes public** — the
   tool alleges specific real companies violate a specific law; wording like
   "this pattern resembles a documented pattern type" is defensible, "Company
   X violates CCPA" from an unaffiliated tool is not. Real defamation exposure
   if a finding is wrong and published.

### Technical, in priority order
7. Run a real audit against OpenCart/Juice Shop demos once Railway is live
   (adapters already written: `capture/adapters/opencart_live_demo.py`,
   `capture/adapters/juiceshop_demo.py`) — closes the "never tested against a
   real site" gap.
8. Close remaining CCPA gaps, cheapest first: Subscription Trap (click-depth
   counter), Trick Wording offline stub, Forced Action. Full reasoning in
   `TAXONOMY_CROSSWALK.md`.
9. Once AppRay JSON is reviewed and GPU resources exist (Colab/Kaggle, or
   GitHub Student Pack credits via DAVV email): real YOLO11/YOLO26 fine-tuning
   for Disguised-Ad visual-mimicry detection, layered on top of the existing
   v1 disclosure check, not replacing it.
10. Rate limiter is in-memory only — needs Redis backing before multi-worker
    production use.

---

## 6. Key facts to not re-litigate in a new chat

- **YOLOv12x is real but not Ultralytics' current recommendation** — they
  now recommend YOLO11 or YOLO26 for production; YOLO12 is "maintained
  primarily for benchmarking."
- **Parliament doesn't procure software** — the realistic path to
  institutional credibility is getting findings *cited* (like the real
  CCPA-to-Rajya-Sabha disclosure already found), not selling directly to a
  legislature.
- **MakeMyTrip is reported as one of the safer travel platforms**, not a bad
  actor, per the one industry report cited in this project's own data.
- Railway's Acceptable Use Policy bans "bots/scrapers that violate applicable
  terms of service" — the operative condition is the *target's* ToS, not
  scraping itself. Own fixtures and purpose-built benchmark sites (WebArena,
  Juice Shop) are zero-risk; real commercial sites need a ToS check first.

---

## 7. How to resume

Paste this document into a new chat along with the project zip, and say what
you want to work on next — likely either "help me push to GitHub and deploy"
or "here's the AppRay JSON, check the Disguised Ad instance count." Both are
ready to act on immediately with no further context needed.
