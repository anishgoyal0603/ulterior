# Pushing Ulterior to a private GitHub repo

**Use `ulterior-repo.zip`.** That is the only file you need — it already
contains the full git repository (7 commits, branch `main`, authored as
Anish Goyal <anishgoyal0603@gmail.com>). You are not creating a repo from
scratch; you are attaching an existing one to GitHub.

Verified before packing: working tree clean, no `.env`, no database file, no
API keys, no `.venv`, no `__pycache__`. 98 tracked files.

---

## Step 1 — Extract

```bash
unzip ulterior-repo.zip
cd darkpattern-auditor
```

**Immediately check the history survived:**

```bash
git log --oneline
```

You should see 7 commits, newest first:

```
493b35c Draw the dashboard chart inline; drop the CDN dependency
174017d Add a public, clickable demo: hosted storefront, live audit page, launcher
704e7ec Document architecture and refresh next steps
052c418 Cover all 13 CCPA patterns with stated evidence tiers
828094f Add NEXT_STEPS.md: verified state, AppRay analysis, deploy path
e6cb5d4 Bind uvicorn to $PORT so PaaS deploys pass healthchecks
5a89ede Ulterior — Dark Pattern Auditor: initial commit
```

> **If `git log` says "not a git repository"**, your unzip tool skipped the
> hidden `.git` folder. Some Windows extractors do this. Re-extract with
> `unzip` (Git Bash / WSL), or 7-Zip, or `tar -xf` — not Explorer's built-in
> "Extract All" if it is set to skip hidden files. Without `.git` you would be
> pushing a folder with no history, which loses the whole build record.

---

## Step 2 — Create the private repo on GitHub

**Create it EMPTY.** Do not tick "Add a README", ".gitignore", or a licence.
If GitHub puts even one commit in the repo, your push is rejected as
non-fast-forward and you have to start untangling it.

### Option A — the website (no tools needed)

1. Go to <https://github.com/new>
2. **Repository name:** `ulterior-dark-pattern-auditor`
3. **Visibility:** **Private**
4. Leave *Add a README file*, *Add .gitignore* and *Choose a license* all
   **unticked**
5. **Create repository**
6. Copy the URL it shows you — `https://github.com/<your-username>/ulterior-dark-pattern-auditor.git`

### Option B — the `gh` CLI (creates and pushes in one command)

Only if you already have GitHub CLI installed and logged in (`gh auth login`):

```bash
gh repo create ulterior-dark-pattern-auditor --private --source=. --remote=origin --push
```

That does Step 3 as well. Skip to Step 4.

---

## Step 3 — Point your copy at GitHub and push

Replace `<your-username>` with your actual GitHub username in both lines.

```bash
git remote add origin https://github.com/<your-username>/ulterior-dark-pattern-auditor.git
git push -u origin main
```

### Authenticating the push

GitHub stopped accepting account passwords over HTTPS. When it prompts:

- **Username:** your GitHub username
- **Password:** a **Personal Access Token**, not your password

To make one: <https://github.com/settings/personal-access-tokens/new>

- **Token name:** `ulterior-push`
- **Expiration:** 7 days (you only need it once)
- **Repository access:** *Only select repositories* → pick
  `ulterior-dark-pattern-auditor`
- **Permissions → Repository permissions → Contents:** **Read and write**
- Generate, copy it, paste it as the password

**Revoke it after the push** at <https://github.com/settings/tokens?type=beta>.
A token scoped to one repo for one week is already low-risk, but a token you
have finished with is a token you should delete.

<details>
<summary>Prefer SSH? (no token prompts ever again)</summary>

```bash
ssh-keygen -t ed25519 -C "anishgoyal0603@gmail.com"     # press Enter 3 times
cat ~/.ssh/id_ed25519.pub                                # copy this whole line
```

Paste it at <https://github.com/settings/ssh/new>, then:

```bash
git remote add origin git@github.com:<your-username>/ulterior-dark-pattern-auditor.git
git push -u origin main
```
</details>

---

## Step 4 — Confirm it landed

```bash
git remote -v          # should show your GitHub URL, fetch and push
git log origin/main --oneline | head -1   # should show 493b35c
```

Then open the repo page. You should see 98 files, `README.md` rendered on the
front page, and a **private** badge next to the repo name.

**Check CI is running:** click the **Actions** tab. `.github/workflows/ci.yml`
runs on every push — 98 Python tests and 12 Node tests, in the same Playwright
container the Dockerfile deploys with. A green tick there is worth showing a
judge; it means the numbers in your deck are re-verified on every commit rather
than asserted once.

---

## Step 5 — Run it locally

```bash
./run_demo.sh
```

Then open **<http://127.0.0.1:8000/demo/>** and press **Run the audit**.

- **macOS / Linux:** works as-is.
- **Windows / PowerShell:** use the PowerShell launcher instead —
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
  ```
  Typing `./run_demo.sh` in PowerShell does **not** run it. PowerShell does not
  know what a .sh file is, hands it to the file association, and returns
  instantly with no output — which looks exactly like success. Nothing starts,
  and the browser then shows ERR_CONNECTION_REFUSED.

  `-ExecutionPolicy Bypass` is required, not superstition: Windows marks files
  that came out of a downloaded zip, and the default policy refuses to run them.
- **Windows / Git Bash or WSL:** `./run_demo.sh` works there.

If port 8000 is busy: `PORT=8010 ./run_demo.sh`.

---

## Step 6 — The one screenshot only you can take

With the server running, in Chrome:

1. `chrome://extensions` → **Developer mode** on (top right)
2. **Load unpacked** → select the `extension/` folder
3. Open <http://127.0.0.1:8000/storefront/cart.html>
4. Click the extension icon → screenshot the popup

It will show the live DP-01/02/03/08/11/12 flags found client-side on that
page. That image belongs on slide 2 of the deck, beside the live-audit
screenshot — it shows the same engine protecting a shopper in real time, not
only auditing after the fact.

---

## Step 7 — Then deploy

Once the repo exists, send me `owner/repo` and I can drive the whole Railway
side: Postgres, the app service, every environment variable, the domain, and
the first live audit. The two gotchas that will otherwise stop the boot are
written up in `NEXT_STEPS.md` §3 — production refuses to start without
`?sslmode=require` on `DATABASE_URL`, and refuses a wildcard or plain-HTTP
CORS origin. Both are deliberate.

---

## Housekeeping for later commits

Your existing 7 commits are already attributed to you. For anything you commit
from this machine afterwards:

```bash
git config user.name  "Anish Goyal"
git config user.email "anishgoyal0603@gmail.com"
```

`.gitignore` already excludes `.env`, `*.db`, `.venv/`, captured screenshots
and generated reports. Keep real API keys in `.env` only — never in a tracked
file, and never in a commit message.
