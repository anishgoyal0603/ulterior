# Ulterior — status and next steps (2026-09-06)

Supersedes the earlier version of this file and section 5 of
`PROJECT_HANDOFF.md`. Everything below was run and verified in this session.

---

## 1. Where the project stands now

| | Before this session | Now |
|---|---|---|
| CCPA-13 coverage | 7 detectors, 2 partial, 4 missing | **13 of 13**, each with a stated evidence tier |
| Python tests | 53 | **92** |
| Node tests | 3 | **12** |
| Layer 3 without an API key | DP-11 silently detected nothing | offline detection for both DP-03 and DP-11 |
| Dashboard | hardcoded `localhost:8000`, would not work deployed | served from the API's own origin |
| Deployability | container bound to a fixed port; would fail every healthcheck | binds `$PORT`; boots under production config in CI |
| Report wording | "violation(s) detected" | observation report, tiers shown, limitations printed, wording asserted by test |

**What was deliberately NOT done:** no model was trained on AppRay for DP-09.
The annotations were analysed (876 images, 97 apps, 2,185 instances) and
Disguised Ad has only 107 instances across 96 images, all Android screenshots
at 1440×2960, with the images themselves unretrievable. A thin single-class
model trained on app UI and applied to web checkouts would not survive one
informed question. AppRay's three largest categories were used instead as a
published specification, and DP-04, DP-10 and DP-05 were written from them.

How the whole thing fits together: **`ARCHITECTURE.md`**.

---

## 2. What I still need from you

Exactly one thing blocks deployment, and it is the same one as before.

**A GitHub repo name.** You said you'd push, so:

```bash
unzip ulterior-repo.zip && cd darkpattern-auditor
git remote add origin https://github.com/<your-username>/ulterior-dark-pattern-auditor.git
git branch -M main
git push -u origin main
```

Create the repo on GitHub **empty** first — no README, no .gitignore, no
licence — or the push is rejected as non-fast-forward. Then tell me
`owner/repo` and I will do the whole Railway side from here: create the
Postgres service, create the app service from the repo, set every environment
variable, generate the domain, and run the first live audit.

Nothing else is blocking. Specifically **not** needed:

- **An Anthropic API key** — you chose offline-default with an LLM opt-in, and
  offline is what runs unless an audit explicitly asks otherwise. Add a key
  later if you want the extra recall; nothing breaks without one.
- **Redis** — `app/tasks.py` uses an in-process ThreadPoolExecutor, not Celery.
- **Anything for the demo targets** — OpenCart and Juice Shop adapters are
  already written and need no credentials.

**Useful but not blocking**, for the Udyam Udaan slides rather than the code:
your final team roster (3–5 DAVV UTD students) and whether the revenue model
is a B2B SaaS subscription or a per-audit fee. The Business Model and
Financial Requirements slides can't be finished without that call, and it's
yours to make, not mine.

---

## 3. Deploy sequence (callable from a chat here once the repo exists)

1. `create-service` with image `postgres` — or add Railway's Postgres template
   in the dashboard, which is cleaner and gets you backups.
2. `create-deployment` with `repo=<owner>/<repo>`, `branch=main`, into project
   `fd02cb30-0721-437c-9ca9-c81014f40d2a`.
3. `set-variables` on the app service:

   | Variable | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}?sslmode=require` |
   | `API_KEYS` | two keys from `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
   | `ADMIN_API_KEYS` | the **second** key only |
   | `CORS_ALLOW_ORIGINS` | your Railway https domain |
   | `TRUST_PROXY_HEADERS` | `true` — Railway is a reverse proxy; without this every client shares one rate-limit bucket |
   | `RETENTION_DAYS` / `MAX_STORED_AUDITS` | `30` / `500` |

4. `generate-domain`, then check `GET /healthz`, `GET /coverage`, and open
   `/dashboard/`.
5. **Run the audit that has never been run:** `adapter_name` of
   `opencart_live_demo`, then `juiceshop_demo`. That output is your Udyam
   Udaan demo.

### The two gotchas that will stop the boot

Both are `app/config.py` refusing to start, by design:

- **`sslmode` is mandatory.** Railway's `${{Postgres.DATABASE_URL}}` has none,
  so the app exits with *"DATABASE_URL has no sslmode parameter."* Append
  `?sslmode=require`. If the internal endpoint refuses TLS, use
  `${{Postgres.DATABASE_PUBLIC_URL}}?sslmode=require` instead. Do not "fix"
  this by relaxing `config.py` — that check is a pitch asset.
- **CORS must be HTTPS and non-wildcard.** Now satisfied automatically,
  because the dashboard is served from the API origin. Set
  `CORS_ALLOW_ORIGINS` to your Railway domain anyway; production won't start
  with it empty.

---

## 4. Tests: what exists, and the ones only a live deploy can run

**Running green now:** 92 Python tests (real Chromium, real fixtures, no
mocking of the DOM) and 12 Node tests. CI (`.github/workflows/ci.yml`) runs
both on every push, in the same Playwright image the Dockerfile deploys with,
plus a check that the app *accepts* a production-shaped config — the inverse
of the existing "refuses bad config" test, and the failure you'd otherwise
only discover mid-deploy.

**Still outstanding — none of these can run from this sandbox:**

1. **A real live-site audit.** The single biggest remaining gap. Demo targets
   are unreachable here (gateway 403 on CONNECT); Railway has normal outbound
   internet. First thing to do once deployed.
2. **False-positive rate on real pages.** Every negative control so far is a
   fixture I wrote. Running the auditor across ~20 genuinely compliant real
   pages and confirming near-zero findings is worth more to a judge than any
   additional detector — and it is the number a hostile reviewer will ask for.
3. **`docker-compose.yml` end to end.** Written, never run; no Docker daemon
   here. Not on the critical path since Railway doesn't use it.
4. **Load behaviour.** The rate limiter is in-memory, so it does not share
   state across workers. Fine for one Railway instance; needs Redis before
   scaling out.
5. **Extension against a real storefront.** The client-side rules are unit
   tested, but the extension has never run on a live site.

---

## 5. Udyam Udaan (13 days out) — ranked by what moves a judge

1. **A live URL and a real audit report.** Section 3. Everything else is
   secondary to being able to run it on stage.
2. **State coverage the way the code states it:** *"All 13 categories covered,
   each with its evidence tier — provable, corroborated, or indicative. We
   publish what each detector does and does not detect rather than a single
   accuracy number."* `GET /coverage` is live proof, and the tier caps are
   enforced by a test. If a judge probes and you overclaimed, everything else
   you said gets discounted.
3. **Demo the clean-control fixture, not just the dark one.** A tool that only
   shows itself catching violations, with no proof it stays silent on a
   compliant page, is a weaker artifact than one that demonstrably does both.
4. **One pilot user** — a consumer-rights NGO, law-school clinic, or a
   professor. Still worth more than any additional detector.
5. **Don't imply MakeMyTrip is a bad actor.** Your own cited B-Index data
   ranks it among the safest (9.4) and Cleartrip as the most harmful (85.2).

---

## 6. Technical backlog, in order

1. **Redis-backed rate limiter** — only matters once you run more than one
   worker. Not before a pilot user exists.
2. **DP-09 visual mimicry** — parked, with reasoning recorded in
   `TAXONOMY_CROSSWALK.md`. Revisit only with real labelled *web* screenshots.
3. **DP-10 across sessions** — currently detects repetition within one funnel
   walk. Cross-session nagging (a popup returning tomorrow) needs persistent
   per-site state.
4. **Adapter authoring UI** — onboarding a site is a 20-line Python file. For
   a non-technical pilot user it needs to be a form.
5. **Celery migration** — `app/tasks.py` is written so this touches one file.
   Needed only when audits outlive a single process restart.
