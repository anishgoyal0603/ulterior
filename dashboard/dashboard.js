/**
 * SAME-ORIGIN BY DEFAULT.
 *
 * This was hardcoded to http://localhost:8000, which meant the dashboard
 * worked on a developer's laptop and nowhere else: deployed, it would call
 * the developer's own machine and show nothing. It also forced a
 * cross-origin setup, and production config REQUIRES a non-wildcard HTTPS
 * CORS origin -- so a separately hosted dashboard needs its own domain,
 * its own certificate and a CORS entry before it can make one request.
 *
 * The API now serves this file itself (see app/main.py), so an empty base
 * means "same origin as this page" and CORS never enters into it. The
 * file:// branch keeps `open index.html` working while developing.
 */
const API = location.protocol === "file:" ? "http://localhost:8000" : "";
let pollTimer = null;

/**
 * The API requires a key in production. The key is held in memory only and
 * deliberately NOT written to localStorage: this dashboard renders content
 * captured from hostile websites, so anything persisted in this origin is
 * reachable by a stored-XSS payload that gets past the escaping below. A
 * key the browser never stores cannot be stolen from storage.
 */
function apiKey() {
  const el = document.getElementById("api-key");
  return el && el.value.trim() ? el.value.trim() : null;
}

/**
 * Every failure the server can return has to arrive as an Error with a
 * sentence a person can act on.
 *
 * This used to special-case 401/403 and then call r.json() on everything
 * else. A 429 is valid JSON, so it sailed through as {detail: "Rate limit
 * exceeded"} and the caller did jobs.slice(...) on an object -- a TypeError
 * about `.slice` surfaced as "Could not load", which tells a rate-limited
 * user nothing about waiting a minute. A 502 from a proxy is not JSON at all
 * and threw a SyntaxError about "Unexpected token <".
 */
/**
 * A 401/403 is not a failure to report, it is a state to show: the dashboard
 * is locked until someone connects with a key. Giving it its own type lets
 * boot() and the run buttons tell "locked" apart from "the server is down".
 */
class AuthError extends Error {}
/** The key is valid but this part is not for its organisation (403). */
class ForbiddenError extends Error {}

/*
 * "unknown" until the first protected call answers; then "open" (local
 * development, no key configured), "ok" (a key was accepted), "locked" (a key
 * is required and none was given) or "bad" (a key was given and refused).
 */
let authState = "unknown";

function setAuthState(state, detail) {
  authState = state;
  const card = document.getElementById("keycard");
  const note = document.getElementById("key-state");
  const gate = document.getElementById("auth-gate");
  card.classList.remove("is-locked", "is-bad", "is-ok");
  gate.hidden = !(state === "locked" || state === "bad");

  if (state === "locked") {
    card.classList.add("is-locked");
    note.textContent = "Locked \u2014 paste your access key and press Connect.";
    document.getElementById("gate-title").textContent = "Connect to run real audits";
  } else if (state === "bad") {
    card.classList.add("is-bad");
    note.textContent = "That key was not accepted. Check it and press Connect again.";
    document.getElementById("gate-title").textContent = "That key was not accepted";
  } else if (state === "ok") {
    card.classList.add("is-ok");
    note.textContent = "Connected \u2713" + (detail ? " \u00b7 " + detail : "")
      + " \u00b7 key held in this tab only.";
  } else if (state === "open") {
    card.classList.add("is-ok");
    note.textContent = "Local mode \u2014 this server needs no key."
      + (detail ? " \u00b7 " + detail : "");
  }

  if (state === "locked" || state === "bad") {
    const sel = document.getElementById("adapter-select");
    sel.replaceChildren(new Option("Connect with your API key to load sites", ""));
    ["kpi-audits", "kpi-violations", "kpi-top"].forEach((id) => {
      document.getElementById(id).textContent = "\u2014";
    });
    // Everything the previous key could see goes too: on a shared browser,
    // clearing the key or typing a wrong one left the last organisation's
    // audits, findings, chart and reports on screen.
    endAuditRun();
    document.getElementById("jobs-table").replaceChildren();
    renderPatternChart([]);
    clearJobPanel();
    document.getElementById("violations-title").textContent = "Connect with your API key to see audits";
    document.getElementById("reports-table").replaceChildren();
    document.getElementById("reports-card").hidden = true;
  }
}

/** Point the person at the key box instead of sending a request that will 401. */
function askForKey() {
  const input = document.getElementById("api-key");
  input.focus();
  const card = document.getElementById("keycard");
  card.classList.remove("nudge");
  void card.offsetWidth;            // restart the animation on repeat clicks
  card.classList.add("nudge");
  const status = document.getElementById("run-status");
  status.textContent = "Enter your API key above and press Connect first.";
  status.classList.add("is-error");
}

async function authFetch(path, options) {
  const opts = Object.assign({}, options);
  opts.headers = Object.assign({}, opts.headers || {});
  const key = apiKey();
  if (key) opts.headers["X-API-Key"] = key;

  const r = await fetch(`${API}${path}`, opts);

  if (r.ok) {
    try {
      return await r.json();
    } catch (e) {
      throw new Error("The server replied with something that was not JSON.");
    }
  }

  // 403 is NOT a bad key: a customer organisation's key is valid but may
  // not read the team's own panels (extension reports). Treating it as 401
  // locked the whole dashboard and told a paying customer "That key was not
  // accepted".
  if (r.status === 403 && key) {
    throw new ForbiddenError("This part of the dashboard is not available for your organisation.");
  }
  if (r.status === 401 || r.status === 403) {
    // Record which kind of locked this is, so every panel agrees.
    setAuthState(key ? "bad" : "locked");
    throw new AuthError(key ? "That API key was not accepted."
                            : "This dashboard needs an API key \u2014 enter it above.");
  }
  if (r.status === 429) {
    // Two different 429s: the per-minute rate limit, and this workspace's
    // limit on audits running at once. The server's own words say which.
    let why = "";
    try {
      const body = await r.json();
      if (body && typeof body.detail === "string") why = body.detail;
    } catch (e) { /* not JSON */ }
    if (why && !/^rate limit/i.test(why)) throw new Error(why);
    throw new Error("Too many requests \u2014 audits are limited to a few per "
                    + "minute. Wait a moment and try again.");
  }

  // Prefer the server's own explanation; fall back to the status.
  let detail = "";
  try {
    const body = await r.json();
    if (body && typeof body.detail === "string") detail = body.detail;
  } catch (e) { /* not JSON -- the status line is all we have */ }
  throw new Error(detail || `The server returned ${r.status} ${r.statusText}.`);
}

/**
 * SECURITY — stored XSS defence.
 *
 * Every string this dashboard renders originates from a WEBSITE WE AUDITED:
 * button labels, checkbox text, site names. An audited site is by definition
 * untrusted and often actively adversarial (we are accusing it of deceptive
 * design). A site operator who wanted to attack an auditor would put
 * `<img src=x onerror=...>` in a decline-button label; it would flow through
 * capture -> DB -> API -> this page.
 *
 * So: NEVER interpolate audit data into innerHTML. Use escapeHtml() for
 * template strings, or textContent when building nodes.
 */
function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadAdapters() {
  const adapters = await authFetch("/adapters");
  const sel = document.getElementById("adapter-select");
  sel.innerHTML = Object.entries(adapters)
    .map(([k, v]) => `<option value="${escapeHtml(k)}">${escapeHtml(v)}</option>`).join("");
  // /adapters is the cheapest protected call, so its answer decides whether
  // the dashboard is connected.
  const n = Object.keys(adapters).length;
  setAuthState(apiKey() ? "ok" : "open", `${n} registered site${n === 1 ? "" : "s"}`);
}

/**
 * One runner for both entry points. `payload` is either {adapter_name} or
 * {target_urls}; the API rejects both-at-once and neither-at-all, so the UI
 * does not need to re-police that.
 */
/**
 * Re-enable every audit button and stop any poll in flight.
 *
 * Both entry points shared ONE pollTimer, and each button's re-enable lived
 * only inside that timer's callback. Starting an adapter audit while a URL
 * audit was polling cleared the first timer, so the first button never got
 * its re-enable and stayed disabled for the rest of the session. Ending a run
 * has to restore the page, not just the button that happened to start it.
 */
let runningJobId = null;
let stopRequested = false;
function endAuditRun() {
  clearTimeout(pollTimer);
  pollTimer = null;
  runningJobId = null;
  stopRequested = false;
  document.getElementById("cancel-audit").hidden = true;
  ["run-audit", "run-urls"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
}

async function startAudit(payload, btn) {
  // Known to be locked and still no key typed: say so, do not send a request
  // that can only come back 401.
  if ((authState === "locked" || authState === "bad") && !apiKey()) {
    askForKey();
    return;
  }
  // Whatever was running is over: stop its timer and give its button back
  // before this run takes over.
  endAuditRun();
  btn.disabled = true;
  const status = document.getElementById("run-status");
  status.classList.remove("is-error");
  status.textContent = "Starting…";

  let job;
  try {
    job = await authFetch("/audits", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(Object.assign({
        // Offline language detection always runs server-side; this flag only
        // ADDS an LLM pass on top of it. Off by default so an audit costs
        // nothing and sends nothing off-box unless explicitly asked.
        use_llm: document.getElementById("use-llm").checked
      }, payload))
    });
  } catch (err) {
    endAuditRun();
    if (err instanceof AuthError) {
      askForKey();
      status.textContent = err.message;
      return;
    }
    // Shown right under the button that was pressed -- a refused URL is an
    // answer to what the person just did, not a page-level outage.
    status.textContent = err && err.message ? err.message : "The audit was not accepted.";
    status.classList.add("is-error");
    return;
  }

  // A rejected URL comes back as a 400 with a readable reason from the SSRF
  // guard ("resolves to a private address", "credentials in URL"). Show it
  // rather than spinning forever on a job that was never created.
  if (job.id === undefined) {
    endAuditRun();
    status.textContent = "";
    showConnectionError(new Error(job.detail || "The audit was not accepted."));
    return;
  }

  const started = Date.now();
  let consecutiveFailures = 0;
  runningJobId = job.id;
  document.getElementById("cancel-audit").hidden = false;

  // Chained timeouts, not setInterval: the next poll is scheduled only after
  // this one has finished, so a slow response can never overlap the next
  // request (setInterval fired again while the previous fetch was pending).
  const tick = async () => {
    const secs = Math.round((Date.now() - started) / 1000);

    // The poll used to await this fetch with NO error handling. One failed
    // tick -- a 500, a dropped connection, a rate limit -- rejected inside
    // setInterval, where nothing catches it and nothing clears the interval.
    // The page then said "Running... 47s" forever with the button disabled:
    // a transient blip became a permanent hang that only a reload fixed.
    //
    // One failure is tolerated and retried, because a single dropped request
    // during a two-minute crawl is not a reason to abandon a finished audit.
    // Three in a row is a real problem and the page says so and gives itself
    // back to the user.
    let j;
    try {
      j = await authFetch(`/audits/${job.id}`);
      consecutiveFailures = 0;
    } catch (err) {
      consecutiveFailures += 1;
      if (consecutiveFailures >= 3) {
        endAuditRun();
        status.textContent = `Lost contact with the server after ${secs}s \u2014 `
          + (err && err.message ? err.message : "the audit may still be running.");
        status.classList.add("is-error");
      }
      return;
    }

    // Once Stop was pressed, keep saying so: the next poll used to overwrite
    // "Stopping..." with "Running..." a second later.
    status.textContent = stopRequested
      ? `Stopping\u2026 ${secs}s \u2014 the browser finishes its current page first.`
      : j.status === "pending"
      ? `Queued\u2026 ${secs}s \u2014 waiting for a free browser.`
      : `Running\u2026 ${secs}s \u2014 a real browser is walking the site (usually 20\u201390 s).`;
    status.classList.remove("is-error");

    // A run that never ends is a hang with a timer on it. The server's own
    // walk is bounded, so past this point something is wrong upstream.
    if (secs > 300) {
      endAuditRun();
      status.textContent = `Still running after ${secs}s \u2014 check the terminal `
        + "running the server.";
      status.classList.add("is-error");
      return;
    }

    if (j.status === "done" || j.status === "failed" || j.status === "cancelled") {
      endAuditRun();
      // Name the failure. error_message carries the exception type and job id;
      // the server withholds the traceback on purpose (it can contain a DB
      // password) but the type is safe and usually identifies the cause.
      status.textContent = j.status === "done"
        ? `Done \u2713 (${secs}s)`
        : j.status === "cancelled"
          ? (j.error_message || "Cancelled.")
          : (j.error_message || "Failed \u2717 \u2014 see the terminal running the server");
      status.classList.toggle("is-error", j.status !== "done");
      showJob(j);
      loadDashboard({keepSelection: true}).catch(showConnectionError);
    }
  };
  const loop = async () => {
    if (runningJobId !== job.id) return;          // this run was ended or replaced
    await tick();
    if (runningJobId === job.id) pollTimer = setTimeout(loop, 1000);
  };
  pollTimer = setTimeout(loop, 1000);
}

function runAudit() {
  return startAudit(
    {adapter_name: document.getElementById("adapter-select").value},
    document.getElementById("run-audit"));
}

/**
 * People paste "flipkart.com/cart", not "https://flipkart.com/cart", and the
 * server's SSRF guard rightly refuses anything without an http(s) scheme. So
 * a bare host gets https:// in front, and a path starting with "/" means this
 * same server (the try-it buttons use that for the bundled storefronts).
 * Anything already carrying a scheme is left exactly as typed, so the guard
 * still sees -- and refuses -- javascript:, file:, ftp: and friends.
 */
function normaliseUrl(u) {
  if (u.startsWith("/")) return location.origin + u;
  if (/^[a-z][a-z0-9+.-]*:/i.test(u) && !/^[^:/]+:\d+(\/|$)/.test(u)) return u;
  return "https://" + u;
}

function runUrlAudit() {
  const box = document.getElementById("target-urls");
  const urls = box.value.split(",").map(u => u.trim()).filter(Boolean).map(normaliseUrl);
  const status = document.getElementById("run-status");
  if (!urls.length) {
    status.textContent = "Paste a product, cart or home-page URL first.";
    status.classList.add("is-error");
    box.focus();
    return;
  }
  box.value = urls.join(", ");      // show exactly what will be audited
  const discover = document.getElementById("auto-discover").checked;
  if (discover && urls.length > 1) {
    status.textContent = "\u201cFind the checkout by itself\u201d starts from ONE URL. "
      + "Turn it off to audit each of these pages as a step, or keep only the first.";
    status.classList.add("is-error");
    return;
  }
  // Locked and no key typed: ask for the key first -- the permission box
  // would be a pointless step before a request that can only 401.
  if ((authState === "locked" || authState === "bad") && !apiKey()) {
    askForKey();
    return;
  }
  const authorization = readPermission();
  if (!authorization) {
    status.textContent = "Fill in who gave permission to audit this site, and tick the confirmation box.";
    status.classList.add("is-error");
    return;
  }
  return startAudit({
    target_urls: urls,
    auto_discover: discover,
    authorization,
  }, document.getElementById("run-urls"));
}

/**
 * The permission record every pasted-URL audit needs. Returns null (and
 * marks the box) when something required is missing; the server checks the
 * same rules again.
 */
function readPermission() {
  const box = document.getElementById("perm");
  const owner = document.getElementById("perm-owner").value.trim();
  const org = document.getElementById("perm-org").value.trim();
  const confirmed = document.getElementById("perm-confirm").checked;
  const ok = owner.length >= 2 && org.length >= 2 && confirmed;
  box.classList.toggle("is-error", !ok);
  if (!ok) {
    (owner.length < 2 ? document.getElementById("perm-owner")
      : org.length < 2 ? document.getElementById("perm-org")
      : document.getElementById("perm-confirm")).focus();
    return null;
  }
  return {
    owner_name: owner,
    organisation: org,
    authority: document.getElementById("perm-authority").value,
    reference: document.getElementById("perm-ref").value.trim(),
    confirmed: true,
  };
}

/** Fill the permission box for the built-in try-it targets, whose status is known. */
function fillPermission(owner, org, authority) {
  document.getElementById("perm-owner").value = owner;
  document.getElementById("perm-org").value = org;
  document.getElementById("perm-authority").value = authority;
  document.getElementById("perm-ref").value = "";
  document.getElementById("perm-confirm").checked = true;
  document.getElementById("perm").classList.remove("is-error");
}

const AUTHORITY_TEXT = {
  owner: "store owner",
  written_permission: "written permission from the owner",
  public_test_site: "public test site",
};

/** Permission record, evidence fingerprint and the re-check tools for one audit. */
function renderTrust(job, changed) {
  const host = document.getElementById("job-trust");
  const done = job.status === "done";
  host.hidden = !done;
  if (changed) {
    document.getElementById("trust-result").textContent = "";
    document.getElementById("baseline-id").value = "";
    // A note typed for the previous audit must not be recorded on this one.
    document.getElementById("review-note").value = "";
    const outcome = document.getElementById("review-outcome");
    outcome.selectedIndex = 0;
  }
  if (!done) return;

  const perm = document.getElementById("trust-perm");
  perm.replaceChildren();
  const b1 = document.createElement("strong");
  b1.textContent = "Permission: ";
  const a = job.authorization;
  perm.append(b1, document.createTextNode(a
    ? `${a.owner_name}, ${a.organisation} (${AUTHORITY_TEXT[a.authority] || a.authority})`
      + (a.reference ? ` \u2014 ${a.reference}` : "")
    : "not needed (registered demo or sandbox site)"));

  const fp = document.getElementById("trust-print");
  fp.replaceChildren();
  const b2 = document.createElement("strong");
  b2.textContent = "Evidence fingerprint: ";
  const m = job.evidence_manifest;
  const code = document.createElement("span");
  code.className = "fp";
  code.textContent = m ? m.fingerprint : "not available (audit made before fingerprints existed)";
  fp.append(b2, code);
  document.getElementById("verify-evidence").disabled = !m;
  renderCoverage(job);
  renderReview(job);
}

async function verifyEvidence() {
  if (!selectedJobId) return;
  const out = document.getElementById("trust-result");
  out.textContent = "Checking\u2026";
  try {
    const v = await authFetch(`/audits/${Number(selectedJobId)}/verify-evidence`);
    const shots = (v.screenshots || []);
    const ok = shots.filter(s => s.status === "match").length;
    const msg = {
      intact: `Intact: all ${shots.length} screenshot(s) and the findings match their fingerprints.`,
      incomplete: `Nothing altered, but ${shots.length - ok} of ${shots.length} screenshot(s) were deleted by the retention policy; the rest match.`,
      changed: "Changed: some evidence no longer matches its fingerprint. Do not rely on this audit; run it again.",
      unavailable: v.detail || "No fingerprints for this audit.",
    }[v.status] || v.status;
    out.textContent = msg;
  } catch (err) {
    out.textContent = "Could not verify: " + err.message;
  }
}

/** Stop the audit that is running now. */
async function cancelRunningAudit() {
  if (!runningJobId) return;
  const status = document.getElementById("run-status");
  try {
    stopRequested = true;
    const r = await authFetch(`/audits/${Number(runningJobId)}/cancel`, {method: "POST"});
    status.textContent = r.status === "cancelled" ? "Cancelled." : "Stopping\u2026 the browser finishes its current page first.";
  } catch (err) {
    status.textContent = "Could not stop it: " + err.message;
  }
}

const COVERAGE_LABEL = {
  observed: "Observed", not_observed: "Not observed",
  partially_assessed: "Partially assessed", not_assessed: "Not assessed",
};

/** What each of the 13 patterns could and could not be checked on. */
function renderCoverage(job) {
  const box = document.getElementById("trust-coverage");
  const list = document.getElementById("coverage-list");
  list.replaceChildren();
  const cov = job.coverage || [];
  box.hidden = !cov.length;
  if (!cov.length) return;
  const count = (st) => cov.filter(c => c.status === st).length;
  document.getElementById("coverage-summary").textContent =
    `Coverage of the 13 patterns: ${count("observed")} observed, ${count("not_observed")} not observed, `
    + `${count("partially_assessed")} partially assessed, ${count("not_assessed")} not assessed`;
  cov.forEach((c) => {
    const li = document.createElement("li");
    li.className = "cov-" + c.status;
    li.textContent = `${c.code} ${c.name}: ${COVERAGE_LABEL[c.status] || c.status}` + (c.note ? ` \u2014 ${c.note}` : "");
    list.append(li);
  });
}

function renderReview(job) {
  const p = document.getElementById("trust-review");
  p.replaceChildren();
  const b = document.createElement("strong");
  b.textContent = "Human review: ";
  const r = job.review;
  p.append(b, document.createTextNode(r
    ? `${r.reviewer}, ${r.reviewed_at} (${r.outcome.replace("_", " ")})` + (r.note ? ` \u2014 ${r.note}` : "")
    : "not yet reviewed. Check every finding against its screenshot before sending the report."));
}

async function saveReview() {
  if (!selectedJobId) return;
  const out = document.getElementById("trust-result");
  const reviewer = document.getElementById("review-name").value.trim();
  if (reviewer.length < 2) {
    out.textContent = "Type the name of the person who checked the findings.";
    document.getElementById("review-name").focus();
    return;
  }
  try {
    const job = await authFetch(`/audits/${Number(selectedJobId)}/review`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        reviewer,
        outcome: document.getElementById("review-outcome").value,
        note: document.getElementById("review-note").value.trim(),
      }),
    });
    renderReview(job);
    // Keep the list's copy in step, or re-opening this audit from the list
    // showed "Not yet reviewed" again.
    const i = JOBS.findIndex((j) => j.id === job.id);
    if (i >= 0) JOBS[i] = Object.assign({}, JOBS[i], job);
    out.textContent = "Review recorded. It is printed on the PDF report.";
  } catch (err) {
    out.textContent = "Could not record the review: " + err.message;
  }
}

/** Re-check after fixes: compare this audit with an earlier one of the same pages. */
let baselineId = null;
async function compareWithBaseline() {
  if (!selectedJobId) return;
  const out = document.getElementById("trust-result");
  const n = Number(document.getElementById("baseline-id").value);
  if (!Number.isInteger(n) || n < 1 || n === Number(selectedJobId)) {
    out.textContent = "Enter the number of an earlier, finished audit of the same pages.";
    return;
  }
  out.textContent = "Comparing\u2026";
  try {
    const c = await authFetch(`/audits/${Number(selectedJobId)}/compare?baseline=${n}`);
    baselineId = n;
    const names = (list) => list.map(i => `${i.pattern_code} (${i.step_name})`).join(", ") || "none";
    out.textContent = `Since audit #${n}: ${c.resolved.length} no longer observed [${names(c.resolved)}]; `
      + `${c.still_present.length} still observed [${names(c.still_present)}]; `
      + `${c.new.length} new [${names(c.new)}].`
      + (c.same_site ? "" : " Note: the two audits are of different sites.")
      + " The PDF report will include this comparison.";
  } catch (err) {
    baselineId = null;
    out.textContent = "Could not compare: " + err.message;
  }
}

/**
 * Show how the crawl actually went. An audit that quietly examined one page,
 * when the user believed it walked a whole funnel, would be worse than one
 * that found nothing -- so "stages not found" is shown as prominently as the
 * findings themselves.
 */
function renderDiscovery(discovery) {
  const host = document.getElementById("discovery-report");
  host.replaceChildren();
  if (!discovery) { host.hidden = true; return; }
  host.hidden = false;

  // Classes, not element.style: a stricter Content-Security-Policy (an IDE
  // preview pane, an embedded webview) refuses inline styles, and the colours
  // below were hardcoded light-mode hexes that turned unreadable in dark mode.
  // Both problems disappear when the stylesheet owns the appearance.
  const line = (label, value, kind) => {
    const p = document.createElement("p");
    p.className = "disc-line" + (kind ? " disc-" + kind : "");
    const b = document.createElement("strong");
    b.textContent = label + " ";
    p.append(b, document.createTextNode(value));
    return p;
  };

  const reached = (discovery.stages_reached || []).join(" \u2192 ") || "none";
  host.append(line("Pages walked:", reached, "ok"));
  if ((discovery.stages_not_found || []).length) {
    host.append(line("Could not find:", discovery.stages_not_found.join(", "), "warn"));
  }
  (discovery.notes || []).forEach((n) => {
    const p = document.createElement("p");
    p.className = "disc-note";
    p.textContent = n;
    host.append(p);
  });
}

/** Show one audit's findings in the bottom panel, and name which one it is. */
let selectedJobId = null;
function showJob(job) {
  const selectedJobIdBefore = selectedJobId;
  selectedJobId = job.id;
  const title = document.getElementById("violations-title");
  title.textContent = `Audit #${job.id} \u2014 ${job.site_name || job.adapter_name}`
    + (job.status === "done" ? "" : ` (${job.status})`);
  renderDiscovery(job.discovery);
  const changedJob = selectedJobIdBefore !== job.id;
  if (changedJob) baselineId = null;
  renderTrust(job, changedJob);
  document.getElementById("download-pdf").hidden = job.status !== "done";
  if (job.status === "failed" || job.status === "cancelled") {
    const tbody = document.getElementById("violations-table");
    tbody.replaceChildren();
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "cov-note";
    td.textContent = job.error_message || "This audit failed.";
    tr.append(td);
    tbody.append(tr);
  } else {
    renderViolations(job.violations || []);
  }
  document.querySelectorAll("#jobs-table tr").forEach((tr) => {
    tr.classList.toggle("is-selected", Number(tr.dataset.id) === job.id);
  });
}

function renderViolations(violations) {
  const tbody = document.getElementById("violations-table");
  tbody.innerHTML = violations.length ? violations.map(v => `
    <tr>
      <td><span class="badge">${escapeHtml(v.pattern_code)}</span><br>${escapeHtml(v.pattern_name)}</td>
      <td>${escapeHtml(v.step_name)}</td>
      <td class="layer-tag">Layer ${escapeHtml(v.layer)}</td>
      <td>
        <span class="tier tier-${escapeHtml((v.evidence && v.evidence.evidence_tier) || "unstated")}">${escapeHtml((v.evidence && v.evidence.evidence_tier) || "unstated")}</span>
        <br>${(Number(v.confidence)*100).toFixed(0)}%
      </td>
      <td>${escapeHtml(v.explanation)}</td>
    </tr>`).join("") : `<tr><td colspan="5" class="cov-note">No violations found on this run.</td></tr>`;
}


/* ---------------------------------------------------------------------------
 * Violations by CCPA pattern — inline SVG, no charting library.
 *
 * This was a Chart.js bar chart pulled from cdnjs. Screenshotting the
 * dashboard on a network where that CDN is blocked showed why that was a bad
 * dependency: `Chart` was undefined, the exception rejected the whole boot
 * promise, and the ENTIRE dashboard rendered as an error banner — coverage
 * table, KPIs, job history and all — over one optional chart. A pitch venue
 * with bad wifi would have produced exactly that.
 *
 * Drawing it here instead removes ~200KB of CDN dependency, works with no
 * network at all, and lets the CSP drop its cdnjs allowance entirely.
 *
 * Form: horizontal bars. The measure is magnitude across 13 named categories
 * whose labels are too long to sit under vertical bars without rotating them.
 *
 * Colour: the bars are NOT one colour. Each is filled by the pattern's own
 * evidence tier, which is the same semantic system used everywhere else in
 * this product, so the chart answers a second question for free — how much of
 * the finding volume is provable rather than merely indicative. The three
 * fill steps come from the SAME CSS custom properties the tier badges use, read
 * at draw time rather than hardcoded here. That is not tidiness: the palette
 * has a light set and a dark set, and a hardcoded fill would leave the chart
 * painting light-mode greens onto a dark surface, where they fail contrast
 * against it. Reading the token means the chart follows the theme for free and
 * there is exactly one place a tier colour is defined.
 *
 * Both sets were checked with the palette validator rather than picked by eye
 * (lightness band, chroma floor, CVD separation, normal-vision floor, contrast
 * vs surface — all pass in both modes). The dark set’s worst adjacent tritan
 * separation sits in the 6–8 floor band, which is permitted ONLY alongside a
 * second encoding: every bar is direct-labelled with its value and its tier is
 * named in the tooltip and the legend, so identity never rests on colour.
 * ------------------------------------------------------------------------- */
const TIER_TOKEN = {
  provable:     "--tier-provable",
  corroborated: "--tier-corroborated",
  indicative:   "--tier-indicative",
  unstated:     "--tier-unstated",
};

function tierFill(tier) {
  const name = TIER_TOKEN[tier] || TIER_TOKEN.unstated;
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
  // A missing token would paint black-on-dark; fall back to the neutral.
  return value || "#8a8aa0";
}
const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  return node;
}

/* Tier per pattern code, from /coverage. Cached after the first load so the
 * chart can be redrawn after an audit without a second round trip. */
let TIER_BY_CODE = {};

function renderPatternChart(byPattern) {
  const svg = document.getElementById("patternChart");
  const legend = document.getElementById("chart-legend");
  svg.replaceChildren();
  legend.replaceChildren();

  const rows = [...(byPattern || [])].sort((a, b) => b.count - a.count);
  if (!rows.length) {
    svg.append(svgEl("text", {x: 8, y: 28, class: "chart-empty"}));
    svg.lastChild.textContent = "No violations recorded yet — run an audit above.";
    return;
  }

  const W = svg.clientWidth || 620;
  const rowH = Math.max(18, Math.min(30, Math.floor(300 / rows.length)));
  const barH = Math.min(14, rowH - 8);          // thin marks, 2px+ surface gap
  // The label column has to hold the longest code in the data, not an assumed
  // one: DPAF-* codes (the patterns beyond CCPA's 13) are twice the width of
  // "DP-01", and a fixed offset made them collide with the pattern name.
  const longestCode = rows.reduce((n, r) => Math.max(n, r.code.length), 0);
  const codeW = Math.ceil(longestCode * 6.6) + 8;
  const labelW = codeW + 108, valueW = 30, padR = 8;
  const plotW = Math.max(60, W - labelW - valueW - padR);
  const H = rows.length * rowH + 6;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);

  const max = Math.max(...rows.map(r => r.count), 1);

  rows.forEach((r, i) => {
    const y = i * rowH + 3;
    const tier = TIER_BY_CODE[r.code] || "unstated";
    const w = Math.max(2, Math.round((r.count / max) * plotW));

    const code = svgEl("text", {x: 0, y: y + barH - 1, class: "bar-code",
                                fill: tierFill(tier)});
    code.textContent = r.code;
    const name = svgEl("text", {x: codeW, y: y + barH - 1, class: "bar-label"});
    // Trim to the space actually available between the code and the bar, not
    // to a guessed character count — "Interface Interference" overflowed into
    // the plot area when the budget was hardcoded.
    const nameBudget = Math.max(6, Math.floor((labelW - codeW - 10) / 5.6));
    name.textContent = r.name.length > nameBudget
      ? r.name.slice(0, nameBudget - 1) + "…" : r.name;

    const bar = svgEl("rect", {x: labelW, y: y, width: w, height: barH,
                               rx: 4, fill: tierFill(tier)});
    const value = svgEl("text", {x: labelW + w + 6, y: y + barH - 1, class: "bar-value"});
    value.textContent = r.count;

    // Hit target spans the full row, not just the bar — a 2-unit bar is
    // otherwise 4px wide and effectively unhoverable.
    const hit = svgEl("rect", {x: 0, y: y - 2, width: W, height: rowH,
                               fill: "transparent"});
    hit.addEventListener("mousemove", (e) => showTip(e,
      `${r.code} · ${r.name}\n${r.count} finding${r.count === 1 ? "" : "s"} · ${tier}`));
    hit.addEventListener("mouseleave", hideTip);

    svg.append(code, name, bar, value, hit);
  });

  const present = [...new Set(rows.map(r => TIER_BY_CODE[r.code] || "unstated"))];
  ["provable", "corroborated", "indicative", "unstated"]
    .filter(t => present.includes(t))
    .forEach(t => {
      const item = document.createElement("span");
      const swatch = document.createElement("i");
      swatch.style.background = tierFill(t);
      item.append(swatch, document.createTextNode(t));
      legend.append(item);
    });
}

function showTip(evt, label) {
  const tip = document.getElementById("chart-tip");
  tip.textContent = label;          // textContent: pattern names are our own,
  tip.hidden = false;               // but the habit is the point
  tip.style.left = Math.min(evt.clientX + 14, window.innerWidth - 250) + "px";
  tip.style.top = (evt.clientY + 14) + "px";
}

function hideTip() {
  document.getElementById("chart-tip").hidden = true;
}

let JOBS = [];

async function loadDashboard(opts) {
  const summary = await authFetch("/dashboard/summary");
  // The LLM pass needs ANTHROPIC_API_KEY on the server. Without it the box
  // would be a switch wired to nothing, so it says so and cannot be ticked.
  if (summary.llm_available === false) {
    const llm = document.getElementById("use-llm");
    llm.checked = false;
    llm.disabled = true;
    document.getElementById("llm-note").textContent = "(not configured on this server)";
  }
  document.getElementById("kpi-audits").textContent = summary.total_audits;
  document.getElementById("kpi-violations").textContent = summary.total_violations;
  const top = [...summary.by_pattern].sort((a,b) => b.count - a.count)[0];
  document.getElementById("kpi-top").textContent = top ? `${top.code} — ${top.name}` : "—";

  renderPatternChart(summary.by_pattern);

  const jobs = await authFetch("/audits?limit=20");
  JOBS = jobs;
  document.getElementById("jobs-table").innerHTML = jobs.slice(0, 20).map(j => `
    <tr class="job-row" data-id="${Number(j.id)}" tabindex="0">
      <td>${escapeHtml(j.site_name || j.adapter_name)}</td>
      <td><span class="status ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span></td>
      <td>${Number(j.violations.length)}</td>
    </tr>`).join("");

  const keep = opts && opts.keepSelection && JOBS.find(j => j.id === selectedJobId);
  if (keep) showJob(keep);
  else if (jobs.length) showJob(jobs[0]);
  else clearJobPanel();
}

/** No audits in this workspace. Changing from the team key to an empty
 *  customer key in the same tab left the team's last audit (findings,
 *  review, PDF button) on screen under the customer's key. */
function clearJobPanel() {
  selectedJobId = null;
  baselineId = null;
  document.getElementById("violations-title").textContent = "No audits in this workspace yet";
  document.getElementById("violations-table").replaceChildren();
  document.getElementById("download-pdf").hidden = true;
  const disc = document.getElementById("discovery-report");
  disc.replaceChildren();
  disc.hidden = true;
  document.getElementById("job-trust").hidden = true;
}

/**
 * Save the selected audit's PDF. A link cannot send the X-API-Key header, so
 * the file is fetched here and handed to the browser from memory.
 */
async function downloadReport() {
  const btn = document.getElementById("download-pdf");
  if (selectedJobId === null) return;
  const opts = {headers: {}};
  const key = apiKey();
  if (key) opts.headers["X-API-Key"] = key;
  btn.disabled = true;
  try {
    const extra = baselineId ? `?baseline=${Number(baselineId)}` : "";
    const r = await fetch(`${API}/audits/${Number(selectedJobId)}/report.pdf${extra}`, opts);
    // 403 with a key is "not for your organisation", not a bad key: say so
    // without locking the dashboard (as authFetch does).
    if (r.status === 403 && key) throw new Error("This report is not available for your organisation.");
    if (r.status === 401 || r.status === 403) { setAuthState(key ? "bad" : "locked"); askForKey(); return; }
    if (!r.ok) throw new Error(`The report could not be made (${r.status}).`);
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `ulterior-audit-${Number(selectedJobId)}.pdf`;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) {
    const status = document.getElementById("run-status");
    status.textContent = err.message;
    status.classList.add("is-error");
  } finally {
    btn.disabled = false;
  }
}

/** Reports sent from the browser extension. Every field came from a page
 *  someone visited and then from an unauthenticated POST, so every one goes
 *  in as text. */
async function loadReports() {
  const card = document.getElementById("reports-card");
  let rows;
  try {
    rows = await authFetch("/extension-reports?limit=50");
  } catch (err) {
    if (err instanceof ForbiddenError) { card.hidden = true; return; }   // team-only panel
    throw err;
  }
  card.hidden = false;
  const tbody = document.getElementById("reports-table");
  tbody.replaceChildren();
  document.getElementById("reports-count").textContent =
    rows.length ? `${rows.length} most recent` : "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "cov-note";
    td.textContent = "No reports yet.";
    tr.append(td);
    tbody.append(tr);
    return;
  }
  for (const r of rows) {
    const tr = document.createElement("tr");
    const cells = [
      new Date(r.created_at + (String(r.created_at).endsWith("Z") ? "" : "Z")).toLocaleString(),
      r.site, r.page_type || "",
      (r.findings || []).map((f) => f.code).join(", "),
      (r.findings && r.findings[0]) ? r.findings[0].title : "",
    ];
    for (const c of cells) {
      const td = document.createElement("td");
      td.textContent = String(c);
      tr.append(td);
    }
    tbody.append(tr);
  }
}

function openJobFromRow(e) {
  const tr = e.target.closest("tr.job-row");
  if (!tr) return;
  const job = JOBS.find(j => j.id === Number(tr.dataset.id));
  if (job) {
    showJob(job);
    document.getElementById("violations-title").scrollIntoView({behavior: "smooth", block: "start"});
  }
}

async function loadCoverage() {
  // Classes, not style="...": these two attributes were the last inline styles
  // in the product, generated here rather than written in the HTML so they
  // survived the sweep that removed the rest. They break the same two ways --
  // a viewer whose CSP forbids inline styles drops them, and #666 is a
  // hardcoded light-mode grey that is nearly invisible on the dark surface.
  const cov = await authFetch("/coverage");
  cov.patterns.forEach(p => { TIER_BY_CODE[p.code] = p.tier; });
  const rows = cov.patterns.map(p => `
    <tr>
      <td><span class="badge">${escapeHtml(p.code)}</span> ${escapeHtml(p.name)}</td>
      <td><span class="tier tier-${escapeHtml(p.tier)}">${escapeHtml(p.tier)}</span></td>
      <td class="layer-tag">Layer ${escapeHtml(p.primary_layer)}</td>
      <td class="cov-note">${escapeHtml(p.coverage_note)}</td>
    </tr>`).join("");
  document.getElementById("coverage-body").innerHTML = `
    <p class="cov-intro">
      <strong>${Number(cov.implemented)} of ${Number(cov.total_patterns)}</strong> CCPA categories have a
      detector. Confidence is capped by evidence tier — provable ${Number(cov.tier_max_confidence.provable)*100}%,
      corroborated ${Number(cov.tier_max_confidence.corroborated)*100}%, indicative ${Number(cov.tier_max_confidence.indicative)*100}%.
    </p>
    <table><thead><tr><th>Pattern</th><th>Tier</th><th>Layer</th><th>What is and isn't detected</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

async function boot() {
  document.getElementById("run-status").textContent = "";
  // Coverage first: it populates the tier map the chart colours by.
  // allSettled, not all — one failing panel must never blank the others,
  // which is exactly what the old Chart.js dependency did when its CDN was
  // unreachable. Any real failure is still surfaced, once, in a banner.
  await loadCoverage().catch(showConnectionError);
  const results = await Promise.allSettled([loadAdapters(), loadDashboard(), loadReports()]);
  // A locked dashboard is shown by the key card and the gate, not by an
  // error banner: nothing is broken, it is waiting for a key.
  const failed = results.find(r => r.status === "rejected" && !(r.reason instanceof AuthError));
  if (failed) showConnectionError(failed.reason);
}

document.getElementById("run-audit").addEventListener("click", runAudit);
document.getElementById("run-urls").addEventListener("click", runUrlAudit);
document.getElementById("target-urls").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runUrlAudit();
});
function connect() {
  // A new key is a new workspace: the old one's running poll must stop
  // rather than keep asking about a job this key may not be able to see.
  endAuditRun();
  document.querySelectorAll(".conn-error").forEach(el => el.remove());
  const status = document.getElementById("run-status");
  status.classList.remove("is-error");
  const btn = document.getElementById("reload");
  btn.disabled = true;
  document.getElementById("key-state").textContent = "Checking\u2026";
  boot().catch(showConnectionError).finally(() => { btn.disabled = false; });
}
document.getElementById("reload").addEventListener("click", connect);
document.getElementById("api-key").addEventListener("keydown", (e) => {
  if (e.key === "Enter") connect();
});
document.getElementById("jobs-table").addEventListener("click", openJobFromRow);
document.getElementById("download-pdf").addEventListener("click", downloadReport);
document.getElementById("verify-evidence").addEventListener("click", verifyEvidence);
document.getElementById("compare-run").addEventListener("click", compareWithBaseline);
document.getElementById("cancel-audit").addEventListener("click", cancelRunningAudit);
document.getElementById("review-save").addEventListener("click", saveReview);
document.getElementById("jobs-table").addEventListener("keydown", (e) => {
  if (e.key === "Enter") openJobFromRow(e);
});
document.querySelectorAll("[data-try]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const box = document.getElementById("target-urls");
    box.value = normaliseUrl(chip.dataset.try);
    document.getElementById("auto-discover").checked = true;
    if (chip.dataset.try.startsWith("/")) {
      fillPermission("Ulterior team", "Ulterior practice store", "owner");
    } else {
      fillPermission("Public test site", new URL(box.value).hostname, "public_test_site");
    }
    box.focus();
  });
});

function showConnectionError(err) {
  const where = API || location.origin;
  const banner = document.createElement("div");
  // The .conn-error class already carries all of this from the stylesheet.
  // Setting it again via cssText was worse than redundant: those were
  // hardcoded light-mode colours, so on the dark surface the ONE banner whose
  // whole job is to be seen when the app cannot reach its API rendered as
  // near-black text on near-black. And a viewer whose CSP forbids inline
  // styles dropped the declaration entirely.
  banner.className = "conn-error";
  // One banner, the latest one. Pressing Connect five times used to stack
  // five identical banners above the page.
  document.querySelectorAll(".conn-error").forEach(el => el.remove());
  // textContent, never innerHTML: the message can carry server-supplied text.
  banner.textContent = `Could not load from ${where}. ${err && err.message ? err.message : ""}`;
  document.body.insertBefore(banner, document.body.firstChild);
}

boot().catch(showConnectionError);
