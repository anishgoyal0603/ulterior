/**
 * Ulterior extension -- the watchlist page.
 *
 * Every value here came from shopping pages, so it goes in as text. Network:
 * none from this page. "Check community prices now" and the drops list ask
 * the background script, which only contacts the server when community
 * prices are switched on and permitted.
 */
(function () {
  "use strict";
  const C = globalThis.UlteriorCore, Watch = globalThis.UlteriorWatch, Chart = globalThis.UlteriorChart;
  const ext = (globalThis.browser && globalThis.browser.runtime) ? globalThis.browser : globalThis.chrome;
  const $ = (id) => document.getElementById(id);
  const day = (t) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }

  function num(label, value, cls) {
    const d = el("div", cls || "");
    d.append(el("span", "", label), el("b", "", value));
    return d;
  }

  async function render() {
    const items = await Watch.list();
    $("count").textContent = `${items.length} of ${Watch.MAX}`;
    $("empty").hidden = items.length > 0;
    const host = $("items");
    host.replaceChildren();
    const hist = await ext.storage.local.get(items.map((i) => "ph:" + i.key));
    for (const it of items.slice().reverse()) {
      const card = el("article", "item");
      const left = el("div");
      const h = el("h2");
      if (it.url && /^https?:\/\//.test(it.url)) {
        const a = el("a", "", it.title);
        a.href = it.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        h.append(a);
      } else {
        h.textContent = it.title;
      }
      left.append(h, el("p", "meta2", `${it.site} · watching since ${day(it.addedAt)}` +
        (it.lastSeen ? ` · last price ${day(it.lastSeen)} (${it.lastSource === "community" ? "community" : "you"})` : "")));
      const nums = el("div", "nums");
      nums.append(num("Last price", it.lastPrice ? C.formatINR(it.lastPrice) : "-",
                      it.lastPrice && it.lastPrice <= it.target ? "hit" : ""));
      nums.append(num("Your target", C.formatINR(it.target)));
      if (it.startPrice) nums.append(num("When you started", C.formatINR(it.startPrice)));
      left.append(nums);

      const row = el("div", "watchrow");
      const input = el("input");
      input.type = "number";
      input.min = "1";
      input.value = String(it.target);
      input.setAttribute("aria-label", `New target for ${it.title}`);
      const save = el("button", "btn", "Change target");
      save.addEventListener("click", async () => {
        const t = parseFloat(input.value);
        if (t > 0) { await Watch.add(Object.assign({}, it, { target: t, price: it.lastPrice })); render(); }
      });
      const rm = el("button", "btn", "Remove");
      rm.addEventListener("click", async () => { await Watch.remove(it.key); render(); });
      row.append(el("span", "", "₹"), input, save, rm);
      left.append(row);

      const right = el("div");
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "chart");
      const tip = el("div", "tip");
      tip.hidden = true;
      const pts = ((hist["ph:" + it.key] || {}).pts || []).map((x) => ({ p: x.p, t: x.t, src: "you" }));
      right.append(svg, tip);
      card.append(left, right);
      host.append(card);
      Chart.draw(svg, pts, { days: 0, target: it.target, tooltip: tip });
    }

    const log = ((await ext.storage.local.get("alerts_log")).alerts_log) || [];
    $("alerts-box").hidden = !log.length;
    const ul = $("alerts");
    ul.replaceChildren();
    for (const a of log) {
      ul.append(el("li", "", `${day(a.at)}: ${a.title} at ${C.formatINR(a.price)} (target ${C.formatINR(a.target)}, ` +
        `${a.source === "community" ? "seen by the community" : "you saw it"})`));
    }

    const s = ((await ext.storage.local.get("settings")).settings) || {};
    $("check-now").hidden = !s.community;
    if (s.community) loadDrops();
  }

  async function loadDrops() {
    const r = await ext.runtime.sendMessage({ type: "COMMUNITY_DROPS" });
    const list = (r && r.ok && r.drops) || [];
    $("drops-box").hidden = !list.length;
    const ul = $("drops");
    ul.replaceChildren();
    for (const d of list) {
      ul.append(el("li", "", `${d.key.split(":")[0]}: ${C.formatINR(d.from)} → ${C.formatINR(d.to)} ` +
        `(${Math.round(d.pct)}% down, ${d.reports} reports)`));
    }
  }

  $("check-now").addEventListener("click", async () => {
    $("check-status").textContent = "Checking…";
    const r = await ext.runtime.sendMessage({ type: "RUN_WATCH_CHECK" });
    $("check-status").textContent = r && r.checked !== undefined
      ? `Checked ${r.checked} product${r.checked === 1 ? "" : "s"}${r.alerts ? `, ${r.alerts} alert${r.alerts === 1 ? "" : "s"}` : ""}.`
      : "Could not reach community prices.";
    render();
  });

  ext.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && Object.keys(changes).some((k) => k.startsWith("watch") || k === "alerts_log")) render();
  });
  render();
})();
