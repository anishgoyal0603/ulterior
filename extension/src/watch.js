/**
 * Ulterior extension -- your watchlist and target prices.
 *
 * You add a product and the price you would pay. When you next open it, or
 * (if you switched community prices on) when someone else's browser sees it
 * at or under your target, Ulterior shows a notification.
 *
 * Kept in storage.local on your device: for each watched product, the
 * product id, the name and link YOU chose to watch (link without its query
 * string), your target, and the prices seen. Up to 100 products. Removing a
 * product removes all of it.
 */
(function (root) {
  "use strict";
  const C = root.UlteriorCore;
  const ext = (root.browser && root.browser.runtime) ? root.browser : root.chrome;
  const INDEX = "watch_index";
  const MAX = 100;
  const slot = (key) => "watch:" + key;

  async function get(key) {
    const r = await ext.storage.local.get(slot(key));
    return r[slot(key)] || null;
  }

  async function list() {
    const idx = (await ext.storage.local.get(INDEX))[INDEX] || [];
    if (!idx.length) return [];
    const got = await ext.storage.local.get(idx.map(slot));
    return idx.map((k) => got[slot(k)]).filter(Boolean);
  }

  async function add(item) {
    if (!item || !item.key || !(item.target > 0)) throw new Error("A product and a target price are needed.");
    const url = C.cleanProductUrl(item.url || "");
    const now = Date.now();
    const old = await get(item.key);
    const entry = Object.assign({}, old || {}, {
      key: item.key, site: item.site || item.key.split(":")[0],
      title: C.clip(C.redact(item.title || item.key), 100), url,
      target: Math.round(item.target * 100) / 100,
      addedAt: old ? old.addedAt : now,
      lastPrice: item.price || (old && old.lastPrice) || null, lastSeen: now,
      startPrice: old ? old.startPrice : (item.price || null),
      alertedPrice: null,
    });
    let idx = ((await ext.storage.local.get(INDEX))[INDEX] || []).filter((k) => k !== item.key);
    idx.push(item.key);
    if (idx.length > MAX) {
      const drop = idx.slice(0, idx.length - MAX);
      idx = idx.slice(-MAX);
      await ext.storage.local.remove(drop.map(slot));
    }
    await ext.storage.local.set({ [slot(item.key)]: entry, [INDEX]: idx });
    return entry;
  }

  async function remove(key) {
    const idx = ((await ext.storage.local.get(INDEX))[INDEX] || []).filter((k) => k !== key);
    await ext.storage.local.remove(slot(key));
    await ext.storage.local.set({ [INDEX]: idx });
  }

  /**
   * A price for a watched product was seen (by you, or by the community
   * check). Returns {item, hit}: hit is true when it is at or under your
   * target and lower than the last price you were alerted about -- so one
   * drop gives one notification, and a further drop gives another.
   */
  async function seen(key, price, source) {
    const item = await get(key);
    if (!item || !(price > 0)) return { item: null, hit: false };
    item.lastPrice = price;
    item.lastSeen = Date.now();
    item.lastSource = source || "you";
    const hit = price <= item.target && (item.alertedPrice === null || item.alertedPrice === undefined || price < item.alertedPrice);
    if (hit) item.alertedPrice = price;
    if (price > item.target) item.alertedPrice = null;   // back above target: the next drop alerts again
    await ext.storage.local.set({ [slot(key)]: item });
    return { item, hit };
  }

  root.UlteriorWatch = { get, list, add, remove, seen, MAX };
})(globalThis);
