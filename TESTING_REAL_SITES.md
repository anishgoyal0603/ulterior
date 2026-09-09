# Testing Ulterior against real storefronts

This is the procedure for pointing the auditor at a site nobody built for it,
and for reading the result honestly.

Everything here uses `scripts/audit_url.py`, which exists for one reason: the
dashboard reports "no findings", and **"no findings" and "the crawler saw an
empty page" look identical**. This script prints what was actually read, so you
can tell those two apart.

---

## 1. Which sites are fair game

Only two kinds:

1. **Purpose-built automation sandboxes** — sites the testing community
   publishes *so that* tools crawl them.
2. **A store you own** — a Shopify development store, a LocalWP WooCommerce
   site, your own Saleor/OpenCart container.

Do not point this at a real company's checkout. Not because it would break —
because a finding is a public statement that a named business is deceiving
customers, and you have not asked their permission to publish one.

The crawler cannot place an order (`NEVER_CLICK` in
`capture/funnel_discovery.py` is checked before every single click, and it
wins over any intent match), but "it cannot spend money" is not the same as
"you are allowed to crawl it".

---

## 2. Run it

From the project folder, with the virtual environment already created
(see `RUNNING.md`):

**Windows PowerShell**

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py --preset sandboxes --json sandbox-results.json
```

**macOS / Linux**

```bash
.venv/bin/python scripts/audit_url.py --preset sandboxes --json sandbox-results.json
```

One site at a time:

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py https://demo.opencart.com/
```

Watch it happen in a real browser window (useful when a site behaves oddly):

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py https://demo.opencart.com/ --headed
```

---

## 3. Reading the verdict

The script ends every site with one of four verdicts. They are not
interchangeable.

| Verdict | What it means | What to do |
|---|---|---|
| **INCONCLUSIVE** | Almost no text rendered. A challenge page, a login wall, or JavaScript that never finished. | **Not a clean result.** Do not quote it. Send me the output. |
| **PARTIAL** | Pages rendered, but no price could be read. | DP-08 cannot run. Send me the output and I will teach the extractor that layout. |
| **CLEAN** | Pages rendered, prices were read, no CCPA-13 pattern found. | A real result. This site is fine. |
| **_n_ finding(s)** | Findings on pages the crawler genuinely read. | Read each one and check the tier. |

Above the verdict, `WHAT THE CRAWLER ACTUALLY READ` shows the rendered text
length per page. Anything marked `<-- SUSPICIOUSLY EMPTY` means that page
contributed nothing, whatever the verdict says.

`HOW IT WENT` lists every control that was clicked, and every control that was
**refused** — a line reading

```
Refused to click 'Place Order' - matches the never-click list ('place order')
```

is the safety rail working, not a failure.

---

## 4. What to expect from each sandbox

These are known, expected behaviours — not bugs to report.

- **saucedemo.com** — products are behind a login, and `Login` / `Sign in` are
  on the never-click list *by design* (this tool never types credentials
  anywhere). Discovery will correctly stop at the login page and report one
  page examined. That is the right answer, and it is also why saucedemo is a
  weak target for this particular tool.
- **automationexercise.com** — carries third-party ad frames. Expect noise in
  the blocked-request list; the storefront itself walks fine.
- **ecommerce-playground.lambdatest.com** and **demo.opencart.com** — classic
  server-rendered OpenCart. These are the best public targets: a real
  add-to-cart → cart → checkout funnel with no login.
- **demo.saleor.io** — a React storefront. This is the one that tests
  `capture/page_ready.py`; if it comes back INCONCLUSIVE, the hydration wait
  is too short and that is worth telling me.

---

## 5. The strongest target: a store you own

A **Shopify development store** is free, takes about ten minutes, and gives you
a real production checkout architecture that you are entitled to audit. It is
a better demonstration than any sandbox.

1. Create a free Shopify Partner account and add a development store.
2. Add one product with a price.
3. Add something worth catching — a pre-ticked add-on, a shipping fee not shown
   on the product page.
4. Remove the storefront password (Online Store → Preferences) so the crawler
   can reach it, or the audit will stop at the password page.
5. Run:

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py https://your-store.myshopify.com/
```

### A store on your own machine (LocalWP, Docker, a dev server)

Private addresses are refused by default — a server that fetches internal
addresses on request is an SSRF hole. For a local store, turn the flag on for
that one shell:

```powershell
$env:ALLOW_LOCAL_TARGETS = '1'
.\.venv\Scripts\python.exe scripts\audit_url.py http://localhost:10004/
```

```bash
export ALLOW_LOCAL_TARGETS=1
.venv/bin/python scripts/audit_url.py http://localhost:10004/
```

Never set it on a deployed instance.

---

## 6. What to send back

The whole stdout of the run, plus `sandbox-results.json`. The two things that
actually improve the tool are:

- a page where the verdict was **PARTIAL** — a money layout the extractor
  cannot read yet, and
- a finding you believe is **wrong** — a shop reported for something lawful.

The second matters more. A missed detection costs a finding; a false positive
costs the argument, because it is this tool publicly calling an honest seller
deceptive. `tests/test_compliant_corpus.py` exists to hold that line, and every
real-world false positive you find should end up as a new shop in it.
