/**
 * Ulterior extension -- the price history chart.
 *
 * One series (the price of one product over time), so: a 2px line, no
 * legend, the title names it. Two direct labels only -- the lowest price and
 * today's -- plus a dashed line at your target if you are watching it. A
 * crosshair snaps to the nearest day on hover or arrow keys and shows the
 * value in a tooltip; the same numbers are always in the text beside it, so
 * the tooltip never gates anything.
 *
 * Colours come from CSS classes (pc-*) set by whichever page draws it -- the
 * popup, the watchlist page or the on-page panel -- so light and dark each
 * get their own validated line colour.
 */
(function (root) {
  "use strict";
  const C = root.UlteriorCore;
  const NS = "http://www.w3.org/2000/svg";
  const W = 300, H = 120, L = 46, R = 10, T = 12, B = 22;

  function node(name, attrs, text) {
    const n = document.createElementNS(NS, name);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }

  const day = (t) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  /**
   * svg: an <svg> element. series: [{p, t, src}] oldest first.
   * opts: {days, target, tooltip (an HTML element), now}
   * Returns the points actually drawn.
   */
  function draw(svg, series, opts) {
    opts = opts || {};
    const now = opts.now || Date.now();
    const from = opts.days ? now - opts.days * C.DAY : -Infinity;
    const pts = (series || []).filter((x) => x.t >= from);
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    if (pts.length < 2) {
      svg.append(node("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "pc-label" },
        pts.length ? "One price so far - the line starts with the next one" : "No prices in this period"));
      svg.setAttribute("aria-label", "Not enough prices to draw a line yet");
      return pts;
    }
    const prices = pts.map((x) => x.p).concat(opts.target ? [opts.target] : []);
    let lo = Math.min(...prices), hi = Math.max(...prices);
    const pad = Math.max(1, (hi - lo) * 0.12);
    lo = Math.max(0, lo - pad); hi += pad;
    const t0 = pts[0].t, t1 = Math.max(pts[pts.length - 1].t, t0 + 1);
    const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
    const y = (p) => T + (1 - (p - lo) / (hi - lo)) * (H - T - B);

    // Recessive frame: baseline and two value ticks, no grid.
    svg.append(node("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "pc-axis" }));
    svg.append(node("text", { x: L - 6, y: y(hi - pad) + 4, "text-anchor": "end", class: "pc-label" }, C.formatINR(Math.round(hi - pad))));
    svg.append(node("text", { x: L - 6, y: y(lo + pad) + 4, "text-anchor": "end", class: "pc-label" }, C.formatINR(Math.round(lo + pad))));
    svg.append(node("text", { x: L, y: H - 6, class: "pc-label" }, day(t0)));
    svg.append(node("text", { x: W - R, y: H - 6, "text-anchor": "end", class: "pc-label" }, day(pts[pts.length - 1].t)));

    if (opts.target) {
      svg.append(node("line", { x1: L, x2: W - R, y1: y(opts.target), y2: y(opts.target), class: "pc-target" }));
      // Labelled at the left end, where the line never ends in today's dot.
      const ty = y(opts.target);
      svg.append(node("text", { x: L + 4, y: ty > T + 14 ? ty - 4 : ty + 12, class: "pc-label" },
        `your target ${C.formatINR(opts.target)}`));
    }

    // A price holds until it changes, so the line steps rather than slopes.
    let d = `M${x(pts[0].t).toFixed(1)},${y(pts[0].p).toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      d += ` H${x(pts[i].t).toFixed(1)} V${y(pts[i].p).toFixed(1)}`;
    }
    svg.append(node("path", { d, class: "pc-line" }));

    const low = pts.reduce((a, b) => (b.p < a.p ? b : a));
    const last = pts[pts.length - 1];
    svg.append(node("circle", { cx: x(low.t), cy: y(low.p), r: 4, class: "pc-dot pc-dot-low" }));
    if (low !== last) {
      const lx = Math.min(Math.max(x(low.t), L + 30), W - R - 30);
      svg.append(node("text", { x: lx, y: Math.min(y(low.p) + 16, H - B - 3), "text-anchor": "middle", class: "pc-label" },
        `low ${C.formatINR(low.p)}`));
    }
    svg.append(node("circle", { cx: x(last.t), cy: y(last.p), r: 4, class: "pc-dot" }));

    // Hover / keyboard layer: the whole plot is the target, snapping to the
    // nearest reading.
    const hair = node("line", { x1: 0, x2: 0, y1: T, y2: H - B, class: "pc-hair", visibility: "hidden" });
    const ring = node("circle", { cx: 0, cy: 0, r: 5, class: "pc-dot pc-ring", visibility: "hidden" });
    const hit = node("rect", { x: L, y: 0, width: W - L - R, height: H - B, fill: "transparent", tabindex: "0",
                               role: "img", "aria-label": `Price history: ${pts.length} readings from ${day(t0)}` });
    svg.append(hair, ring, hit);
    let idx = pts.length - 1;
    function show(i) {
      idx = Math.max(0, Math.min(pts.length - 1, i));
      const p = pts[idx];
      hair.setAttribute("x1", x(p.t)); hair.setAttribute("x2", x(p.t));
      ring.setAttribute("cx", x(p.t)); ring.setAttribute("cy", y(p.p));
      hair.setAttribute("visibility", "visible"); ring.setAttribute("visibility", "visible");
      if (opts.tooltip) {
        const tip = opts.tooltip;
        tip.replaceChildren();
        const v = document.createElement("strong");
        v.textContent = C.formatINR(p.p);
        tip.append(v, document.createTextNode(` ${day(p.t)} · ${p.src === "community" ? "community" : "you saw it"}`));
        tip.hidden = false;
      }
    }
    function hide() {
      hair.setAttribute("visibility", "hidden"); ring.setAttribute("visibility", "hidden");
      if (opts.tooltip) opts.tooltip.hidden = true;
    }
    hit.addEventListener("pointermove", (e) => {
      const r = svg.getBoundingClientRect();
      const vx = ((e.clientX - r.left) / r.width) * W;
      let best = 0;
      for (let i = 1; i < pts.length; i++) if (Math.abs(x(pts[i].t) - vx) < Math.abs(x(pts[best].t) - vx)) best = i;
      show(best);
    });
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("focus", () => show(idx));
    hit.addEventListener("blur", hide);
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { show(idx - 1); e.preventDefault(); }
      if (e.key === "ArrowRight") { show(idx + 1); e.preventDefault(); }
    });
    svg.setAttribute("aria-label", `Price from ${day(t0)} to ${day(last.t)}: lowest ${C.formatINR(low.p)}, now ${C.formatINR(last.p)}`);
    return pts;
  }

  /** Shared chart colours, for pages that inline them (the on-page panel). */
  const CSS = `
  .pc-line { fill: none; stroke: #3538cd; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .pc-axis { stroke: #d3d9e8; stroke-width: 1; }
  .pc-hair { stroke: #5d6781; stroke-width: 1; }
  .pc-target { stroke: #5d6781; stroke-width: 1.5; stroke-dasharray: 4 3; }
  .pc-label { fill: #5d6781; font: 10px system-ui, sans-serif; }
  .pc-dot { fill: #3538cd; stroke: #ffffff; stroke-width: 2; }
  .pc-dot-low { fill: #ffffff; stroke: #3538cd; }
  .pc-ring { fill: none; stroke: #3538cd; }
  @media (prefers-color-scheme: dark) {
    .pc-line { stroke: #7a7ff0; } .pc-axis { stroke: #2e3a63; } .pc-hair, .pc-target { stroke: #8c97b5; }
    .pc-label { fill: #8c97b5; } .pc-dot { fill: #7a7ff0; stroke: #131b35; }
    .pc-dot-low { fill: #131b35; stroke: #7a7ff0; } .pc-ring { stroke: #7a7ff0; }
  }`;

  root.UlteriorChart = { draw, CSS };
})(globalThis);
