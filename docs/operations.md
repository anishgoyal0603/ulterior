# Running Ulterior for customers (managed pilot)

How the team operates Ulterior for paying pilot customers, and how to deploy
it so their data and evidence are safe. Everything here is built into the
code as of version 2.4 unless it says "manual".

---

## 1. Before an audit

1. **Written permission.** Get the store owner's signed form
   (`docs/pilot-authorization-template.md`). No form, no audit.
2. **Scope.** Write down the exact pages or the one starting URL. Do not audit
   anything outside it.
3. **Workspace.** Each customer organisation gets its own API key in its own
   workspace (section 4). Use that key for its audits, so its reports never mix
   with another customer's.

## 2. Running the audit

1. In the dashboard, paste the URL and fill **Permission to audit this site**
   with the signatory, the business and "Written permission from the owner",
   and the form's date as the reference.
2. Watch it run. **Stop this audit** cancels it; findings are only written
   when an audit finishes, so a stopped audit leaves no half-report.
3. When it is done, read **Coverage of the 13 patterns**. Anything
   *Not assessed* was not checked (the reason is shown). Do not tell the
   customer those patterns are absent.

## 3. Human review before anything is sent (required)

One person who did not set up the audit checks every finding:

- [ ] Open the screenshot for the step and find the thing the finding describes.
- [ ] Check the numbers (prices, fees, totals) against the screenshot.
- [ ] Check the wording is "resembles a pattern", never "violates" or "illegal".
- [ ] Remove or note anything that is wrong (for example a price read from the wrong element).
- [ ] Press **Verify evidence**: it must say *Intact*.
- [ ] Record the review in the dashboard (**Reviewed by**, outcome, note).
      It is printed on the PDF. A report without a review says
      "Not yet reviewed by a person" on its first page.

Only then download the PDF and send it.

## 4. Organisations and keys

Write keys as `org:key` in `API_KEYS`:

```
API_KEYS=team:<team key>,parkhya:<parkhya key>
ADMIN_API_KEYS=<team key>
```

- A key sees only its own organisation's audits, reports, evidence,
  comparisons and summary. Another organisation's audit answers "not found".
- Plain keys (no `org:`) belong to the team. Audits made before 2.4 belong to the team.
- Admin keys see every organisation and can delete. Never give a customer an admin key.
- Generate a key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Per organisation, at most `MAX_ACTIVE_AUDITS_PER_ORG` audits (default 3) can be queued or running.

Customers do not log in themselves yet (accounts with passwords are the next
milestone). During the managed pilot the team runs audits and sends reports.

## 5. Re-check after fixes

Run the same pages again, open the new audit, type the earlier audit's
number in **Re-check against audit #** and press **Compare**. The PDF then
lists what is no longer observed, still observed and new.

## 6. Data handling (manual policy)

- Screenshots of checkout pages can contain personal data. Audit with a test
  account or as a guest, never with a real customer's account.
- Default retention is 30 days (`RETENTION_DAYS`). If a customer needs the
  evidence for its annual self-audit, agree a longer period in writing and set
  it for that deployment; or give them the PDF and delete the audit.
- Delete on request: `DELETE /audits/{id}` with an admin key removes the
  audit, its findings and its screenshots.
- Disputed finding: reply within 2 working days, re-check the screenshot, and
  if the finding was wrong, record a review with outcome "Checked; some
  rejected" and send a corrected report.

## 7. Deployment checklist

- [ ] PostgreSQL with `sslmode=require` in `DATABASE_URL`.
- [ ] `EVIDENCE_DIR` on a **persistent** volume (otherwise a redeploy deletes the
      screenshots and Verify evidence reports *Incomplete*).
- [ ] The container runs as the unprivileged `pwuser`, not root. If the host
      mounts the evidence volume as root-owned (Railway does), the first audit
      fails with "Permission denied" on the screenshot folder. On Railway set
      `RAILWAY_RUN_UID=0` for that service only if you cannot change the
      volume's owner, and know that it runs the crawler as root again.
- [ ] `API_KEYS` with organisation keys; `ADMIN_API_KEYS` set explicitly
      (production refuses to start without it).
- [ ] `PUBLIC_BASE_URL` = this deployment's public https address (production
      refuses to start with the 127.0.0.1 default while the demo is on).
- [ ] `TRUST_PROXY_HEADERS=true` and `TRUSTED_PROXY_HOPS=1` behind Railway/nginx.
      This is also what switches on uvicorn's proxy headers in the container
      (https redirects); without a proxy in front, leave it `false`, so a
      client cannot choose its own address with `X-Forwarded-For`.
- [ ] Daily database backup, and one test restore done before the first customer.
- [ ] Either `RUN_AUDIT_WORKERS=true` (one process does everything), or
      `RUN_AUDIT_WORKERS=false` on the API plus `python -m app.worker` running
      separately (see `docker-compose.yml`).
- [ ] After deploying, run one audit of the practice store, press Verify
      evidence (Intact), restart the service, and press it again (still Intact).

### Jobs survive restarts

Audits are stored in the database before a browser starts. If the server
restarts mid-audit, the job's heartbeat stops; after `JOB_STALE_SECONDS`
(default 180) it is restarted, up to `JOB_MAX_ATTEMPTS` (default 2), then
marked failed with an explanation. Queued audits start again on their own.

### Browser safety

Every crawling browser is launched by `capture/browser.py`: WebRTC is removed,
WebSockets go through the same address check as every other request, and
private, link-local, carrier-grade NAT (100.64.0.0/10), NAT64, 6to4 and
IPv4-mapped addresses are refused, including after redirects.

All browser traffic also goes through a local egress proxy
(`capture/egress_proxy.py`) that resolves each host once, checks every
address, and connects only to the address it checked. That closes DNS
rebinding (a hostname that answers "public" to the first lookup and
"169.254.169.254" to the browser's own lookup). `EGRESS_PROXY=off` disables it
for diagnosing a site; leave it on otherwise.

Still to do before customers run audits themselves: put the worker in its own
network that can reach the internet only through an egress proxy, and prove in
staging that direct connections to internal addresses fail.
