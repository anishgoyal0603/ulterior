# Running and contributing to Ulterior

Ulterior audits e-commerce checkout flows against India's **Guidelines for
Prevention and Regulation of Dark Patterns, 2023** — all thirteen categories.
It drives a real headless Chromium through a shop's funnel and reports what it
can prove, what it can corroborate, and what only a human should confirm.

This is the guide for anyone with access to the repository. It takes about
**25 minutes** on a clean Windows machine, most of it downloads running
unattended. You need no API key, no database and no cloud account — everything
runs on your own machine and talks to nothing but itself.

Follow the steps in order; each depends on the one before it.

---

## 0. What your access lets you do

If Anish added you as a **collaborator**, you have GitHub's *Write* role. In
practice:

**You can**

- clone the repository and pull changes
- create branches and push to them
- open, review, approve and merge pull requests
- edit issues, labels, milestones and releases
- run and edit GitHub Actions workflows

**You cannot** (these need the owner)

- change repository visibility, or delete the repository
- add or remove other collaborators
- change branch protection rules or repository settings
- rename the default branch

If you only have the link and were never added, you can still do everything in
sections 1–6 and 8 — cloning and running needs no permission at all while the
repository is public. You just cannot push.

---

## 1. Install Python 3.12.10 — the exact version matters

This is the step most likely to cost you an hour, so read the reasoning once.

**Why not the newest Python?** The project needs Python 3.10–3.13. Python 3.14
will not work: several dependencies have no prebuilt wheels for it, so `pip`
falls back to compiling C source and fails with Microsoft C++ Build Tools
errors that have nothing to do with this project.

**Why 3.12.10 and not a higher 3.12?** The downloads page lists 3.12.11 through
3.12.14, which look newer. They are not usable here. Python 3.12 has passed out
of full maintenance into security-fix-only status, and releases in that phase
ship as **source code only** — there is no `.exe`. **3.12.10 (8 April 2025) is
the last 3.12 with a Windows installer.**

Download the 64-bit installer (~26 MB):

    https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe

> **Do not miss this checkbox.** On the installer's *first* screen, tick
> **"Add python.exe to PATH"** at the bottom, then click **Install Now**. If
> you miss it, every later step fails with `python is not recognized`. To fix
> it, run the installer again and choose *Modify*.

Close every open terminal and open a fresh one — PATH changes only apply to
terminals started afterwards. Then verify:

```powershell
py -3.12 --version
```

Expect `Python 3.12.10`. Other Python versions installed alongside are fine:
the launcher asks for 3.12 by name, so having 3.14 present will not confuse it.

## 2. Install Git

Download from <https://git-scm.com/download/win> and accept every default.
Verify in a fresh terminal:

```powershell
git --version
```

Any `2.x` version is fine.

## 3. Clone the repository

```powershell
cd $HOME\Documents
git clone https://github.com/anishgoyal0603/ulterior.git
cd ulterior
```

Every remaining command assumes you are inside that folder.

> **Clone into Documents, not Downloads.** Anything is fine technically, but
> keeping the project somewhere you never unzip things into avoids ending up
> with two copies of it. Two copies is how someone edits one and pushes the
> other. If a command ever behaves strangely, run `pwd` first and check which
> folder you are standing in.

The first `git push` will ask you to sign in to GitHub. A browser window
opens; approve it and the push continues.

## 4. Run the setup script

One command creates an isolated Python environment, installs the dependencies,
downloads the exact Chromium build the auditor drives, and starts the server:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
```

> **Why `-ExecutionPolicy Bypass`?** Windows blocks downloaded PowerShell
> scripts by default. This lifts it for one command only and changes no setting
> on your machine. Running `.\run_demo.ps1` alone is refused with *"running
> scripts is disabled on this system"*.

> **The long silent pause is normal.** The Chromium download is ~150 MB with no
> progress bar. On a slow connection it can look frozen for several minutes.
> Leave it alone — do not press Ctrl+C.

When it is ready:

```
==> Starting the API on http://127.0.0.1:8000

    Demo (start here) : http://127.0.0.1:8000/demo/
    Dashboard         : http://127.0.0.1:8000/dashboard/
    Coverage (JSON)   : http://127.0.0.1:8000/coverage
    Dark storefront   : http://127.0.0.1:8000/storefront/cart.html

    No API key is needed in development.
    This window will look frozen. That is the server running.
    Press Ctrl-C to stop it.
```

The terminal is *supposed* to look frozen now. That is the server running.

## 5. Open it in a real browser

Leave PowerShell running. Open **Chrome or Edge** at
<http://127.0.0.1:8000/demo/>.

> **Use a real browser window.** Do not use VS Code's built-in preview pane
> (Simple Browser / Live Preview) or any in-app webview. Those impose a
> stricter security policy that blocks the page's stylesheet, and the app
> renders as unstyled Times New Roman with a broken box where the storefront
> should be. It looks like the software is broken. It is not.

## 6. Try the two things worth seeing

### The public demo — `/demo/`

Pick a storefront and press **Run the audit** (~10 seconds). A real headless
Chromium walks four checkout pages, loading the urgency pages twice with real
time in between to test whether the countdown actually moves.

Each finding carries its **evidence tier**: *Provable* means arithmetic or DOM
state — the page either did this or it did not. *Corroborated* means two or
more independent signals agreed. *Indicative* means it matches the pattern and
a human should confirm. No detector may report more confidence than its tier
allows.

Run the clean storefront too. A tool that finds violations everywhere is not
measuring anything.

### The dashboard — `/dashboard/`

Leave the **API key box blank** — authentication is off locally and only
required in a deployed instance.

1. Paste `http://127.0.0.1:8000/storefront/listing.html` into the URL box
2. Leave **"Find the checkout by itself"** ticked
3. Press **Audit this site**

From that one link it finds its own way — product page, cart, checkout — and
reports six violations. The discovery report above the table includes:

```
Refused to click 'Pay Now' - matches the never-click list ('pay'), which
exists so this tool can never place an order, start a subscription, or
sign in as somebody.
```

That refusal is the design point, not a limitation. The tool walks real shops,
and a real shop's checkout has a button that spends money. It stops one button
short and says so.

## 7. Run the tests

Stop the server first (**Ctrl+C**), then:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q -m "not ui"
```

Expect:

```
271 passed, 28 deselected, 1 warning
```

### Reading that line

**`28 deselected` is not a problem, and there is nothing to remove.** It is
that command doing exactly what you asked. `-m "not ui"` means *skip the tests
marked `ui`* — the 28 browser tests that launch a real server and drive a real
Chromium window. pytest is reporting how many it set aside on your
instruction. Drop the flag and all 299 run.

**`1 warning` is also fine.** It comes from inside Starlette's own code, not
this project. Ours are at zero and a test keeps them there.

**The word `skipped`, if you ever see it, IS a problem.** A skipped test is
not a passing test. It usually means the test-only dependency is missing —
`run_demo.ps1` installs it, but if you built the virtual environment by hand:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

That one package (`pdfminer.six`) lets six tests read the generated PDF back
and check what it actually says. Those six guard the wording of a report that
names a company, and they were silently skipping on every laptop for days
because nobody noticed the word in a wall of output.

### The full set

About six minutes. Runs everything, including the browser tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q
node tests\test_extension_cache.js
```

Expect **299 passed** and **ALL TESTS PASSED** (12 checks on the browser
extension; `node` is only needed for this one file — skip it if you have not
installed Node).

A different number is worth reporting rather than working around. Many of
these tests exist because a detector was accusing honest shops, and a green
suite is the only thing standing between a change and that happening again.

**Run the tests before you push.** CI runs them on every pull request anyway,
but finding out locally takes one minute instead of five.

## 7b. Auditing a real website

The demo and the dashboard audit bundled storefronts. To point the tool at a
real site, use the command-line harness:

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py --preset sandboxes
```

That walks five public automation sandboxes — sites the testing community
publishes *so that* tools crawl them. One site at a time:

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py https://demo.opencart.com/
```

Why this exists rather than just using the dashboard: the dashboard reports
"no findings", and **"no findings" and "the crawler saw an empty page" look
identical**. This prints what was actually read on each page — text length,
the price and where it came from, every control clicked and every control
refused — and ends with a verdict that separates them:

| Verdict | Meaning |
|---|---|
| INCONCLUSIVE | Almost nothing rendered. **Not a clean result.** |
| PARTIAL | Pages rendered, no price found. Price detectors cannot run. |
| CLEAN | Rendered, priced, nothing found. A real result. |
| _n_ finding(s) | Findings on pages the crawler genuinely read. |

**Only audit sites you are allowed to audit**: those sandboxes, an
open-source demo, or a store you own. Not because it would break — the
crawler never clicks a control that could spend money — but because a finding
is a public statement that a named business is deceiving customers.

### Auditing a big commercial site (Amazon, MakeMyTrip, Flipkart)

Do not point the crawler at them. It will not get past their bot protection,
it will never reach the checkout behind their login, and automated access is
against their terms. Instead, browse the funnel yourself as an ordinary
customer, save each page (`Ctrl+S` -> "Webpage, Complete"), and run:

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py --saved 1-product.html 2-cart.html 3-checkout.html
```

That measures what YOU were shown. It is a customer documenting their own
transaction rather than a robot hitting a server, it gets past the login
because you logged in, and the evidence is a screenshot any human can check.

**Read `VERIFYING_A_FINDING.md` before repeating any finding to anyone.** It
explains how to tell a real finding from a false one, and lists the four false
positives this tool has actually produced so you know their shapes.

`TESTING_REAL_SITES.md` has the full procedure, including how to audit a
store running on your own machine.

## 8. Making a change

Never commit directly to `main`. Once two people are pushing, whoever pushes
second gets rejected, and a broken commit on `main` breaks everyone.

```powershell
git checkout main
git pull
git checkout -b yourname/what-you-are-doing
```

Make your change, then:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -q -m "not ui"
git add -A
git commit -m "Say what changed and why"
git push -u origin yourname/what-you-are-doing
```

Then on GitHub press **Compare & pull request**, describe what you changed, and
ask for a review. CI runs on the PR, so a broken change is caught before it
reaches `main`.

To pick up other people's work later: `git checkout main` then `git pull`.

### If you touch a detector

The tests in `tests/test_audit_regressions.py` each correspond to a bug that
was really in this codebase — mostly the detector reporting a *compliant* shop
as deceptive. If your change makes one of them fail, that is the test doing its
job. Read its docstring before changing it: it explains what went wrong and
why the assertion is worded the way it is.

Findings from this tool are public statements that a named company's checkout
is deceptive. A false positive is worse than a missed detection.

## 9. When something goes wrong

Every error below has actually happened to someone on this project.

**`run_demo.ps1 cannot be loaded because running scripts is disabled`**
You ran it without the bypass. Use the full command in section 4.

**`python : The term 'python' is not recognized`**
The PATH checkbox was missed, or your terminal was already open when Python was
installed. Close the terminal and open a new one first — that fixes it about
half the time. Otherwise re-run the installer and choose *Modify*.

**`No supported Python was found. This project needs Python 3.10 to 3.13.`**
No Python installed, or only 3.14. Install 3.12.10 (section 1). You do not need
to uninstall 3.14.

**`The existing .venv was built with Python 3.14, which is not supported.`**
An earlier attempt used the wrong Python. Delete it and re-run:

```powershell
Remove-Item -Recurse -Force .venv
powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
```

**`error: Microsoft Visual C++ 14.0 or greater is required`**
You are on Python 3.14 and pip is compiling from source. Do **not** install the
C++ Build Tools to work around this. Install 3.12.10, delete `.venv`, re-run.

**`ReadTimeoutError: HTTPSConnectionPool(host='files.pythonhosted.org')`**
A slow connection dropped a download. The launcher already gives pip a
120-second timeout and 10 retries, so this is rare now. Run the same command
again — pip keeps what it downloaded, so each attempt gets further. On campus
Wi-Fi, a tethered phone is often faster.

**`ERR_CONNECTION_REFUSED` in the browser**
The server is not running. Scroll up in the terminal: if a step failed, the
script prints `FAILED:` and stops, and the real error is just above that line.

**The page loads as plain text with no layout**
You are in VS Code's preview pane or another webview. Open it in Chrome or
Edge. See section 5.

**`address already in use` on port 8000**
An earlier run is still going. Close that window, or use another port:

```powershell
$env:PORT = "8010"
powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
```

**`Hostname resolves to a private/internal address (127.0.0.1) — refusing to crawl.`**
You started the server some way other than `run_demo.ps1`, so the
local-development flag was not set. This is the SSRF guard doing its job — on a
deployed server it stops the crawler being pointed at internal services. Start
it with `run_demo.ps1`.

**An audit says "Failed"**
The status line now names the actual exception rather than telling you to check
logs. Send that line along with the last twenty lines of the terminal.

## 10. macOS and Linux

Same steps, two differences: install Python 3.12 your usual way
(`brew install python@3.12`, or your distribution's package manager), and use
the shell launcher.

```bash
cd ~/Documents
git clone https://github.com/anishgoyal0603/ulterior.git
cd ulterior
./run_demo.sh
```

If it refuses with *permission denied*, run `chmod +x run_demo.sh` first.
Everything from section 5 onward is identical, except the test command:

```bash
.venv/bin/python -m pytest tests/ -q -m "not ui"
```

---

## 11. Ground rules

**Do not audit real commercial sites.** The dashboard accepts any URL, and the
crawler is deliberately built so it can never place an order or sign in as
anyone. That is not the same as having permission to be there. Crawling a live
shop without its operator's consent is a terms-of-service question and
potentially a legal one, and a finding published about a named company is a
defamation question. Use the bundled storefronts, or a site whose owner has
agreed in writing. **Ask Anish before running this against anything not in this
repository.**

**Never commit secrets.** `.env`, API keys and tokens are in `.gitignore` for a
reason. If you ever commit one, tell Anish immediately — the key has to be
revoked, because rewriting history does not un-publish it.

**Never force-push `main`.** It rewrites history for everyone and can destroy
work that was already pushed. If you think you need it, ask first.

The tool's own wording follows the same caution as these rules: findings
describe characteristics matching the Guidelines' definitions. They are not a
determination that any company has breached any law, and must not be presented
as one.

---

Stuck on something not listed above? Send the last twenty lines of the
terminal — the real error is almost always just above where it stopped.
