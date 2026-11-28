/**
 * Ulterior extension -- runs the checks on the page you are looking at.
 *
 * Auto-runs only on the shopping and travel sites listed in manifest.json.
 * Anywhere else nothing runs until you press "Scan this page" in the popup,
 * which injects these same files into that one tab. A banking site, a mail
 * client or a hospital portal is never read.
 */
(function (root) {
  "use strict";
  if (root.__ulteriorLoaded) {           // injected twice (auto + "Scan this page")
    if (root.__ulteriorScanNow) root.__ulteriorScanNow();
    return;
  }
  root.__ulteriorLoaded = true;

  const C = root.UlteriorCore, D = root.UlteriorDetect, J = root.UlteriorJourney,
        H = root.UlteriorHistory, O = root.UlteriorOverlay, P = root.UlteriorPlatform,
        W = root.UlteriorWatch;
  const ext = P.ext;
  const SITE = C.siteOf(location.hostname);

  const state = {
    firstChecked: new WeakMap(),         // what each choice looked like when we first saw it
    touched: new WeakSet(),              // choices the person changed themselves
    timersSampledFor: null,              // the route whose countdowns have been sampled
    timerFindings: [],
    route: null,                         // the route the timer findings belong to
    settings: { overlay: true, history: true, priceCheck: true },
    hidden: false,
    lastResults: null,
    dialogsShown: new Map(),             // signature -> element, pop-ups on screen at the last scan
    dialogClicks: new Map(),             // signature -> time of the person's last click inside it
    nagFinding: null,
  };

  // A choice the person changed is theirs, not the shop's.
  document.addEventListener("change", (e) => {
    if (e.isTrusted && e.target) state.touched.add(e.target);
  }, true);
  document.addEventListener("click", (e) => {
    if (!e.isTrusted || !e.target || !e.target.closest) return;
    const c = e.target.closest("[role=checkbox], [role=radio], [role=switch], label");
    if (c) {
      state.touched.add(c);
      if (c.tagName === "LABEL" && c.control) state.touched.add(c.control);
    }
    // Only a DISMISSAL counts as closing a pop-up: the ×, "Close", "No
    // thanks", "Maybe later". Picking a size or pressing "Apply" inside a
    // size chart or address picker is using it, not refusing it.
    const ctl = e.target.closest("button, a, [role=button], [aria-label]");
    const words = ctl ? ((ctl.getAttribute("aria-label") || "") + " " + (ctl.innerText || ctl.textContent || "")).trim() : "";
    const dismissal = ctl && (/^\s*([x×✕✖✗]|close|dismiss)\s*$/i.test((ctl.innerText || ctl.textContent || "").trim())
      || /\b(close|dismiss)\b/i.test(ctl.getAttribute("aria-label") || "") || C.DECLINE_RE.test(C.normalizeQuotes(words)));
    for (const [sig, el] of state.dialogsShown) {
      if (dismissal && el.contains(e.target)) state.dialogClicks.set(sig, Date.now());
    }
  }, true);

  async function loadSettings() {
    try {
      const r = await ext.storage.local.get("settings");
      Object.assign(state.settings, r.settings || {});
      state.hidden = !!(await P.Session.get("hide:" + SITE));
    } catch (e) { /* defaults */ }
  }

  // ------------------------------------------------------------ timers
  /**
   * A countdown is only evidence if it is LIVE (it ticks down while we
   * watch) and it RESTARTS (after a reload it shows more time than was left
   * before). A static "2h 15m" is a flight duration; a timer that resumes
   * where it left off is an honest deadline. Either way: no finding.
   */
  // Path, query AND hash: query-routed products (?product_id=42) and
  // hash-routed apps change only those.
  const routeKey = () => location.pathname + location.search + location.hash;
  let timerGeneration = 0, timerBusy = false;

  async function checkTimers() {
    if (timerBusy) return;
    const route = routeKey();
    const found = D.findCountdowns();
    // Nothing yet: a single-page app may still be rendering this route. The
    // next scan (triggered by the page changing) looks again.
    if (!found.length) return;
    state.timersSampledFor = route;
    timerBusy = true;
    const gen = ++timerGeneration;
    try { await sampleTimers(found, route, gen); } finally { timerBusy = false; }
  }

  async function sampleTimers(found, route, gen) {
    // Samples are local to this run (not shared state), so a quick Back and
    // forward cannot mix readings from two routes.
    const samples = new Map();
    const t0 = Date.now();
    // Timers are matched by what they SAY around them, not by their position
    // in the page: if one timer appears or disappears in between, position
    // pairs two different timers.
    const idOf = (f) => (f.ctx || "").replace(/\d+/g, "#").replace(/\s+/g, " ").trim().slice(0, 60);
    found.forEach((f) => samples.set(idOf(f), { first: f.sec, at: t0, ctx: f.ctx }));
    await new Promise((r) => setTimeout(r, 3500));
    if (routeKey() !== route || gen !== timerGeneration) return;   // moved on: stale readings
    const again = D.findCountdowns();
    const findings = [];
    for (const cur of again) {
      const id = idOf(cur);
      const before = samples.get(id);
      if (!before) continue;
      const elapsed = (Date.now() - before.at) / 1000;
      const dropped = before.first - cur.sec;
      if (dropped < 1 || dropped > elapsed + 2) continue;          // not a live countdown
      const key = `timer:${SITE}${route}:${id}`.slice(0, 300);
      const prev = await P.Session.get(key);
      const now = Date.now();
      await P.Session.set(key, { sec: cur.sec, at: now });
      if (!prev) continue;
      const gap = (now - prev.at) / 1000;
      const expected = prev.sec - gap;
      if (gap >= 5 && cur.sec > expected + 5) {
        const tm = (ms) => new Date(ms).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        findings.push({
          code: "DP-01", tier: "corroborated",
          title: "The countdown started over when the page was opened again",
          detail: `It showed ${C.fmtClock(prev.sec)} at ${tm(prev.at)}. ${Math.round(gap)} seconds later it ` +
            `showed ${C.fmtClock(cur.sec)} instead of about ${C.fmtClock(expected)}. A deadline that resets ` +
            "is not measuring a real deadline.",
          evidence: {
            countdown_text: C.redact(cur.ctx),
            earlier_reading: `${C.fmtClock(prev.sec)} at ${tm(prev.at)}`,
            later_reading: `${C.fmtClock(cur.sec)} at ${tm(now)}`,
            expected_if_real: `about ${C.fmtClock(expected)}`,
          },
        });
      }
    }
    if (findings.length && routeKey() === route && gen === timerGeneration) {
      state.timerFindings = findings;
      scan();
    }
  }

  /** Single-page apps change the route with history.pushState, so this
   *  script is not loaded again. Timer readings and findings belong to the
   *  page they were taken on: on a new route they are dropped and the new
   *  page's timers are checked. */
  function onRouteChange() {
    const route = routeKey();
    if (route !== state.route) {
      state.route = route;
      state.timerFindings = [];
      state.timersSampledFor = null;
      timerGeneration++;                 // any run for the old route is now stale
    }
    // Sample this route's countdowns once they exist; a countdown that mounts
    // after the route changed is picked up by a later scan.
    if (state.timersSampledFor !== route) checkTimers();
  }

  // ----------------------------------------------------------- nagging
  /**
   * DP-10: a pop-up you closed that comes back -- on this page or the next
   * one on the same site. "Closed" means it disappeared within a few seconds
   * of you clicking inside it; the record lives in session memory for the
   * site, so moving to another page does not reset the count.
   */
  async function trackDialogs() {
    const now = Date.now();
    const current = new Map();
    // Only pop-ups that ASK for something (sign in, the app, an offer, a
    // newsletter, notifications...). A size chart or a filter drawer the
    // shopper opened twice is not nagging.
    for (const d of D.dialogs()) {
      if (C.DIALOG_HINT_RE.test(D.fast(d.el).slice(0, 900))) current.set(d.sig, d);
    }
    const key = "nag:" + SITE;
    const rec = (await P.Session.get(key)) || {};
    let changed = false;

    for (const [sig, d] of current) {
      if (state.dialogsShown.has(sig)) continue;                     // still the same appearance
      const r = rec[sig] || { shown: 0, closed: 0, again: 0, text: d.text };
      r.shown += 1;
      if (r.closed > 0) r.again += 1;
      r.text = d.text;
      rec[sig] = r;
      changed = true;
    }
    for (const [sig] of state.dialogsShown) {
      if (current.has(sig)) continue;
      const clicked = state.dialogClicks.get(sig);
      if (clicked && now - clicked < 5000 && rec[sig]) {
        rec[sig].closed += 1;
        changed = true;
      }
    }
    state.dialogsShown = new Map([...current].map(([sig, d]) => [sig, d.el]));
    // Keep the record small: the twenty most-shown pop-ups.
    const sigs = Object.keys(rec).sort((a, b) => rec[b].shown - rec[a].shown).slice(0, 20);
    const small = {};
    sigs.forEach((s) => { small[s] = rec[s]; });
    if (changed) await P.Session.set(key, small);

    const worst = sigs.map((s) => small[s]).filter((r) => r.again > 0).sort((a, b) => b.again - a.again)[0];
    state.nagFinding = worst ? {
      code: "DP-10", tier: "corroborated",
      title: "A pop-up you closed keeps coming back",
      detail: `"${C.clip(C.redact(worst.text), 80)}" has been shown ${worst.shown} times on this site this ` +
        `session, ${worst.again} of them after you closed it.`,
      evidence: { pop_up_text: C.clip(C.redact(worst.text), 100), times_shown: String(worst.shown),
                  times_you_closed_it: String(worst.closed), shown_again_after_closing: String(worst.again) },
    } : null;
  }

  // -------------------------------------------------------------- scan
  let running = false, again = false, lastDuration = 0, lastSignature = "";

  async function scan() {
    onRouteChange();
    if (running) { again = true; return; }
    running = true;
    const began = performance.now();
    try {
      const findings = [], notes = [];
      const total = D.findTotal();
      const isCheckout = D.isCheckoutPage(total);
      const fees = isCheckout ? D.findFees() : [];
      const buyCount = isCheckout ? 0 : D.buyButtons().length;

      // A product page: one or two buy buttons and a price that stands out.
      // Three or more is a listing, where the price is whatever card you pick.
      let product = null;
      if (!isCheckout && buyCount >= 1 && buyCount <= 2 && !D.looksLikeCartOrCheckout()) product = D.prominentPrice();
      root.__ulteriorLastScan = {
        isCheckout, productPrice: product ? product.value : null,
        productDisclosed: product && product.el ? C.disclosesCharges(D.textOf(product.el.parentElement || product.el)) : false,
      };

      findings.push(...D.preselectedFindings(state));
      findings.push(...D.trickWordingFindings(state));
      findings.push(...D.confirmShamingFindings());
      findings.push(...D.interferenceFindings());
      findings.push(...D.forcedActionFindings());
      findings.push(...D.subscriptionFindings());
      findings.push(...D.rogueFindings());
      const ads = D.sponsoredFindings();
      findings.push(...ads.findings);
      notes.push(...ads.notes);
      await trackDialogs();
      if (state.nagFinding) findings.push(state.nagFinding);
      findings.push(...state.timerFindings);

      let journey = null;
      if (isCheckout) {
        const addOns = findings.filter((f) => f.code === "DP-02").reduce((t, f) => t + (f.amountValue || 0), 0);
        const j = await J.evaluate({ isCheckout, total, fees, addOns, quantities: D.quantities(total) });
        findings.push(...j.findings);
        notes.push(...j.notes);
        journey = j.summary;
      } else if (product) {
        await J.noteProductPage(product.value, root.__ulteriorLastScan.productDisclosed);
      }

      let history = null, productTitle = null, price = null;
      if (product) {
        productTitle = C.clip(J.productName(), 120);
        const key = C.productKey(location.href, (document.querySelector("link[rel=canonical]") || {}).href);
        if (state.settings.history) {
          const claim = D.stockClaim();
          if (key) {
            history = await H.record(key, product.value, {
              fp: C.fingerprint(productTitle), stockClaim: claim ? claim.n : null, stockText: claim ? claim.text : null,
            }).catch(() => null);
          }
          if (history && history.stockFinding) findings.push(history.stockFinding);
        }
        if (key) price = await priceCheck(key, product, history, productTitle);
      }

      notes.push(...D.urgencyNotes());

      const results = {
        site: SITE, scannedAt: Date.now(), version: C.VERSION,
        scanMs: Math.round(performance.now() - began),
        page: isCheckout ? "checkout" : product ? "product" : "other",
        findings, notes, journey, history, productTitle,
        price: price ? Object.assign({}, price, { series: price.series.slice(-200) }) : null,
        total: total && isCheckout ? { value: C.formatINR(total.value), label: total.label } : null,
        fees: fees.map((f) => ({ label: f.label, value: C.formatINR(f.value) })),
        debug: {
          "Buy buttons": buyCount,
          "Product price read from": product ? product.how : "none",
          "Pop-ups on screen": state.dialogsShown.size,
          "Language": document.documentElement.lang || "not declared",
        },
      };
      state.lastResults = results;
      // Most rescans see the same page. Only tell the toolbar and redraw the
      // alert when something actually changed.
      // Which page and which product are part of "what is on screen": on a
      // single-page shop two products with no findings and one reading each
      // gave the same signature, and the first product's price and watch box
      // stayed up while the second was shown.
      const signature = JSON.stringify([location.pathname + location.search + location.hash,
                                        productTitle, findings, notes, journey, history && history.count,
                                        history && history.elsewhere, state.settings, state.hidden,
                                        price && [price.key, price.current, price.verdict, price.target,
                                                  price.community, price.series.length]]);
      if (signature !== lastSignature) {
        lastSignature = signature;
        P.send({ type: "RESULTS", results });
        if (state.settings.overlay && !state.hidden) {
          O.render(findings, SITE, {
            onHide: hideHere,
            price: state.settings.priceCheck ? price : null,
            onWatch: (target) => watchThis(price, target),
            onUnwatch: () => price && W.remove(price.key).then(rescanNow),
          });
        } else {
          O.remove();
        }
      }
    } catch (e) {
      // A detector must never break the shop's page.
      if (root.console && console.debug) console.debug("Ulterior scan error", e);
    } finally {
      lastDuration = performance.now() - began;
      running = false;
      if (again) { again = false; setTimeout(scan, 300); }
    }
  }

  // ---------------------------------------------------------- prices
  /**
   * The price check for a product page: your own readings, merged with
   * community daily prices when you switched those on (the background asks
   * the server; this page never does), a verdict, the real-discount check,
   * and the watchlist -- including a notification when a watched product is
   * at or under your target.
   */
  async function priceCheck(key, product, history, title) {
    const own = history && history.series ? history.series : [{ p: product.value, t: Date.now(), src: "you" }];
    let community = null;
    const got = await P.send({ type: "COMMUNITY_GET", key });
    if (got && got.ok && Array.isArray(got.series)) community = got.series;
    P.send({ type: "COMMUNITY_PUT", key, price: product.value });

    const series = C.mergeSeries(own, community);
    let watched = await W.get(key);
    if (watched) {
      const s = await W.seen(key, product.value, "you");
      watched = s.item;
      if (s.hit) {
        P.send({ type: "NOTIFY", key, title: watched.title, price: product.value, target: watched.target, url: watched.url });
      }
    }
    return {
      key, current: product.value, title, url: C.cleanProductUrl(location.href),
      verdict: C.dealVerdict(product.value, series),
      discount: C.discountCheck(product.mrp, product.value, series),
      series, target: watched ? watched.target : null,
      community: community ? community.length : 0,
      mrp: product.mrp || null,
    };
  }

  async function watchThis(price, target) {
    if (!price) return;
    await W.add({ key: price.key, title: price.title, url: price.url, price: price.current, target });
    rescanNow();
  }

  function rescanNow() { lastSignature = ""; scan(); }

  function hideHere() {
    state.hidden = true;
    P.Session.set("hide:" + SITE, true);
  }

  // Throttled, not debounced: a page with a ticking countdown mutates every
  // second, and a debounce would never get its quiet moment to run.
  // The gap grows with how long a scan takes, so a huge page is never
  // scanned more than about 5% of the time, and a background tab not at all.
  let scheduled = null;
  function schedule() {
    if (scheduled) return;
    const gap = Math.max(1500, lastDuration * 20);
    scheduled = setTimeout(() => {
      scheduled = null;
      if (document.hidden) { document.addEventListener("visibilitychange", schedule, { once: true }); return; }
      scan();
    }, gap);
  }

  root.__ulteriorScanNow = () => { lastSignature = ""; return scan(); };

  ext.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg && msg.type === "RESCAN") {
      state.hidden = false;
      lastSignature = "";
      scan().then(() => reply({ ok: true }));
      return true;
    }
    return false;
  });

  ext.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.settings) {
      Object.assign(state.settings, changes.settings.newValue || {});
      schedule();
    }
    // Watched from the popup or removed on the watchlist page: redraw.
    if (area === "local" && Object.keys(changes).some((k) => k.startsWith("watch:"))) {
      lastSignature = "";
      schedule();
    }
  });

  (async function start() {
    await loadSettings();
    await scan();
    // Attributes too: most pop-ups appear by flipping hidden/class/style, not
    // by adding nodes.
    new MutationObserver(schedule).observe(document.documentElement, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ["hidden", "class", "style", "open", "aria-hidden", "aria-checked"],
    });
    setTimeout(scan, 1500);              // single-page apps render after "idle"
    checkTimers();
  })();
})(globalThis);
