/**
 * Ulterior extension -- the small alert on the page itself.
 *
 * Shown only when there is a finding (never for notes), in the corner, in a
 * closed shadow root so the shop's CSS cannot restyle it and its scripts
 * cannot read it. Every string that came from the page goes in through
 * textContent: a shop being flagged is exactly the party with a motive to
 * put markup in a button label.
 *
 * Styles are a constructable stylesheet, not a <style> tag, so a shop whose
 * Content-Security-Policy forbids inline styles still gets a styled alert.
 */
(function (root) {
  "use strict";
  const C = root.UlteriorCore;

  const CSS = `
  :host { all: initial; }
  * { box-sizing: border-box; }
  .wrap { position: fixed; right: 16px; bottom: 16px; z-index: 2147483646;
    font: 13px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #0b1633; }
  .pill { display: flex; align-items: center; gap: 8px; padding: 9px 14px 9px 10px; border-radius: 999px;
    border: 0; cursor: pointer; background: #3538cd; color: #fff; font: 700 13px/1 system-ui, sans-serif;
    box-shadow: 0 10px 28px -10px rgba(11,22,51,.55), 0 2px 6px rgba(11,22,51,.2); }
  .pill:focus-visible, button:focus-visible { outline: 3px solid #ffd24a; outline-offset: 2px; }
  .dot { width: 20px; height: 20px; border-radius: 6px; background: #fff; color: #3538cd;
    display: grid; place-items: center; font: 800 12px/1 system-ui, sans-serif; }
  .panel { position: absolute; right: 0; bottom: 48px; width: min(360px, calc(100vw - 32px));
    max-height: 62vh; overflow: auto; background: #fff; border-radius: 16px; border: 1px solid #e4e8f2;
    box-shadow: 0 18px 44px -14px rgba(11,22,51,.45); padding: 14px; }
  .panel[hidden] { display: none; }
  .head { display: flex; align-items: center; justify-content: space-between; margin: 0 0 8px; }
  .head b { font-size: 14px; }
  .x { border: 0; background: #eef1f8; color: #3a4766; border-radius: 8px; width: 28px; height: 28px;
    cursor: pointer; font: 700 16px/1 system-ui, sans-serif; }
  .f { border: 1px solid #e4e8f2; border-radius: 12px; padding: 10px 11px; margin: 0 0 8px; }
  .meta { display: flex; gap: 6px; align-items: center; margin: 0 0 4px; }
  .code { font: 800 11px/1 ui-monospace, Menlo, Consolas, monospace; background: #0b1633; color: #fff;
    padding: 4px 6px; border-radius: 6px; }
  .tier { font: 800 10px/1 system-ui, sans-serif; letter-spacing: .06em; text-transform: uppercase;
    padding: 4px 7px; border-radius: 999px; }
  .tier.provable { background: #e7f7f0; color: #0b7a55; }
  .tier.corroborated { background: #ecedfc; color: #3c41c2; }
  .tier.indicative { background: #fbf2e2; color: #8f5a08; }
  .t { font-weight: 700; margin: 0 0 3px; }
  .d { margin: 0; color: #3a4766; font-size: 12.5px; }
  .row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .btn { border: 1.5px solid #d3d9e8; background: #fff; color: #0b1633; border-radius: 10px;
    padding: 8px 12px; font: 700 12.5px/1 system-ui, sans-serif; cursor: pointer; }
  .btn.primary { background: #3538cd; border-color: #3538cd; color: #fff; }
  .fine { margin: 10px 0 0; font-size: 11.5px; color: #5d6781; }
  .price { border: 1px solid #e4e8f2; border-radius: 12px; padding: 10px 11px; margin: 0 0 8px; }
  .verdict { display: inline-block; font: 800 11px/1 system-ui, sans-serif; letter-spacing: .04em; text-transform: uppercase;
    padding: 5px 8px; border-radius: 999px; margin: 0 0 6px; }
  .verdict.good { background: #e7f7f0; color: #0b7a55; }
  .verdict.fair { background: #ecedfc; color: #3c41c2; }
  .verdict.high { background: #fbf2e2; color: #8f5a08; }
  .verdict.unknown { background: #eef1f8; color: #3a4766; }
  .periods { display: flex; gap: 4px; margin: 6px 0 2px; }
  .periods button { border: 1px solid #d3d9e8; background: #fff; color: #3a4766; border-radius: 999px; padding: 3px 8px;
    font: 700 11px/1.2 system-ui, sans-serif; cursor: pointer; }
  .periods button[aria-pressed="true"] { background: #3538cd; border-color: #3538cd; color: #fff; }
  svg.chart { width: 100%; height: auto; display: block; }
  .tip { font-size: 12px; color: #3a4766; min-height: 1.3em; }
  .tip[hidden] { visibility: hidden; display: block; }
  .tip strong { color: #0b1633; }
  .disc { margin: 6px 0 0; font-size: 12px; color: #3a4766; }
  .disc.warn { color: #8f5a08; font-weight: 650; }
  .watchrow { display: flex; gap: 6px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
  .watchrow input { width: 96px; padding: 6px 8px; border: 1.5px solid #d3d9e8; border-radius: 8px; font: 13px system-ui, sans-serif; }
  ${root.UlteriorChart ? root.UlteriorChart.CSS : ""}
  @media (prefers-color-scheme: dark) {
    .price { border-color: #2e3a63; }
    .periods button { background: #172042; color: #eef1fa; border-color: #2e3a63; }
    .tip, .disc { color: #b9c2da; } .tip strong { color: #eef1fa; }
    .watchrow input { background: #172042; color: #eef1fa; border-color: #2e3a63; }
    .panel { background: #131b35; border-color: #222c4d; color: #eef1fa; }
    .f { border-color: #2e3a63; }
    .d, .fine { color: #b9c2da; }
    .x { background: #1c2650; color: #eef1fa; }
    .btn { background: #172042; color: #eef1fa; border-color: #2e3a63; }
    .btn.primary { background: #5b5fef; border-color: #5b5fef; }
    .code { background: #eef1fa; color: #0b1633; }
  }`;

  let host = null, shadow = null, panelOpen = false, useStyles = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function remove() {
    if (host && host.parentNode) host.parentNode.removeChild(host);
    host = null;
    shadow = null;
  }

  async function copyText(text, button) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text;
      shadow.appendChild(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
    }
    button.textContent = ok ? "Copied ✓" : "Copy failed - use the toolbar popup";
  }

  let period = 90;

  /** The price check on a product page: verdict, chart, the real-discount
   *  check and a Watch button. */
  function priceSection(price, opts) {
    const box = el("div", "price");
    box.append(el("span", "verdict " + price.verdict.kind, price.verdict.label));
    box.append(el("p", "d", `${C.formatINR(price.current)} now. ${price.verdict.detail}`));
    const periods = el("div", "periods");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "chart");
    const tip = el("div", "tip");
    tip.hidden = true;
    const redraw = () => root.UlteriorChart.draw(svg, price.series, { days: period, target: price.target, tooltip: tip });
    for (const [label, days] of [["15d", 15], ["45d", 45], ["90d", 90], ["6m", 182], ["All", 0]]) {
      const b = el("button", "", label);
      b.setAttribute("aria-pressed", String(period === days));
      b.addEventListener("click", () => {
        period = days;
        [...periods.children].forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
        redraw();
      });
      periods.append(b);
    }
    box.append(periods, svg, tip);
    redraw();
    if (price.discount) box.append(el("p", "disc " + price.discount.kind, price.discount.text));
    if (price.community) box.append(el("p", "fine", `Includes community prices from ${price.community} day${price.community === 1 ? "" : "s"}.`));

    const row = el("div", "watchrow");
    if (price.target) {
      row.append(el("span", "d", `Watching - you'll be told at ${C.formatINR(price.target)} or less.`));
      const stop = el("button", "btn", "Stop watching");
      stop.addEventListener("click", () => opts.onUnwatch && opts.onUnwatch());
      row.append(stop);
    } else {
      const input = el("input");
      input.type = "number";
      input.min = "1";
      input.value = String(C.suggestTarget(price.current));
      input.setAttribute("aria-label", "Target price in rupees");
      const watch = el("button", "btn primary", "Watch price");
      watch.addEventListener("click", () => {
        const t = parseFloat(input.value);
        if (t > 0 && opts.onWatch) opts.onWatch(t);
      });
      row.append(el("span", "d", "Tell me at \u20b9"), input, watch);
    }
    box.append(row);
    return box;
  }

  /**
   * findings: the page's findings. opts.onHide: called when the person asks
   * not to see it on this site again this session. opts.price: the price
   * check for a product page (shown even with no findings).
   */
  function render(findings, site, opts) {
    opts = opts || {};
    findings = findings || [];
    const price = opts.price && root.UlteriorChart ? opts.price : null;
    if (!findings.length && !price) { remove(); return; }
    if (!host || !host.isConnected) {
      host = document.createElement("ulterior-alert");
      shadow = host.attachShadow({ mode: "closed" });
      useStyles = null;
      try {
        const sheet = new CSSStyleSheet();
        sheet.replaceSync(CSS);
        shadow.adoptedStyleSheets = [sheet];
      } catch (e) {
        // Firefox content scripts cannot always hand a constructed sheet to
        // the page's shadow root; a <style> inside the shadow works there.
        useStyles = CSS;
      }
      document.documentElement.appendChild(host);
    }
    host.setAttribute("data-count", String(findings.length));
    host.setAttribute("data-mode", findings.length ? "findings" : "price");
    host.setAttribute("data-verdict", price ? price.verdict.kind : "");
    shadow.replaceChildren();
    if (useStyles) shadow.append(el("style", "", useStyles));

    const wrap = el("div", "wrap");
    const panel = el("div", "panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Ulterior findings on this page");
    panel.hidden = !panelOpen;

    const head = el("div", "head");
    head.append(el("b", "", findings.length === 0 ? "Price check"
      : findings.length === 1 ? "1 thing to check on this page" : `${findings.length} things to check on this page`));
    const x = el("button", "x", "×");
    x.setAttribute("aria-label", "Close");
    x.addEventListener("click", () => { panelOpen = false; panel.hidden = true; });
    head.append(x);
    panel.append(head);

    if (price) panel.append(priceSection(price, opts));

    for (const f of findings) {
      const card = el("div", "f");
      const meta = el("div", "meta");
      meta.append(el("span", "code", f.code), el("span", "tier " + f.tier, f.tier));
      meta.append(el("span", "", C.PATTERN_NAMES[f.code] || ""));
      card.append(meta, el("p", "t", f.title), el("p", "d", f.detail || ""));
      panel.append(card);
    }

    const row = el("div", "row");
    if (findings.length) {
      const copy = el("button", "btn primary", "Copy evidence");
      copy.addEventListener("click", () => copyText(C.buildReport(site, findings, new Date()), copy));
      row.append(copy);
    }
    const hide = el("button", "btn", "Hide on this site");
    hide.addEventListener("click", () => { remove(); if (opts.onHide) opts.onHide(); });
    row.append(hide);
    panel.append(row);
    panel.append(el("p", "fine", findings.length
      ? "Observations of resemblance to patterns in India's 2023 dark-pattern Guidelines - not a legal finding."
      : "Prices you have seen are kept on your device."));

    const pill = el("button", "pill");
    pill.setAttribute("aria-expanded", String(panelOpen));
    pill.append(el("span", "dot", "U"),
      el("span", "", findings.length ? `Ulterior \u00b7 ${findings.length} to check`
                                     : `Ulterior \u00b7 ${price.verdict.kind === "unknown" ? "price check" : price.verdict.label}`));
    pill.addEventListener("click", () => {
      panelOpen = !panelOpen;
      panel.hidden = !panelOpen;
      pill.setAttribute("aria-expanded", String(panelOpen));
    });

    wrap.append(panel, pill);
    shadow.append(wrap);
  }

  root.UlteriorOverlay = { render, remove };
})(globalThis);
