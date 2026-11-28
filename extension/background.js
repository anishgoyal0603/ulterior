/**
 * Ulterior extension -- background script (a service worker in Chrome and
 * Edge, an event page in Firefox).
 *
 * Keeps each tab's results and the per-site session memory, draws the
 * toolbar badge, runs "Scan this page" for sites that are not on the
 * auto-run list, and shows price-drop notifications for your watchlist.
 *
 * NETWORK: the only requests made here are for community prices, and only
 * when the person has switched them on in the popup AND granted the browser
 * permission that switch asks for (communityFetch). Without both, nothing
 * is sent.
 */
if (typeof importScripts === "function" && !globalThis.UlteriorCore) importScripts("src/core.js", "src/watch.js");
const C = globalThis.UlteriorCore;
const Watch = globalThis.UlteriorWatch;
const ext = (globalThis.browser && globalThis.browser.runtime) ? globalThis.browser : globalThis.chrome;

const FILES = ["src/core.js", "src/platform.js", "src/detect.js", "src/chart.js", "src/journey.js",
               "src/history.js", "src/watch.js", "src/overlay.js", "src/content.js"];

const TIER_RANK = { provable: 3, corroborated: 2, indicative: 1 };
const TIER_COLOR = { provable: "#0e9e6e", corroborated: "#4a4fd0", indicative: "#bd7a12" };

function badgeFor(tabId, findings) {
  const n = findings.length;
  ext.action.setBadgeText({ tabId, text: n ? String(n) : "" }).catch(() => {});
  if (!n) return;
  const top = findings.reduce((a, f) => (TIER_RANK[f.tier] || 0) > (TIER_RANK[a] || 0) ? f.tier : a, "indicative");
  ext.action.setBadgeBackgroundColor({ tabId, color: TIER_COLOR[top] || "#4a4fd0" }).catch(() => {});
}

function senderSite(sender) {
  try { return C.siteOf(new URL(sender.url || (sender.tab && sender.tab.url) || "").hostname); }
  catch (e) { return ""; }
}

// Session memory a page may use, and only for its own site: a page from
// shop-a.com asking for "journey:shop-b.com" gets nothing.
const SESSION_KINDS = ["journey", "timer", "hide", "nag"];

function allowedKey(key, sender) {
  if (typeof key !== "string" || key.length > 300) return false;
  const site = senderSite(sender);
  if (!site) return false;
  return SESSION_KINDS.some((k) => {
    const prefix = `${k}:${site}`;
    return key === prefix || key.startsWith(prefix + "/") || key.startsWith(prefix + ":");
  });
}

ext.runtime.onMessage.addListener((msg, sender, reply) => {
  if (!msg || typeof msg !== "object") return false;

  if (msg.type === "RESULTS" && sender.tab && msg.results) {
    const tabId = sender.tab.id;
    const findings = Array.isArray(msg.results.findings) ? msg.results.findings : [];
    ext.storage.session.set({ ["tab:" + tabId]: msg.results }).catch(() => {});
    badgeFor(tabId, findings);
    return false;
  }

  if (msg.type === "SESSION_GET" && sender.tab) {
    if (!allowedKey(msg.key, sender)) { reply({ ok: false }); return false; }
    ext.storage.session.get(msg.key).then((r) => reply({ ok: true, value: r[msg.key] ?? null }))
      .catch(() => reply({ ok: false }));
    return true;
  }

  if (msg.type === "SESSION_SET" && sender.tab) {
    if (!allowedKey(msg.key, sender)) { reply({ ok: false }); return false; }
    let size = 0;
    try { size = JSON.stringify(msg.value).length; } catch (e) { size = Infinity; }
    if (size > 16000) { reply({ ok: false }); return false; }
    ext.storage.session.set({ [msg.key]: msg.value }).then(() => reply({ ok: true }))
      .catch(() => reply({ ok: false }));
    return true;
  }

  if (msg.type === "COMMUNITY_GET" && sender.tab) {
    if (!keyBelongsToSender(msg.key, sender) || !communitySite(msg.key)) { reply({ ok: false }); return false; }
    communityHistory(msg.key).then((series) => reply(series ? { ok: true, series } : { ok: false }));
    return true;
  }

  if (msg.type === "COMMUNITY_PUT" && sender.tab) {
    if (keyBelongsToSender(msg.key, sender) && communitySite(msg.key) && msg.price > 0) communityPut(msg.key, msg.price);
    return false;
  }

  if (msg.type === "NOTIFY" && (sender.tab || isExtensionPage(sender))) {
    if (sender.tab && !keyBelongsToSender(msg.key, sender)) return false;
    priceAlert(msg);
    return false;
  }

  if (msg.type === "RUN_WATCH_CHECK" && isExtensionPage(sender)) {
    runWatchCheck().then(reply);
    return true;
  }

  if (msg.type === "COMMUNITY_DROPS" && isExtensionPage(sender)) {
    communityOn().then(async (on) => {
      if (!on) return reply({ ok: false });
      try { reply({ ok: true, drops: (await communityFetch(C.COMMUNITY_PATHS.drops, { days: 7, limit: 15 })).drops || [] }); }
      catch (e) { reply({ ok: false }); }
    });
    return true;
  }

  // Only the extension's own popup may ask for a scan; a page cannot.
  const fromExtension = typeof sender.url === "string" && sender.url.startsWith(ext.runtime.getURL(""));
  if (msg.type === "SCAN_TAB" && Number.isInteger(msg.tabId) && fromExtension) {
    ext.scripting.executeScript({ target: { tabId: msg.tabId }, files: FILES })
      .then(() => reply({ ok: true }))
      .catch((e) => reply({ ok: false, error: String((e && e.message) || e) }));
    return true;
  }
  return false;
});

function isExtensionPage(sender) {
  return typeof sender.url === "string" && sender.url.startsWith(ext.runtime.getURL(""));
}

/** A page may only ask about products of its own site. */
function keyBelongsToSender(key, sender) {
  if (typeof key !== "string" || key.length > 230) return false;
  return key.split(":")[0] === senderSite(sender);
}

// Community prices are for the listed shops only. "Scan this page" can run
// on ANY site (a company portal, a staging store); its product ids must
// never be sent anywhere, even though the server would refuse them.
const COMMUNITY_DEV_SITES = ["127.0.0.1", "localhost"];
function communitySite(key) {
  const site = String(key || "").split(":")[0];
  return C.SUPPORTED_SITES.includes(site) || COMMUNITY_DEV_SITES.includes(site);
}

// ---------------------------------------------------- community prices
const COMMUNITY_PERMISSION = { origins: [C.REPORT_ORIGIN + "/*"] };

async function communityOn() {
  const s = ((await ext.storage.local.get("settings")).settings) || {};
  if (!s.community) return false;
  try { return await ext.permissions.contains(COMMUNITY_PERMISSION); } catch (e) { return false; }
}

/** The extension's only network call outside the popup's report button. */
async function communityFetch(path, body) {
  const r = await fetch(C.REPORT_ORIGIN + path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), credentials: "omit",
  });
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

async function communityHistory(key) {
  if (!(await communityOn())) return null;
  const cacheKey = "ch:" + key;
  const cached = (await ext.storage.session.get(cacheKey))[cacheKey];
  if (cached && Date.now() - cached.at < 30 * 60 * 1000) return cached.series;
  try {
    const r = await communityFetch(C.COMMUNITY_PATHS.history, { key, days: 365 });
    const series = (r.daily || []).map((d) => ({ p: d.price, t: Date.parse(d.day + "T12:00:00Z"), src: "community", n: d.reports }));
    await ext.storage.session.set({ [cacheKey]: { at: Date.now(), series } });
    return series;
  } catch (e) { return null; }
}

async function communityPut(key, price) {
  if (!(await communityOn())) return;
  const k = "cp:" + key;
  const last = (await ext.storage.session.get(k))[k];
  if (last && last.price === price && Date.now() - last.at < 6 * 3600 * 1000) return;   // once per 6 h per price
  await ext.storage.session.set({ [k]: { at: Date.now(), price } });
  communityFetch(C.COMMUNITY_PATHS.put, { observations: [{ key, price }] }).catch(() => {});
}

// --------------------------------------------------------- price alerts
async function priceAlert(a) {
  // Unique per alert: two drops in the same millisecond used to share an id,
  // so the second notification replaced the first and its link.
  const id = `drop-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const title = `Price drop: ${C.formatINR(a.price)}`;
  const message = `${C.clip(a.title || a.key, 90)} is at or under your target of ${C.formatINR(a.target)}` +
    (a.source === "community" ? " (seen by the community)." : ".");
  try {
    await ext.notifications.create(id, { type: "basic", iconUrl: ext.runtime.getURL("icons/icon128.png"), title, message });
  } catch (e) { /* notifications blocked by the OS: the log below still records it */ }
  if (a.url) await ext.storage.session.set({ ["notif:" + id]: a.url });
  const log = ((await ext.storage.local.get("alerts_log")).alerts_log) || [];
  log.unshift({ at: Date.now(), key: a.key, title: C.clip(a.title || a.key, 90), price: a.price, target: a.target,
                source: a.source || "you" });
  await ext.storage.local.set({ alerts_log: log.slice(0, 30) });
}

ext.notifications.onClicked.addListener(async (id) => {
  const url = (await ext.storage.session.get("notif:" + id))["notif:" + id];
  if (url && /^https?:\/\//.test(url)) ext.tabs.create({ url });
  ext.notifications.clear(id);
});

/** Every six hours: ask the community prices for everything you watch. */
async function runWatchCheck() {
  if (!(await communityOn())) return { checked: 0 };
  // Watched items from unlisted sites stay on this device only.
  const items = (await Watch.list()).filter((i) => communitySite(i.key));
  if (!items.length) return { checked: 0 };
  let latest;
  try { latest = await communityFetch(C.COMMUNITY_PATHS.latest, { keys: items.map((i) => i.key).slice(0, 100) }); }
  catch (e) { return { checked: 0, error: String(e.message || e) }; }
  let alerts = 0;
  for (const item of items) {
    const l = (latest.prices || {})[item.key];
    if (!l || !(l.price > 0)) continue;
    const s = await Watch.seen(item.key, l.price, "community");
    if (s.hit) {
      alerts++;
      await priceAlert({ key: item.key, title: item.title, price: l.price, target: item.target, url: item.url, source: "community" });
    }
  }
  return { checked: items.length, alerts };
}
globalThis.runWatchCheck = runWatchCheck;

function scheduleWatchCheck() {
  ext.alarms.create("ulterior-watch", { periodInMinutes: 360, delayInMinutes: 5 });
}
ext.runtime.onInstalled.addListener(scheduleWatchCheck);
ext.runtime.onStartup.addListener(scheduleWatchCheck);
ext.alarms.onAlarm.addListener((a) => { if (a.name === "ulterior-watch") runWatchCheck(); });

ext.tabs.onRemoved.addListener((tabId) => {
  ext.storage.session.remove("tab:" + tabId).catch(() => {});
});

// Leaving the site clears that tab's results, so the popup never shows
// one shop's findings over another shop's page.
ext.tabs.onUpdated.addListener(async (tabId, info) => {
  if (!info.url) return;
  let site = "";
  try { site = C.siteOf(new URL(info.url).hostname); } catch (e) { /* about: pages etc. */ }
  const key = "tab:" + tabId;
  const got = await ext.storage.session.get(key).catch(() => ({}));
  if (got[key] && got[key].site !== site) {
    await ext.storage.session.remove(key).catch(() => {});
    ext.action.setBadgeText({ tabId, text: "" }).catch(() => {});
  }
});
