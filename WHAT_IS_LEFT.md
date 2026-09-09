# Ulterior — what's left, and what I need from you

Written 8 Sep 2026, after the first CI run on GitHub.
Section 1 is **done** — the CI fix and the dashboard URL box are both in the
code you have now, so skip it unless you want the history. Sections 3 and 4
are still current. See `TESTING_REAL_SITES.md` for how to point the auditor
at a real storefront.

---

## 1. The CI failure — what it was, and how to apply the fix

Your **Python suite passed** (98 tests, 46s). Only the Node step failed, and it
was my mistake, not yours:

```
node: not found
Error: Process completed with exit code 127.
```

I had both suites in one job running inside
`mcr.microsoft.com/playwright/python` — the Playwright **Python** image. It
ships Python and Chromium but **no Node runtime**, so `node tests/…` could
never work there. Worse, because that step failed, the production-config check
after it was skipped rather than run, so a green half looked like a red whole.

Fixed by splitting into two jobs, each in the environment it actually needs:

| Job | Where it runs | What it does |
|---|---|---|
| `python-tests` | Playwright Python container | 102 tests against a real browser, then the production-config boot check |
| `extension-tests` | plain `ubuntu-latest` + `setup-node@20` | syntax-checks the three extension sources, validates `manifest.json`, runs the 12 Node tests |

They now run in **parallel**, and a failure in one no longer hides the other.

### Applying it

Two commits are waiting for you as patch files. From your repo folder:

```bash
git am patches/0001-Fix-CI-run-the-Node-suite-outside-the-Playwright-Pyt.patch
git am patches/0002-Expose-self-serve-URL-auditing-in-the-dashboard.patch
git push
```

(Copy the `patches/` folder into the repo first. I verified both apply cleanly
on top of `c5baca6`, which is what you pushed.)

If `git am` complains for any reason, the fallback is manual — copy `ci.yml`
over `.github/workflows/ci.yml`, then:

```bash
git add -A && git commit -m "Fix CI: run the Node suite outside the Playwright Python container" && git push
```

Watch the **Actions** tab. Both jobs should go green in about a minute.

---

## 2. A gap I closed while I was in there

Your pitch slide says **"Paste a link."** The API has accepted arbitrary URLs
since the beginning — but **no screen exposed it**. So the claim was true of
the product and false of every page a person could actually reach. That is
exactly the gap that surfaces when a judge says "show me — audit *this* site."

The dashboard now has a URL box and an **Audit these URLs** button.

It is deliberately **not** on the public `/demo/` page. An unauthenticated box
that fetches any URL you type is a crawler-for-hire, which is the whole reason
the demo endpoint is restricted to two server-defined storefronts. On the
dashboard it sits behind the API key, and every URL still goes through the
SSRF guard before a browser opens it — a rejected one now shows you *why*
instead of spinning.

---

## 3. What I actually need from you

### Blocking — one thing

**The repo name, as `owner/repo`.** Railway is already connected to your
account (I can see your `ulterior-darkpattern-auditor` project). With that one
string I can do the entire deploy from here: create Postgres, create the app
service from your repo, set every environment variable, generate the domain,
and run the first live audit.

**One click you have to do yourself first:** Railway needs permission to read a
**private** repo. In the Railway dashboard → *Account Settings → GitHub* →
connect GitHub and grant access to that repository. Railway cannot deploy a
private repo it cannot see, and that authorisation is yours to give, not mine.

### Not needed — worth saying explicitly

- **No datasets.** Mathur et al. (1,818 real instances) is already in the repo
  and drives the 94.1% / 92.9% figures on every CI run. AppRay's annotations
  are already analysed, and its images are deliberately not used.
- **No API key.** Layer 3 runs offline by default; the LLM pass is opt-in.
- **No Redis, no GPU, no model weights, no training data.** Nothing in the
  system needs any of them.

### Would materially improve it, in order of value

1. **~20 real Indian shopping/travel URLs you'd call compliant.** This is the
   highest-value thing you can give me, and you know Indian e-commerce far
   better than I do. Running the auditor across pages that *should* come back
   clean, and publishing the false-positive rate, is the number a reviewer
   will ask for and the one thing no amount of extra detectors substitutes
   for. Product pages, cart pages, checkout pages — any mix.
2. **One real storefront whose owner consents.** A friend's Shopify or
   WooCommerce shop, a college club's store, a classmate's project. The first
   genuinely real audit with zero terms-of-service risk, and a far better demo
   than a fixture.
3. **A pilot contact** — a consumer-rights NGO, a law-school clinic, or a
   professor who'd cite a finding. Still worth more than any additional
   detector.
4. **Team roster and revenue model** (B2B subscription vs. per-audit fee) —
   needed for the pitch slides, not for the code.

---

## 4. What's left to build, honestly

Ordered by what a judge would notice first.

| # | Item | Effort | Blocked on |
|---|---|---|---|
| 1 | **Deploy** — Postgres, service, vars, domain | ~30 min, mostly mine | your repo name + Railway/GitHub link |
| 2 | **First real-site audit** (OpenCart, OWASP Juice Shop) | minutes once live | #1 |
| 3 | **False-positive sweep** across ~20 compliant real pages, rate published | an afternoon | #1 + your URL list |
| 4 | **Extension screenshot** for the deck | 1 minute | your laptop (Chromium here can't load MV3) |
| 5 | **Cross-session nagging (DP-10)** — currently detects repetition within one walk; a popup that returns *tomorrow* needs persistent per-site state | ~half a day | nothing |
| 6 | **Adapter authoring UI** — onboarding a site is a 20-line Python file; a pilot user needs a form | ~1 day | a pilot user existing (#3 above) |
| 7 | **Redis-backed rate limiter** — the current one is per-process | ~2 hours | only matters at >1 worker |
| 8 | **DP-09 visual mimicry (CV)** | parked | real labelled *web* screenshots, which do not exist yet |

Items 5–8 are genuinely optional for the submission. **1, 2 and 3 are the
ones that change what you can claim on stage**, and 3 is the only one that
needs something from you.

---

## 5. Current state, for the record

- **276 Python tests (248 headless + 28 browser) + 12 Node tests**, all passing
  — including a compliant-shop corpus that exists to catch the tool accusing
  an honest seller, and a real-world extraction suite that reads prices from
  rendered text with no machine-readable markup
- **13 of 13** CCPA categories detected, each with a stated evidence tier
- A public demo page anyone can run, and a dashboard that now audits any URL
- A PDF observation report whose wording is asserted by a test
- CI green on both jobs once the patches above are pushed

The honest summary has not changed: **this is working software that has never
been pointed at a real commercial site.** Everything in section 4 exists to
close that one sentence, and step 1 is the only thing standing in the way.
