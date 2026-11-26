/**
 * Ulterior extension -- your own price history and price comparison, on
 * your own device.
 *
 * Each time you open a product page, its price is noted under a product id
 * ("amazon.in:B0XXXXXXX"), at most once every six hours. Next time you see
 * the product you can tell whether "Deal of the day" is lower than what you
 * saw last week -- or the same price with a new banner. If you looked at the
 * same product on another shop, the popup shows that price too.
 *
 * Stored in storage.local, never sent anywhere: the product id, prices and
 * dates, a scrambled fingerprint of the product name (word codes, used only
 * to recognise the same product on another shop), and any "only N left"
 * number the page showed. No product name, no page address. Capped at 400
 * products and 180 days; cleared with one button in the popup.
 */
(function (root) {
  "use strict";
  const C = root.UlteriorCore;
  const ext = (root.browser && root.browser.runtime) ? root.browser : root.chrome;
  const INDEX = "ph_index";
  const MAX_PRODUCTS = 400, MAX_POINTS = 40, MAX_CLAIMS = 10;
  const MIN_GAP_MS = 6 * 3600 * 1000, MAX_AGE_MS = 180 * 24 * 3600 * 1000;
  const CLAIM_SPAN_MS = 12 * 3600 * 1000;

  const day = (t) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  async function record(key, price, opts) {
    if (!key || !(price > 0)) return null;
    opts = opts || {};
    const store = ext.storage.local;
    const slot = "ph:" + key;
    const now = Date.now();
    const got = await store.get([slot, INDEX]);
    const entry = got[slot] || { pts: [] };
    let pts = (entry.pts || []).filter((p) => now - p.t < MAX_AGE_MS);
    const last = pts[pts.length - 1];
    if (!last || last.p !== price || now - last.t >= MIN_GAP_MS) pts.push({ p: price, t: now });
    pts = pts.slice(-MAX_POINTS);

    let claims = (entry.claims || []).filter((c) => now - c.t < MAX_AGE_MS);
    if (opts.stockClaim) {
      const lastClaim = claims[claims.length - 1];
      if (!lastClaim || lastClaim.n !== opts.stockClaim || now - lastClaim.t >= MIN_GAP_MS) {
        claims.push({ n: opts.stockClaim, t: now });
      }
      claims = claims.slice(-MAX_CLAIMS);
    }

    const fp = opts.fp && opts.fp.length ? opts.fp : (entry.fp || []);
    let index = (got[INDEX] || []).filter((k) => k !== key);
    index.push(key);
    const evicted = index.length > MAX_PRODUCTS ? index.slice(0, index.length - MAX_PRODUCTS) : [];
    index = index.slice(-MAX_PRODUCTS);
    await store.set({ [slot]: { pts, fp, claims, site: C.siteOf(key.split(":")[0]) }, [INDEX]: index });
    if (evicted.length) await store.remove(evicted.map((k) => "ph:" + k));

    const result = insight(pts, price);
    result.series = pts.map((x) => ({ p: x.p, t: x.t, src: "you" }));
    result.elsewhere = await elsewhere(key, fp, index);
    result.stockFinding = stockFinding(claims, opts.stockClaim, opts.stockText);
    return result;
  }

  function insight(pts, current) {
    if (!pts.length) return null;
    let min = pts[0], max = pts[0];
    for (const p of pts) {
      if (p.p < min.p) min = p;
      if (p.p > max.p) max = p;
    }
    return {
      count: pts.length,
      current: C.formatINR(current), currentValue: current,
      lowest: C.formatINR(min.p), lowestOn: day(min.t), lowestValue: min.p,
      highest: C.formatINR(max.p), highestOn: day(max.t),
      since: day(pts[0].t),
      points: pts.map((p) => p.p),
    };
  }

  /** The same product, seen on other shops, newest first. */
  async function elsewhere(key, fp, index) {
    if (!fp || fp.length < 3) return [];
    const mySite = key.split(":")[0];
    const others = index.filter((k) => k.split(":")[0] !== mySite);
    if (!others.length) return [];
    const got = await ext.storage.local.get(others.map((k) => "ph:" + k));
    const out = [];
    for (const k of others) {
      const e = got["ph:" + k];
      if (!e || !e.fp || !e.pts || !e.pts.length || !C.sameProduct(fp, e.fp)) continue;
      const lastPt = e.pts[e.pts.length - 1];
      out.push({ site: k.split(":")[0], price: C.formatINR(lastPt.p), priceValue: lastPt.p, on: day(lastPt.t), t: lastPt.t });
    }
    out.sort((a, b) => b.t - a.t);
    return out.slice(0, 4);
  }

  /**
   * DP-01, indicative: the same "only N left" on visits at least 12 hours
   * apart. Real stock that is genuinely running low tends to move; a number
   * that never does is consistent with a fabricated claim -- but a shop that
   * really has two left would show the same, so a person should confirm.
   */
  function stockFinding(claims, current, text) {
    if (!current || claims.length < 2) return null;
    const same = claims.filter((c) => c.n === current);
    if (same.length < 2 || same[same.length - 1].t - same[0].t < CLAIM_SPAN_MS) return null;
    return {
      code: "DP-01", tier: "indicative",
      title: `"Only ${current} left" has not changed since ${day(same[0].t)}`,
      detail: `You saw the same stock claim on ${same.length} visits from ${day(same[0].t)} to today. ` +
        "Stock that is really running out usually moves; a person should confirm before relying on this.",
      evidence: {
        claim: C.clip(C.redact(text || `Only ${current} left`), 80),
        first_seen: day(same[0].t), visits_with_same_number: String(same.length),
      },
    };
  }

  async function clearAll() {
    const store = ext.storage.local;
    const got = await store.get(INDEX);
    const keys = (got[INDEX] || []).map((k) => "ph:" + k);
    await store.remove(keys.concat([INDEX]));
  }

  root.UlteriorHistory = { record, insight, clearAll, stockFinding };
})(globalThis);
