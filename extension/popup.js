/**
 * Ulterior extension -- toolbar popup.
 *
 * SECURITY: everything shown here came from a page the person visited, and a
 * page being flagged has a motive to put markup in its button labels. Every
 * value goes in through textContent or createElement; nothing here assigns
 * HTML strings. The popup runs with the extension's privileges, so an XSS
 * here would be worse than one on the shop's own page.
 *
 * NETWORK: sendReport() below is the only network call in the extension. It
 * runs only when the person has (1) switched reporting on, which asks the
 * browser for a separate permission, (2) pressed "Report this page", (3) read
 * the exact payload, and (4) pressed Send.
 */
(function () {
  "use strict";
  const C = globalThis.UlteriorCore;
  const ext = (globalThis.browser && globalThis.browser.runtime) ? globalThis.browser : globalThis.chrome;
  const $ = (id) => document.getElementById(id);
  const DEFAULTS = { overlay: true, history: true, priceCheck: true, report: false, community: false };
  const REPORT_PERMISSION = { origins: [C.REPORT_ORIGIN + "/*"] };
  const IS_FIREFOX = !!(globalThis.browser && globalThis.browser.runtime && globalThis.browser.runtime.getBrowserInfo);

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  let tabId = null;
  let current = null;
  let settings = Object.assign({}, DEFAULTS);

  async function resolveTab() {
    // ?tab=<id> lets the test suite open the popup for a specific tab.
    const q = new URLSearchParams(location.search).get("tab");
    if (q && /^\d+$/.test(q)) return { id: Number(q), url: "" };
    const [tab] = await ext.tabs.query({ active: true, currentWindow: true });
    return tab || null;
  }

  function renderFindings(findings) {
    const host = $("findings");
    host.replaceChildren();
    for (const f of findings) {
      const card = el("div", "f");
      const meta = el("div", "meta");
      meta.append(el("span", "code", f.code), el("span", "tier " + f.tier, f.tier),
                  el("span", "pname", C.PATTERN_NAMES[f.code] || ""));
      card.append(meta, el("p", "t", f.title), el("p", "d", f.detail || ""));
      host.append(card);
    }
  }

  function renderJourney(j) {
    const box = $("journey");
    if (!j) { box.hidden = true; return; }
    box.hidden = false;
    const kv = $("journey-kv");
    kv.replaceChildren();
    const rows = [
      ["You picked", `${j.start} at ${j.at}`],
      ["Taken from", j.source],
      [j.qty > 1 ? `Expected for ${j.qty}` : "Expected", j.expected],
      ["Total here", j.total],
    ];
    if (j.fees) rows.push(["Fees on this step", j.fees]);
    for (const [k, v] of rows) kv.append(el("dt", "", k), el("dd", "", v));
  }

  let period = 90;

  function historyText(h) {
    if (!h || h.count < 1) return "";
    if (h.count === 1) return `First time you've opened this product. Ulterior will remember today's ${h.current}.`;
    if (h.currentValue > h.lowestValue) return `You saw it for ${h.lowest} on ${h.lowestOn}. Seen ${h.count} times since ${h.since}.`;
    return `The lowest you've seen since ${h.since} (${h.count} visits; highest ${h.highest}).`;
  }

  /** The price check: verdict, chart with periods, the real-discount check,
   *  a plain list of the same prices, and the watch form. */
  function renderPrice(results) {
    const box = $("price");
    const pr = results.price;
    if (!pr) { box.hidden = true; return; }
    box.hidden = false;
    const v = $("verdict");
    v.textContent = pr.verdict.label;
    v.className = "verdict " + pr.verdict.kind;
    $("verdict-detail").textContent = `${C.formatINR(pr.current)} now. ${pr.verdict.detail}`;
    $("history-text").textContent = historyText(results.history);
    const disc = $("discount");
    disc.hidden = !pr.discount;
    if (pr.discount) { disc.textContent = pr.discount.text; disc.className = "disc " + pr.discount.kind; }
    const draw = () => globalThis.UlteriorChart.draw($("chart"), pr.series, { days: period, target: pr.target, tooltip: $("tip") });
    for (const b of $("periods").querySelectorAll("button")) {
      b.setAttribute("aria-pressed", String(Number(b.dataset.days) === period));
      b.onclick = () => { period = Number(b.dataset.days); renderPrice(results); };
    }
    draw();
    const list = $("pricelist");
    list.replaceChildren();
    for (const x of pr.series.slice().reverse().slice(0, 60)) {
      list.append(el("li", "", `${new Date(x.t).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}: ` +
        `${C.formatINR(x.p)} (${x.src === "community" ? "community" : "you"})`));
    }
    $("watch-form").hidden = !!pr.target;
    $("watching").hidden = !pr.target;
    if (pr.target) $("watching-text").textContent = `Watching - alert at ${C.formatINR(pr.target)} or less.`;
    else if (!$("target").value) $("target").value = String(C.suggestTarget(pr.current));
    $("price-source").textContent = pr.community
      ? `Includes community prices from ${pr.community} day${pr.community === 1 ? "" : "s"}.`
      : "Prices you have seen, kept on this device.";
  }

  function renderCompare(results) {
    const box = $("compare");
    if (!results.productTitle) { box.hidden = true; return; }
    box.hidden = false;
    const ul = $("elsewhere");
    ul.replaceChildren();
    const seen = (results.history && results.history.elsewhere) || [];
    const here = results.history ? results.history.currentValue : null;
    for (const s of seen) {
      const li = el("li");
      const cheaper = here && s.priceValue < here ? " - cheaper" : "";
      li.append(el("span", "", `You saw it on ${s.site} (${s.on})`), el("b", "", s.price + cheaper));
      ul.append(li);
    }
    ul.hidden = seen.length === 0;
    const links = $("compare-links");
    links.replaceChildren();
    for (const l of C.compareLinks(results.productTitle, results.site)) {
      const a = el("a", "", l.name);
      a.href = l.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      links.append(a);
    }
  }

  function renderNotes(notes) {
    const ul = $("notes");
    ul.replaceChildren();
    for (const n of notes || []) ul.append(el("li", n.kind === "ok" ? "ok" : "", n.text));
  }

  function renderCoverage() {
    const ul = $("coverage");
    ul.replaceChildren();
    for (const [code, level, what] of C.EXTENSION_COVERAGE) {
      const li = el("li");
      li.append(el("b", "", code), el("span", "lvl " + level, level), el("span", "", what));
      ul.append(li);
    }
  }

  function render(results) {
    current = results;
    $("report-preview").hidden = true;
    if (!results) {
      $("results").hidden = true;
      $("not-scanned").hidden = false;
      return;
    }
    $("not-scanned").hidden = true;
    $("results").hidden = false;
    $("site").textContent = results.site || "";
    const n = results.findings.length;
    const summary = $("summary");
    summary.textContent = n === 0 ? "Nothing to flag on this page."
      : n === 1 ? "1 thing to check on this page" : `${n} things to check on this page`;
    summary.classList.toggle("clean", n === 0);
    renderFindings(results.findings);
    renderJourney(results.journey);
    renderPrice(results);
    renderCompare(results);
    renderNotes(results.notes);
    $("report").hidden = !(settings.report && n > 0);
  }

  async function load() {
    const tab = await resolveTab();
    if (!tab) { render(null); return; }
    tabId = tab.id;
    try {
      if (tab.url) $("site").textContent = C.siteOf(new URL(tab.url).hostname);
    } catch (e) { /* browser's own page */ }
    if (tab.url && !/^https?:/.test(tab.url)) {
      $("not-scanned-text").textContent = "Ulterior only reads web pages.";
      $("scan").hidden = true;
    }
    const key = "tab:" + tabId;
    const got = await ext.storage.session.get(key);
    render(got[key] || null);
    ext.storage.onChanged.addListener((changes, area) => {
      if (area === "session" && changes[key]) render(changes[key].newValue || null);
    });
  }

  async function saveSettings() {
    await ext.storage.local.set({ settings });
  }

  async function loadSettings() {
    const got = await ext.storage.local.get("settings");
    settings = Object.assign({}, DEFAULTS, got.settings || {});
    // Reporting needs the browser permission too; if it was removed in the
    // browser's settings, the switch follows.
    if (settings.report || settings.community) {
      let has = false;
      try { has = await ext.permissions.contains(REPORT_PERMISSION); } catch (e) { has = false; }
      settings.report = settings.report && has;
      settings.community = settings.community && has;
    }
    $("opt-overlay").checked = !!settings.overlay;
    $("opt-history").checked = !!settings.history;
    $("opt-pricecheck").checked = !!settings.priceCheck;
    $("opt-report").checked = !!settings.report;
    $("opt-community").checked = !!settings.community;
    $("opt-overlay").addEventListener("change", () => { settings.overlay = $("opt-overlay").checked; saveSettings(); });
    $("opt-history").addEventListener("change", () => { settings.history = $("opt-history").checked; saveSettings(); });
    $("opt-pricecheck").addEventListener("change", () => { settings.priceCheck = $("opt-pricecheck").checked; saveSettings(); });
    $("opt-report").addEventListener("change", () => toggleNetworked("report", $("opt-report"), ["websiteContent"]));
    $("opt-community").addEventListener("change", () => toggleNetworked("community", $("opt-community"), ["browsingActivity"]));
  }

  /**
   * Reporting and community prices are the two things that talk to the
   * Ulterior server. Each is off until switched on, and switching either on
   * asks the browser for permission to reach that one address. The
   * permission is given back when both are off again.
   */
  async function toggleNetworked(name, box, firefoxData) {
    if (box.checked) {
      const ask = IS_FIREFOX ? Object.assign({ data_collection: firefoxData }, REPORT_PERMISSION) : REPORT_PERMISSION;
      let granted = false;
      try { granted = await ext.permissions.request(ask); } catch (e) { granted = false; }
      box.checked = granted;
      settings[name] = granted;
    } else {
      settings[name] = false;
      if (!settings.report && !settings.community) {
        try { await ext.permissions.remove(REPORT_PERMISSION); } catch (e) { /* already gone */ }
      }
    }
    await saveSettings();
    if (current) render(current);
  }

  async function sendReport(payload) {
    const r = await fetch(C.REPORT_ORIGIN + C.REPORT_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "omit",
    });
    if (!r.ok) throw new Error(`The server answered ${r.status}.`);
    return r.json();
  }

  $("scan").addEventListener("click", async () => {
    const btn = $("scan");
    btn.disabled = true;
    btn.textContent = "Scanning…";
    const r = await ext.runtime.sendMessage({ type: "SCAN_TAB", tabId });
    if (!r || !r.ok) {
      btn.disabled = false;
      btn.textContent = "Scan this page";
      $("not-scanned-text").textContent = "The browser did not allow a scan of this page.";
    }
  });

  $("rescan").addEventListener("click", () => {
    ext.tabs.sendMessage(tabId, { type: "RESCAN" }).catch(() => {});
  });

  async function copy(text, button, done) {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = done;
    } catch (e) {
      button.textContent = "Copy failed";
    }
  }

  $("copy").addEventListener("click", () => {
    if (!current) return;
    copy(C.buildReport(current.site, current.findings, new Date(current.scannedAt || Date.now())), $("copy"), "Copied ✓");
  });

  $("diagnostics").addEventListener("click", () => {
    copy(C.buildDiagnostics(current), $("diagnostics"), "Diagnostics copied ✓");
  });

  $("report").addEventListener("click", () => {
    if (!current) return;
    $("report-json").textContent = JSON.stringify(C.buildReportPayload(current), null, 2);
    $("report-preview").hidden = false;
    $("report-status").textContent = "";
  });
  $("report-cancel").addEventListener("click", () => { $("report-preview").hidden = true; });
  $("report-send").addEventListener("click", async () => {
    if (!current || !settings.report) return;
    const btn = $("report-send");
    btn.disabled = true;
    try {
      const r = await sendReport(C.buildReportPayload(current));
      $("report-status").textContent = `Sent. Thank you - report #${Number(r.id)} will be reviewed by a person.`;
      $("report-preview").hidden = true;
    } catch (e) {
      $("report-status").textContent = `Not sent: ${e.message}`;
    } finally {
      btn.disabled = false;
    }
  });

  $("watch").addEventListener("click", async () => {
    const pr = current && current.price;
    const t = parseFloat($("target").value);
    if (!pr || !(t > 0)) return;
    await globalThis.UlteriorWatch.add({ key: pr.key, title: pr.title, url: pr.url, price: pr.current, target: t });
    pr.target = t;
    renderPrice(current);
  });
  $("unwatch").addEventListener("click", async () => {
    const pr = current && current.price;
    if (!pr) return;
    await globalThis.UlteriorWatch.remove(pr.key);
    pr.target = null;
    renderPrice(current);
  });
  $("open-watchlist").addEventListener("click", () => {
    ext.tabs.create({ url: ext.runtime.getURL("watchlist.html") });
  });

  $("clear-history").addEventListener("click", async () => {
    await globalThis.UlteriorHistory.clearAll();
    $("clear-history").textContent = "Price history cleared";
    $("history-text").textContent = "";
  });

  renderCoverage();
  loadSettings().then(load);
})();
