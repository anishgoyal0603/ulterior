/**
 * Ulterior extension -- drip pricing and bait-and-switch across a booking.
 *
 * Neither is visible on one page. The cheap price is on the listing; the fees
 * appear three screens later. So the extension remembers the price of the
 * thing you actually chose -- the card around the "Book" or "Select" button
 * you pressed, or the product page you were on -- and when a page with a
 * total turns up, it compares the two.
 *
 * What is remembered: the price, the time, and a scrambled fingerprint of the
 * product's name (to find the same item in the cart). It is held in session
 * memory by the background script, keyed by site, gone when the browser
 * closes, and readable only by pages of that same site.
 */
(function (root) {
  "use strict";
  const C = root.UlteriorCore, D = root.UlteriorDetect, P = root.UlteriorPlatform;
  const SITE = C.siteOf(location.hostname);
  const KEY = "journey:" + SITE;
  // Path, query and hash together name the page: query- and hash-routed
  // shops change only those between two products.
  const MAX_AGE_MS = 3 * 3600 * 1000;
  const NOT_PRICE = /\b(m\.?r\.?p|save|saving|off\b|emi|cashback|per\s+month)\b/i;

  async function getJourney() {
    const j = await P.Session.get(KEY);
    return j && Date.now() - j.at < MAX_AGE_MS ? j : null;
  }

  function setJourney(j) { P.Session.set(KEY, j); }

  function cardFor(target) {
    let n = target;
    for (let i = 0; n && i < 8; i++, n = n.parentElement) {
      const t = D.textOf(n);
      if (t.length > 1500) break;
      const amounts = D.amountNodes(n, 40).filter((a) => !a.struck && a.value > 0 && !NOT_PRICE.test(a.text));
      if (!amounts.length) continue;
      let best = amounts[0];
      for (const a of amounts) if (a.fontSize > best.fontSize + 0.5) best = a;
      const name = t.replace(/(-|−)?\s*(₹|rs\.?|inr)\s*[\d,.]+/gi, " ").slice(0, 160);
      return { value: best.value, disclosed: C.disclosesCharges(t), fp: C.fingerprint(name) };
    }
    return null;
  }

  function productName() {
    const og = document.querySelector('meta[property="og:title"]');
    const h1 = document.querySelector("h1");
    return (h1 && D.textOf(h1)) || (og && og.content) || document.title || "";
  }

  function onClick(e) {
    if (!e.isTrusted) return;
    const btn = e.target && e.target.closest && e.target.closest(D.CLICKABLE);
    if (!btn) return;
    const t = D.clickableText(btn);
    if (!t || t.length > 40 || !C.BUY_VERB_RE.test(t) || C.PAY_BUTTON_RE.test(t)) return;
    const last = root.__ulteriorLastScan;
    // On the cart or checkout we compare, not record: "Proceed to checkout"
    // under a "Subtotal ₹348" used to replace the ₹299 the shopper picked
    // with the subtotal, which can already include the fees.
    if ((last && last.isCheckout) || D.looksLikeCartOrCheckout()) return;

    let price = null, source = "the option you picked", disclosed = false, fp = [];
    const onButton = C.parseAmounts(t).filter((a) => a.value > 0);
    if (onButton.length) {
      // "Book 2 tickets - ₹500": the button states the amount itself.
      price = onButton[onButton.length - 1].value;
      disclosed = C.disclosesCharges(t);
      fp = last && last.productPrice ? C.fingerprint(productName()) : [];
    } else if (last && last.productPrice) {
      price = last.productPrice;
      source = "the product page";
      disclosed = !!last.productDisclosed;
      fp = C.fingerprint(productName());
    } else {
      const card = cardFor(btn);
      if (card) { price = card.value; disclosed = card.disclosed; fp = card.fp; }
    }
    if (!price) return;
    // A "Proceed - ₹500" button after picking two seats already carries the
    // whole amount; a "Book" on a fare card is per person.
    const perPerson = !/\b(continue|proceed)\b/i.test(t) && onButton.length === 0
                      && source !== "the product page";
    setJourney({ price, source, perPerson, disclosed, fp, at: Date.now(), path: (location.pathname + location.search + location.hash),
                 label: C.clip(C.redact(t), 30) });
  }

  /** Called on every scan of a product page. */
  async function noteProductPage(price, disclosed) {
    const j = await getJourney();
    if (j && j.path === (location.pathname + location.search + location.hash) && j.source === "the option you picked") return;
    if (j && j.path === (location.pathname + location.search + location.hash) && j.price === price) return;
    setJourney({ price, source: "the product page", perPerson: false, disclosed: !!disclosed,
                 fp: C.fingerprint(productName()), at: Date.now(), path: (location.pathname + location.search + location.hash), label: "" });
  }

  function timeOf(ms) {
    return new Date(ms).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  }

  /**
   * DP-07: the cart line for the product you picked charges more for it than
   * the price you picked it at. Only when the cart line clearly names the
   * same product (most of its words), and per unit when a quantity is shown.
   */
  function baitAndSwitch(j) {
    if (!j.fp || j.fp.length < 3) return null;
    const line = D.lineItemFor(j.fp);
    if (!line) return null;
    const unit = line.value / (line.qty || 1);
    if (unit <= j.price * 1.02 + 1) return null;
    const strong = line.score >= 0.8;
    return {
      code: "DP-07", tier: strong ? "corroborated" : "indicative",
      amountValue: (unit - j.price) * (line.qty || 1),
      title: `The cart charges ${C.formatINR(unit)} for the item you picked at ${C.formatINR(j.price)}`,
      detail: `"${C.clip(C.redact(line.text), 90)}" - ${C.formatINR(unit - j.price)} more per item than when you ` +
        `chose it${strong ? "" : ". If you changed the size, colour or seller, this may be why"}.`,
      evidence: {
        price_when_you_picked: `${C.formatINR(j.price)} (from ${j.source}, ${timeOf(j.at)})`,
        price_in_cart: `${C.formatINR(unit)} per item`,
        cart_line: C.clip(C.redact(line.text), 120),
        name_match: `${Math.round(line.score * 100)}% of the product's words`,
      },
    };
  }

  /** Compare the remembered price with this checkout. */
  async function evaluate(scan) {
    const out = { findings: [], notes: [], summary: null };
    if (!scan.isCheckout || !scan.total) return out;
    const fees = scan.fees || [];
    const feeText = fees.map((f) => `${f.label} ${C.formatINR(f.value)}`).join("; ");
    const j = await getJourney();

    if (!j) {
      if (fees.length) {
        out.notes.push({ kind: "fees", text: `This step lists fees (${feeText}). Ulterior did not see the ` +
          "price you started from on this site, so it cannot tell whether they were shown earlier." });
      }
      return out;
    }

    const bait = baitAndSwitch(j);
    if (bait) out.findings.push(bait);

    const q = scan.quantities || { persons: 0, items: 0 };
    // Fare cards are priced per person; a product page per item; a button
    // that states its own amount already covers everyone.
    let qty = 1;
    if (j.perPerson && q.persons) qty = q.persons;
    else if (j.source === "the product page" && q.items) qty = q.items;
    const explained = (scan.addOns || 0) + (bait ? bait.amountValue : 0);
    const verdict = C.judgeJourney(j, scan.total.value, qty, fees, explained);

    out.summary = {
      start: C.formatINR(j.price), source: j.source, at: timeOf(j.at), qty,
      expected: C.formatINR(verdict.expected), total: C.formatINR(scan.total.value),
      fees: feeText, kind: verdict.kind,
    };

    const several = j.source === "the product page" && q.items > 1 && !q.persons;
    if (verdict.kind === "match") {
      out.notes.push({ kind: "ok", text: `The total (${C.formatINR(scan.total.value)}) matches the price you ` +
        `picked${qty > 1 ? ` for ${qty}` : ""} (${C.formatINR(verdict.expected)})` +
        (verdict.addOnSum ? " plus the items flagged above." : ". No price was added along the way.") });
    } else if (several) {
      out.notes.push({ kind: "info", text: "Several items are in this cart, so Ulterior does not compare the " +
        "total with one product's price." });
    } else if (verdict.kind === "disclosed") {
      out.notes.push({ kind: "info", text: `The total is ${C.formatINR(verdict.diff)} above the price you ` +
        "picked, but fees were mentioned next to that price when you chose it." });
    } else if (verdict.kind === "drip") {
      const per = qty > 1 ? ` x ${qty}` : "";
      out.findings.push({
        code: "DP-08", tier: "corroborated",
        title: `You picked this at ${C.formatINR(j.price)}${per}; the total is now ${C.formatINR(scan.total.value)}`,
        detail: `${C.formatINR(verdict.feeSum)} of the ${C.formatINR(verdict.diff)} difference is fees that first ` +
          `appear at this step: ${feeText}.`,
        evidence: {
          price_when_you_picked: `${C.formatINR(j.price)} (from ${j.source}, ${timeOf(j.at)})`,
          people_or_quantity: String(qty),
          expected_total: C.formatINR(verdict.expected),
          total_at_this_step: `${C.formatINR(scan.total.value)} ("${scan.total.label}")`,
          fees_first_shown_here: feeText,
        },
      });
    } else {
      out.notes.push({ kind: "info", text: `The total (${C.formatINR(scan.total.value)}) is ` +
        `${C.formatINR(verdict.diff)} more than the ${C.formatINR(verdict.expected)} you picked. Ulterior could not ` +
        "match that to named fees - it may be taxes or something you added. Check the price breakup." });
    }
    return out;
  }

  document.addEventListener("click", onClick, true);
  root.UlteriorJourney = { evaluate, noteProductPage, getJourney, productName, KEY };
})(globalThis);
