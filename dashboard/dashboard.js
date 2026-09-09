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

  if (r.status === 401 || r.status === 403) {
    throw new Error("Unauthorised \u2014 enter a valid API key above.");
  }
  if (r.status === 429) {
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
function endAuditRun() {
  clearInterval(pollTimer);
  pollTimer = null;
  ["run-audit", "run-urls"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
}

async function startAudit(payload, btn) {
  // Whatever was running is over: stop its timer and give its button back
  // before this run takes over.
  endAuditRun();
  btn.disabled = true;
  const status = document.getElementById("run-status");
  status.textContent = "Running…";

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
    status.textContent = "";
    showConnectionError(err);
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

  pollTimer = setInterval(async () => {
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

    status.textContent = `Running\u2026 ${secs}s`;
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

    if (j.status === "done" || j.status === "failed") {
      endAuditRun();
      // Name the failure. error_message carries the exception type and job id;
      // the server withholds the traceback on purpose (it can contain a DB
      // password) but the type is safe and usually identifies the cause.
      status.textContent = j.status === "done"
        ? `Done \u2713 (${secs}s)`
        : (j.error_message || "Failed \u2717 \u2014 see the terminal running the server");
      status.classList.toggle("is-error", j.status !== "done");
      renderDiscovery(j.discovery);
      renderViolations(j.violations);
      loadDashboard();
    }
  }, 1000);
}

function runAudit() {
  return startAudit(
    {adapter_name: document.getElementById("adapter-select").value},
    document.getElementById("run-audit"));
}

function runUrlAudit() {
  const raw = document.getElementById("target-urls").value;
  const urls = raw.split(",").map(u => u.trim()).filter(Boolean);
  if (!urls.length) {
    showConnectionError(new Error("Enter at least one URL to audit."));
    return;
  }
  return startAudit({
    target_urls: urls,
    auto_discover: document.getElementById("auto-discover").checked,
  }, document.getElementById("run-urls"));
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

async function loadDashboard() {
  const summary = await authFetch("/dashboard/summary");
  document.getElementById("kpi-audits").textContent = summary.total_audits;
  document.getElementById("kpi-violations").textContent = summary.total_violations;
  const top = [...summary.by_pattern].sort((a,b) => b.count - a.count)[0];
  document.getElementById("kpi-top").textContent = top ? `${top.code} — ${top.name}` : "—";

  renderPatternChart(summary.by_pattern);

  const jobs = await authFetch("/audits");
  document.getElementById("jobs-table").innerHTML = jobs.slice(0, 10).map(j => `
    <tr>
      <td>${escapeHtml(j.site_name || j.adapter_name)}</td>
      <td><span class="status ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span></td>
      <td>${Number(j.violations.length)}</td>
    </tr>`).join("");

  if (jobs.length && jobs[0].violations.length) renderViolations(jobs[0].violations);
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
  const results = await Promise.allSettled([loadAdapters(), loadDashboard()]);
  const failed = results.find(r => r.status === "rejected");
  if (failed) showConnectionError(failed.reason);
}

document.getElementById("run-audit").addEventListener("click", runAudit);
document.getElementById("run-urls").addEventListener("click", runUrlAudit);
document.getElementById("target-urls").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runUrlAudit();
});
document.getElementById("reload").addEventListener("click", () => {
  document.querySelectorAll(".conn-error").forEach(el => el.remove());
  boot().catch(showConnectionError);
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
  // textContent, never innerHTML: the message can carry server-supplied text.
  banner.textContent = `Could not load from ${where}. ${err && err.message ? err.message : ""}`;
  document.body.insertBefore(banner, document.body.firstChild);
}

boot().catch(showConnectionError);
