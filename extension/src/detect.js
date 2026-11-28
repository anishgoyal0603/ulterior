/**
 * Ulterior extension -- what can be decided from the page in front of you.
 *
 * Each detector returns findings (shown, counted on the badge) or notes
 * (shown, never counted). The line between them is the evidence tier:
 *
 *   provable      the page's own state says so (a paid add-on ticked before
 *                 you touched anything)
 *   corroborated  two independent observations agree (a price you picked and
 *                 a total with named fees; a countdown that restarted)
 *   indicative    consistent with the pattern; worth a human look
 *
 * Anything weaker -- "Only 2 left", which nobody can verify from a browser --
 * is a note. A badge that lights up on every product page is a badge people
 * learn to ignore.
 */
(function (root) {
  "use strict";
  const C = root.UlteriorCore;

  // ------------------------------------------------------------- helpers
  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    // 1x1 boxes are "visually hidden" text for screen readers (a common way
    // shops repeat a price off-screen). A person never sees them.
    if (r.width <= 1 || r.height <= 1) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none" && parseFloat(cs.opacity || "1") > 0.05;
  }

  /** Cheap text for filtering: textContent needs no layout. innerText (in
   *  textOf) is what a person sees, but on a page with ten thousand elements
   *  asking every one of them for it would make each scan crawl. So filter on
   *  this first, then confirm with textOf on the few that pass. */
  function fast(el) {
    return ((el && el.textContent) || "").normalize("NFC").replace(/\s+/g, " ").trim();
  }

  function textOf(el) {
    return C.normalizeQuotes((el && (el.innerText || el.textContent)) || "").replace(/\s+/g, " ").trim();
  }

  function isStruck(el) {
    for (let n = el, i = 0; n && i < 3; n = n.parentElement, i++) {
      if (/^(S|DEL|STRIKE)$/.test(n.tagName)) return true;
      const deco = getComputedStyle(n).textDecorationLine || getComputedStyle(n).textDecoration || "";
      if (deco.includes("line-through")) return true;
    }
    return false;
  }

  const MONEY_HINT = /(₹|\brs\.?\s*\d|\binr\s*\d|\u0930\u0941)/i;
  const NOT_A_PRICE_CONTEXT = /\b(m\.?r\.?p|save|saving|off\b|discount|emi|per\s+month|\/\s*mo|cashback|you\s+save|was\b|earn)\b/i;

  /**
   * Every visible money amount on the page (or inside `scope`), with the
   * element that carries it and how big it is drawn. Walks text nodes, so it
   * finds "₹4,599" whether it sits in a <span>, a <b> or a bare <div>.
   */
  function amountNodes(scope, limit) {
    const out = [];
    const rootEl = scope || document.body;
    if (!rootEl) return out;
    const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        return /\d/.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const seen = new Set();
    let n, visited = 0;
    while ((n = walker.nextNode()) && visited < 4000) {
      visited++;
      let el = n.parentElement;
      if (!el) continue;
      // Read the amount from the text node itself when it carries one. Its
      // element's text can run several prices together -- innerText of
      // <p>₹299<s>₹899</s><span>67% off</span></p> is "₹299₹89967% off",
      // which would parse as ₹89,967.
      const digitsEl = el;
      let txt = C.normalizeQuotes(n.nodeValue).replace(/\s+/g, " ").trim();
      let key = n;
      if (!MONEY_HINT.test(txt)) {
        // The currency symbol is in its own element, sometimes two levels
        // away: <span>₹</span><span>1,299<span>.</span></span><span>00</span>.
        // Climb to the nearest ancestor whose whole text is a short price.
        let up = el, found = null;
        for (let i = 0; up && i < 3; i++, up = up.parentElement) {
          const f = fast(up);
          if (f.length > 30) break;
          if (MONEY_HINT.test(f)) { found = up; break; }
        }
        if (!found) continue;
        el = found;
        txt = textOf(el) || fast(el);
        key = el;
      }
      if (seen.has(key) || txt.length > 60 || !MONEY_HINT.test(txt)) continue;
      seen.add(key);
      const amounts = C.parseAmounts(txt);
      if (!amounts.length || !isVisible(el) || !isVisible(digitsEl)) continue;
      const r = el.getBoundingClientRect();
      out.push({
        el, text: txt, value: amounts[amounts.length - 1].value,
        // The size the DIGITS are drawn at -- what a shopper reads as "big".
        fontSize: parseFloat(getComputedStyle(digitsEl).fontSize) || 0,
        top: r.top + (window.scrollY || 0), struck: isStruck(el),
      });
      if (out.length >= (limit || 400)) break;
    }
    return out;
  }

  function clickableText(el) {
    if (el.tagName === "INPUT") return C.normalizeQuotes(el.value || "").trim();
    return textOf(el);
  }

  const CLICKABLE = "button, a, [role=button], input[type=submit], input[type=button]";

  function buyButtons() {
    const out = [];
    document.querySelectorAll(CLICKABLE).forEach((el) => {
      if (el.tagName !== "INPUT") {
        const f = fast(el);
        if (!f || f.length > 80 || !C.BUY_VERB_RE.test(f)) return;
      }
      const t = clickableText(el);
      if (t && t.length <= 40 && C.BUY_VERB_RE.test(t) && !C.PAY_BUTTON_RE.test(t) && isVisible(el)) out.push(el);
    });
    return out;
  }

  // ------------------------------------------------------- price on a page
  function jsonLdPrice() {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try { data = JSON.parse(s.textContent); } catch (e) { continue; }
      const stack = [data];
      while (stack.length) {
        const d = stack.pop();
        if (!d || typeof d !== "object") continue;
        if (Array.isArray(d)) { stack.push(...d); continue; }
        if (d["@graph"]) stack.push(d["@graph"]);
        const offers = d.offers;
        if (offers) {
          const list = Array.isArray(offers) ? offers : [offers];
          for (const o of list) {
            // "1,299.00" is 1299, not 1: parseFloat stops at the comma.
            const p = parseFloat(String(o && (o.price || o.lowPrice) || "").replace(/,/g, ""));
            if (isFinite(p) && p > 0) return p;
          }
        }
      }
    }
    return null;
  }

  /** The price a shopper would read as "the price" of this product: the
   *  biggest amount near the top that is not a struck-out MRP or a saving. */
  function prominentPrice() {
    // Structured data can lag the page (a sale price shown, the list price in
    // JSON-LD), so it is used only when the same amount is visibly on screen.
    const ld = jsonLdPrice();
    const shown = amountNodes(null, 300);
    if (ld) {
      const match = shown.find((a) => !a.struck && Math.abs(a.value - ld) < 0.01);
      if (match) return { value: ld, how: "the product page's own structured data", el: match.el, mrp: mrpNear(match, shown) };
    }
    let best = null;
    for (const a of shown) {
      if (a.struck || a.value <= 0 || a.top > 1300) continue;
      // Judge an amount by its own words and the label just before it
      // ("M.R.P.: ₹2,499", "You save ₹1,200") -- not by the whole line, which
      // on a real product page holds the price AND the MRP AND the saving.
      // The text just before it: the previous text node AND the previous
      // element. previousSibling alone is usually the whitespace between
      // "<span>EMI from</span> <strong>₹105</strong>", which hid the label.
      const prevText = a.el.previousSibling ? (a.el.previousSibling.textContent || "") : "";
      const prevEl = a.el.previousElementSibling ? (a.el.previousElementSibling.textContent || "") : "";
      const before = C.normalizeQuotes(prevEl + " " + prevText).trim().slice(-30);
      if (NOT_A_PRICE_CONTEXT.test(a.text) || NOT_A_PRICE_CONTEXT.test(before)) continue;
      if (!best || a.fontSize > best.fontSize + 0.5) best = a;
    }
    return best ? { value: best.value, how: "the largest price shown at the top of the page", el: best.el,
                    mrp: mrpNear(best, shown) } : null;
  }

  /** The "M.R.P." a price is shown against: a struck-through amount, or one
   *  labelled MRP, close to the price (same block) and higher than it. */
  function mrpNear(price, shown) {
    let box = price.el;
    for (let i = 0; box && i < 3; i++) {
      if (box.parentElement && fast(box.parentElement).length <= 300) box = box.parentElement; else break;
    }
    let mrp = null;
    for (const a of shown) {
      if (a === price || a.value <= price.value * 1.02 || !box.contains(a.el)) continue;
      const prev = a.el.previousSibling;
      const before = prev ? (prev.textContent || "").slice(-20) : "";
      if (a.struck || /m\.?\s*r\.?\s*p/i.test(a.text + " " + before)) mrp = Math.max(mrp || 0, a.value);
    }
    return mrp;
  }

  /** The amount that belongs to a label: the label's own text, or the row it
   *  sits in -- the last amount in the row that is not struck through. */
  // A condition inside a fee label is not the fee: "Delivery fee (Free above
  // ₹499)" next to "FREE", or "Small cart fee (orders under ₹199)" next to
  // "₹20". Reading the label first returned 499 and 199.
  // A COMPARATOR is required ("above", "under", "min"...). "Order" alone is
  // not a condition: it is the first word of "Order Total ₹457" and "Order
  // processing fee ₹49", and matching it erased the amount itself.
  const THRESHOLD_CLAUSE_RE = /(\(|\b)(free\s+)?(?:(?:on\s+)?(?:orders?|cart|basket)\s+(?:value\s+)?)?(above|over|under|below|upto|up\s*to|min(imum)?|less\s+than|more\s+than|worth|till|until)\b[^)\n]{0,30}?(₹|rs\.?|inr)\s*[\d,]+(\.\d+)?\)?/gi;

  function amountForLabel(labelEl) {
    // The label's own amount ("Total ₹457"), unless it is only a condition.
    const own = C.parseAmounts(textOf(labelEl).replace(THRESHOLD_CLAUSE_RE, " "));
    if (own.length) return own[own.length - 1].value;
    let row = labelEl.parentElement;
    for (let i = 0; row && i < 3; i++, row = row.parentElement) {
      const t = textOf(row);
      if (t.length > 200) break;
      const free = /\b(free|waived)\b/i.test(t.replace(THRESHOLD_CLAUSE_RE, " "));
      // A value cell can hold a condition too: "FREE (on orders above ₹499)".
      const nodes = amountNodes(row, 20).filter((a) => !a.struck && !labelEl.contains(a.el)
        && C.parseAmounts(String(a.text || "").replace(THRESHOLD_CLAUSE_RE, " ")).length > 0);
      if (nodes.length) {
        if (free && nodes.every((a) => a.value <= 0)) return 0;
        return nodes[nodes.length - 1].value;
      }
      if (free) return 0;
    }
    return null;
  }

  const LABEL_TAGS = "td, th, dt, dd, span, div, p, label, strong, b, li, h1, h2, h3, h4, h5, h6";

  function shortLabelElements(re, maxLen) {
    const out = [];
    for (const el of document.querySelectorAll(LABEL_TAGS)) {
      if (el.children.length > 3) continue;
      const f = fast(el);
      if (!f || f.length > maxLen * 2 || !re.test(f)) continue;
      const t = textOf(el);
      if (!t || t.length > maxLen || !re.test(t) || !isVisible(el)) continue;
      // Skip a wrapper whose child already matched: keep the innermost.
      if ([...el.children].some((c) => re.test(textOf(c)))) continue;
      out.push({ el, text: t });
    }
    return out;
  }

  function findTotal() {
    let best = null;
    C.TOTAL_LABELS.forEach((re, priority) => {
      if (best && best.priority < priority) return;
      for (const { el, text } of shortLabelElements(re, 48)) {
        if (C.FEE_LABEL_RE.test(text)) continue;
        const v = amountForLabel(el);
        if (v === null || v <= 0) continue;
        if (!best || priority < best.priority || (priority === best.priority && v > best.value)) {
          best = { value: v, label: C.clip(text, 40), priority, el };
        }
      }
    });
    if (best) return best;
    for (const el of document.querySelectorAll(CLICKABLE)) {
      if (el.tagName !== "INPUT" && !C.PAY_BUTTON_RE.test(fast(el))) continue;
      const t = clickableText(el);
      if (t.length <= 50 && C.PAY_BUTTON_RE.test(t) && isVisible(el)) {
        const a = C.parseAmounts(t);
        if (a.length && a[a.length - 1].value > 0) {
          return { value: a[a.length - 1].value, label: C.clip(t, 40), priority: 99 };
        }
      }
    }
    return null;
  }

  function findFees() {
    const fees = [];
    const seen = new Set();
    for (const { el, text } of shortLabelElements(C.FEE_LABEL_RE, 70)) {
      if (C.TOTAL_LABELS.some((re) => re.test(text))) continue;
      const v = amountForLabel(el);
      if (v === null || v <= 0) continue;
      // The label without its amount: "Convenience fee ₹350" -> "Convenience fee".
      const label = C.clip(text.replace(/(-|−)?\s*(₹|(?<![A-Za-z])(?:rs\.?|inr))\s*[\d,.]+(\s*\/-)?/gi, "")
                               .replace(/[:\s-]+$/, "").trim(), 40);
      const key = label.toLowerCase() + "|" + v;
      if (seen.has(key)) continue;
      seen.add(key);
      fees.push({ label, value: v });
    }
    return fees;
  }

  const CHECKOUT_WORDS = /\b(checkout|payment|pay|review|summary|cart|basket|bag|order|booking|confirm)\b/i;

  /** Cart, bag and checkout pages are never product pages, even when they
   *  carry one "Proceed" button and one price that stands out. */
  function looksLikeCartOrCheckout() {
    const where = `${location.pathname} ${document.title} ` +
      [...document.querySelectorAll("h1")].slice(0, 3).map(fast).join(" ");
    return /\b(checkout|payment|cart|basket|bag|summary|review\s+your)\b/i.test(where);
  }

  function isCheckoutPage(total) {
    if (!total) return false;
    if (total.priority === 99) return true;
    const where = `${location.pathname} ${document.title} ` +
      [...document.querySelectorAll("h1, h2")].slice(0, 6).map(textOf).join(" ");
    if (CHECKOUT_WORDS.test(where)) return true;
    return [...document.querySelectorAll(CLICKABLE)].some((el) => {
      if (el.tagName !== "INPUT" && !C.PAY_BUTTON_RE.test(fast(el))) return false;
      const t = clickableText(el);
      return t.length <= 50 && C.PAY_BUTTON_RE.test(t) && isVisible(el);
    });
  }

  /** How many people / items this order is for.
   *
   *  Read from the order summary (the block around the total), not the whole
   *  page, and never from scarcity, promotion or capacity wording. "Only 3
   *  seats left" on a 1-adult flight made the expected price 3 x the fare,
   *  so a ₹549 drip "matched", and "Buy 3, get 10% off" on a 1-item cart
   *  switched the drip comparison off. */
  function quantities(total) {
    let scope = document.body;
    if (total && total.el) {
      let el = total.el;
      for (let i = 0; el && el !== document.body && i < 6; i++, el = el.parentElement) {
        scope = el;
        if (fast(el).length >= 400) break;
      }
    }
    const body = textOf(scope).slice(0, 20000);
    // Not "for" or "of": "Base fare for 2 Adults" and "Price of 3 items" ARE
    // the count. "for 2+ guests" / "for 3 or more" are caught by NOT_AFTER.
    const NOT_BEFORE = /\b(only|just|up\s*to|upto|max(imum)?|min(imum)?|buy|over|under|above|below|at\s*least|any|get)\s*$/i;
    const NOT_AFTER = /^\s*(\+|or\s+more|left|remaining|available|in\s+stock|max|maximum)/i;
    const isCount = (m, re) => {
      const before = body.slice(Math.max(0, m.index - 16), m.index);
      const after = body.slice(m.index + m[0].length, m.index + m[0].length + 20);
      return !NOT_BEFORE.test(before) && !NOT_AFTER.test(after);
    };
    let persons = 0, items = 0, m;
    C.QTY_RE.lastIndex = 0;
    while ((m = C.QTY_RE.exec(body)) !== null) {
      const n = +m[1];
      if (n > 0 && n <= 9 && isCount(m)) persons = Math.max(persons, n);
    }
    C.ITEM_COUNT_RE.lastIndex = 0;
    while ((m = C.ITEM_COUNT_RE.exec(body)) !== null) {
      const n = +(m[1] || m[2] || m[3]);
      if (n > 0 && n <= 20 && isCount(m)) items = Math.max(items, n);
    }
    return { persons, items };
  }

  // --------------------------------------------------- pre-selected items
  function labelTextFor(input) {
    if (input.getAttribute && input.getAttribute("aria-label")) return C.normalizeQuotes(input.getAttribute("aria-label"));
    if (input.id) {
      try {
        const explicit = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
        if (explicit) return textOf(explicit);
      } catch (e) { /* odd id, fall through */ }
    }
    const wrap = input.closest && input.closest("label");
    if (wrap) return textOf(wrap);
    // A drawn checkbox (<div role=checkbox>) often has no text of its own;
    // its words are beside it, so fall through to the parent when empty.
    if (input.getAttribute && input.getAttribute("role") && textOf(input)) return textOf(input);
    let p = input.parentElement;
    for (let i = 0; p && i < 2; i++, p = p.parentElement) {
      const t = textOf(p);
      if (t) return t.length > 200 ? t.slice(0, 200) : t;
    }
    return "";
  }

  function isChecked(el) {
    if (el.tagName === "INPUT") return el.checked;
    return el.getAttribute("aria-checked") === "true";
  }

  function choiceControls() {
    return document.querySelectorAll(
      "input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]");
  }

  const DECLINE_CHOICE_RE = /\b(no|don'?t|do not|skip|without|not\s+(now|required|needed)|i'?ll\s+take|none|free)\b/i;
  // Paying for ONE purchase in instalments is not a subscription
  // (kept in step with EMI_RE in detectors/vocab.py).
  const EMI_RE = /\b(emi|emis|instal+ments?|pay\s*later|no[\s-]cost)\b/i;

  function groupHasDecline(input) {
    const name = input.getAttribute("name");
    let peers = [];
    if (name) {
      const scope = input.form || document;
      peers = [...scope.querySelectorAll(`input[type=radio][name="${CSS.escape(name)}"]`)];
    } else if (input.getAttribute("role") === "radio") {
      const group = input.closest("[role=radiogroup]") || input.parentElement && input.parentElement.parentElement;
      if (group) peers = [...group.querySelectorAll("[role=radio]")];
    }
    return peers.some((p) => p !== input && DECLINE_CHOICE_RE.test(labelTextFor(p)));
  }

  function preselectedFindings(state) {
    const findings = [];
    choiceControls().forEach((el) => {
      if (!state.firstChecked.has(el)) state.firstChecked.set(el, isChecked(el));
      if (state.touched.has(el) || !state.firstChecked.get(el) || !isChecked(el)) return;
      if (!isVisible(el) && !(el.tagName === "INPUT" && el.closest("label") && isVisible(el.closest("label")))) return;
      const label = labelTextFor(el);
      if (!label) return;
      const amounts = C.parseAmounts(label).filter((a) => a.value > 0);
      const isRadio = el.type === "radio" || el.getAttribute("role") === "radio";

      // Preferences and benefits first: a wallet balance or "notify me" is
      // never a charge. But generic words in BENEFIT_RE ("agree", "apply",
      // "terms", "save") do not cancel a priced paid extra or a priced
      // recurring plan: "Extended Warranty ₹299 (T&C apply)" and "I agree to
      // Auto-Renew ₹149/month" are charges (as on the server).
      const recurringWords = C.RECURRING_BILLING_PATTERNS.some((p) => p.test(label)) && !EMI_RE.test(label);
      const pricedCharge = amounts.length > 0 && (C.PAID_ADDON_RE.test(label) || recurringWords);
      if (C.BENEFIT_RE.test(label) && !pricedCharge) return;
      // A radio is one of a REQUIRED choice. Its default is only the shop's
      // choice made for you when the group offers an explicit "no" or a free
      // option -- not when it picks between Monthly and Annual, or between
      // two ways to pay.
      if (isRadio && !groupHasDecline(el)) return;
      if (recurringWords && amounts.length) {
        findings.push({
          code: "DP-12", tier: "provable",
          title: "A repeating charge was already selected for you",
          detail: `Selected before you touched anything: "${C.clip(C.redact(label), 110)}"`,
          evidence: { option: C.clip(C.redact(label), 110), state: "selected when the page loaded" },
        });
        return;
      }
      // A free or ₹0 extra adds nothing to the bill ("Free Gift Wrap (₹0)",
      // "Complimentary 1-year Warranty"), as on the server.
      if (!amounts.length && (C.parseAmounts(label).some((a) => a.value === 0)
                              || /\b(free|complimentary|included|no\s+charge)\b/i.test(label))) return;
      if (!(C.PAID_ADDON_RE.test(label) || amounts.length)) return;
      const ev = { add_on: C.clip(C.redact(label), 110), state: "selected when the page loaded, before you touched it" };
      if (amounts.length) ev.price = C.formatINR(amounts[0].value);
      findings.push({
        code: "DP-02", tier: "provable", amountValue: amounts.length ? amounts[0].value : 0,
        title: "A paid add-on was already selected for you",
        detail: `"${C.clip(C.redact(label), 110)}"` + (amounts.length ? ` adds ${C.formatINR(amounts[0].value)}.` : "."),
        evidence: ev,
      });
    });
    return findings;
  }

  function trickWordingFindings(state) {
    const findings = [];
    choiceControls().forEach((el) => {
      const label = labelTextFor(el);
      if (!label || label.length > 300) return;
      if (C.DOUBLE_NEGATIVE_PATTERNS.some((p) => p.test(label))) {
        findings.push({
          code: "DP-11", tier: "indicative",
          title: "Double negative in a choice",
          detail: `You have to do something negative to say no: "${C.clip(C.redact(label), 110)}"`,
          evidence: { wording: C.clip(C.redact(label), 110) },
        });
      } else if (state.firstChecked.get(el) && !state.touched.has(el) && isChecked(el)
                 && C.OPT_OUT_LABEL_PATTERNS.some((p) => p.test(label))) {
        findings.push({
          code: "DP-11", tier: "corroborated",
          title: "Ticked for you, but worded as an opt-out",
          detail: `The tick and the words point opposite ways: "${C.clip(C.redact(label), 110)}"`,
          evidence: { wording: C.clip(C.redact(label), 110), state: "ticked when the page loaded" },
        });
      }
    });
    return findings;
  }

  // ---------------------------------------------------- confirm shaming
  function confirmShamingFindings() {
    const findings = [];
    const seen = new Set();
    document.querySelectorAll(CLICKABLE + ", label").forEach((el) => {
      if (el.tagName !== "INPUT") {
        const f = C.normalizeQuotes(fast(el));
        if (f.length < 8 || f.length > 320 || !C.isShaming(f)) return;
      }
      const t = clickableText(el);
      if (t.length < 8 || t.length > 160 || seen.has(t)) return;
      if (!C.isShaming(t) || !isVisible(el)) return;
      seen.add(t);
      findings.push({
        code: "DP-03", tier: "indicative",
        title: "Saying no is worded to make you feel bad",
        detail: `"${C.clip(C.redact(t), 120)}"`,
        evidence: { decline_wording: C.clip(C.redact(t), 120) },
      });
    });
    return findings;
  }

  // ------------------------------------------- interface interference
  /** CSS opacity on the element and every ancestor multiplies. Text faded
   *  with opacity: .2 (Tailwind "opacity-20") keeps color rgb(0,0,0), and
   *  was scored at 21:1 although it is barely visible. */
  function effectiveOpacity(el) {
    let o = 1;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const v = parseFloat(getComputedStyle(n).opacity);
      if (isFinite(v)) o *= v;
    }
    return Math.max(0, Math.min(1, o));
  }

  /** Contrast of el's text against what is behind it, as it is SEEN. */
  function seenContrast(el, fg) {
    const bg = effectiveBackground(el);
    const faded = { r: fg.r, g: fg.g, b: fg.b, a: (fg.a === undefined ? 1 : fg.a) * effectiveOpacity(el) };
    return C.contrastRatio(C.over(faded, bg), bg);
  }

  function effectiveBackground(el) {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const c = C.parseColor(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) {
        layers.push(c);
        if (c.a >= 0.999) break;
      }
    }
    let bg = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i--) bg = C.over(layers[i], bg);
    return bg;
  }

  const CLOSE_CONTROL_RE = /^([x×✕✖✗]|close|dismiss)$/i;

  function interferenceFindings() {
    const findings = [];
    const seen = new Set();
    for (const decline of document.querySelectorAll(CLICKABLE)) {
      if (decline.tagName !== "INPUT" && !C.DECLINE_RE.test(C.normalizeQuotes(fast(decline)))) continue;
      const dt = clickableText(decline);
      if (!dt || dt.length > 80 || !C.DECLINE_RE.test(dt) || !isVisible(decline)) continue;
      // The container is the one where the accept option was FOUND. The loop
      // used to step to the parent once more after finding it, so the offer
      // text was read one level too high (or from nothing near <body>).
      let container = decline.parentElement, accept = null;
      // The accept option is the LARGEST other call to action in the box,
      // never a close icon: in "× ... Yes, protect ... no thanks" the first
      // clickable in DOM order is the ×, which made the size comparison
      // meaningless and printed "×" as the accept option.
      for (let i = 0; container && i < 5; i++) {
        let bestArea = 0;
        for (const cand of container.querySelectorAll(CLICKABLE)) {
          if (cand === decline || cand.contains(decline) || decline.contains(cand)) continue;
          const at = clickableText(cand);
          if (!at || at.length > 60 || C.DECLINE_RE.test(at) || !isVisible(cand)) continue;
          if (CLOSE_CONTROL_RE.test(at.trim()) || /\b(close|dismiss)\b/i.test(cand.getAttribute("aria-label") || "")) continue;
          const r = cand.getBoundingClientRect();
          const area = r.width * r.height;
          if (!accept || area > bestArea) { accept = cand; bestArea = area; }
        }
        if (accept) break;
        container = container.parentElement;
      }
      if (!accept) continue;
      // Only where something is being sold: a tiny "Skip" beside "Next" in a
      // photo carousel is a design choice, not interference with a purchase.
      const offerText = fast(container || decline.parentElement).slice(0, 800);
      if (!(C.parseAmounts(offerText).length || C.PAID_ADDON_RE.test(offerText)
            || /\b(subscribe|upgrade|offer|plan|trial|join|membership)\b/i.test(offerText))) continue;

      const cs = getComputedStyle(decline);
      const fg = C.parseColor(cs.color);
      if (!fg) continue;
      const ratio = seenContrast(decline, fg);
      const dr = decline.getBoundingClientRect(), ar = accept.getBoundingClientRect();
      const areaRatio = (ar.width * ar.height) / Math.max(1, dr.width * dr.height);
      const lowContrast = ratio < 3.0;
      const dwarfed = areaRatio >= 4 && ratio < 4.5;
      if (!lowContrast && !dwarfed) continue;
      const key = dt + "|" + clickableText(accept);
      if (seen.has(key)) continue;
      seen.add(key);
      const ev = {
        decline_option: C.clip(C.redact(dt), 60),
        decline_contrast: `${ratio.toFixed(2)}:1 (WCAG AA asks 4.5:1 for normal text, 3:1 at minimum)`,
        accept_option: C.clip(C.redact(clickableText(accept)), 60),
        size_difference: `the accept option is ${areaRatio.toFixed(1)}x the area of the decline option`,
      };
      findings.push({
        code: "DP-06", tier: lowContrast && areaRatio >= 3 ? "corroborated" : "indicative",
        title: "The way to say no is made hard to see",
        detail: `"${ev.decline_option}" is drawn at ${ratio.toFixed(1)}:1 contrast, next to an accept option ${areaRatio.toFixed(1)}x its size.`,
        evidence: ev,
      });
    }
    return findings;
  }

  // ------------------------------------------------------------ urgency
  const TIMER_CONTEXT = /\b(ends?|ending|expir\w*|left|hurry|offer|deal|sale|hold|held|reserved|price|fare|lock\w*|grab|valid|timer|countdown)\b/i;
  const NOT_URGENCY_TIMER = /\b(session|log\s*out|logged\s*out|otp|resend|verify|redirect\w*|refresh\w*)\b/i;

  function findCountdowns() {
    const out = [];
    for (const el of document.querySelectorAll("span, div, b, strong, p, time, small, em")) {
      if (el.children.length > 4) continue;
      const f = fast(el);
      if (!f || f.length > 60 || !/\d:\d\d|\d\s*s(ec)?\b/i.test(f)) continue;
      const t = textOf(el);
      if (!t || t.length > 40) continue;
      const sec = C.parseCountdown(t);
      if (sec === null) continue;
      if ([...el.children].some((c) => C.parseCountdown(textOf(c)) !== null)) continue;
      let ctx = t;
      for (let n = el.parentElement, i = 0; n && i < 3; n = n.parentElement, i++) {
        const nt = textOf(n);
        if (nt.length > 220) break;
        ctx = nt;
      }
      if (!TIMER_CONTEXT.test(ctx) || NOT_URGENCY_TIMER.test(ctx) || !isVisible(el)) continue;
      out.push({ el, sec, ctx: C.clip(ctx, 80) });
      if (out.length >= 5) break;
    }
    return out;
  }

  /** The smallest element whose text matches -- its own words, not a run of
   *  neighbouring text glued together by innerText. */
  /** Every visible short element whose text matches, those inside a pop-up
   *  first, then shortest first. Checking only the single shortest match on
   *  the page let an innocent short line ("Battery is damaged?" in a repair
   *  FAQ) hide a real scare pop-up elsewhere. */
  function matchingElements(re, limit = 25) {
    const boxes = dialogs().map((d) => d.el);
    const inDialog = (el) => boxes.some((b) => b.contains(el));
    const out = [];
    for (const el of document.querySelectorAll("p, span, div, b, strong, li, small, em, h1, h2, h3, h4, label")) {
      const f = fast(el);
      if (!f || f.length > 200 || !re.test(f) || !isVisible(el)) continue;
      out.push({ el, len: f.length, dialog: inDialog(el) });
    }
    out.sort((a, b) => (b.dialog - a.dialog) || (a.len - b.len));
    return out.slice(0, limit).map((x) => x.el);
  }

  function smallestMatching(re) {
    let best = null;
    for (const el of document.querySelectorAll("p, span, div, b, strong, li, small, em, h1, h2, h3, h4, label")) {
      const f = fast(el);
      if (!f || f.length > 200 || !re.test(f)) continue;
      if (!best || f.length < best.len) best = { el, len: f.length };
    }
    return best ? textOf(best.el) : null;
  }

  function urgencyNotes() {
    const notes = [];
    const body = fast(document.body).slice(0, 30000);
    for (const re of C.URGENCY_PATTERNS) {
      if (!re.test(body)) continue;
      const snippet = C.clip(smallestMatching(re) || body.match(re)[0], 70);
      if (notes.some((n) => n.text.includes(snippet))) continue;
      notes.push({
        kind: "claim",
        text: `Urgency claim: "${C.redact(snippet)}". A browser cannot check stock or demand, so treat it as a claim, not a fact.`,
      });
      if (notes.length >= 2) break;
    }
    return notes;
  }

  // ------------------------------------------------- DP-07 helper: cart line
  /** The cart or checkout line that names the product you picked: the
   *  element whose words match most of the picked product's fingerprint, and
   *  the amount on its row. */
  /** The quantity on this cart row: in the label's own words, or anywhere
   *  in its row -- a sibling "Qty: 2", a number box, a quantity dropdown. A
   *  row total of ₹998 for two ₹499 items is not a price change. */
  const QTY_TEXT_RE = /\b(?:qty|quantity)\s*[:.]?\s*(\d{1,2})\b|\bx\s*(\d{1,2})\b|\b(\d{1,2})\s*x\b/i;
  function rowQuantity(el) {
    let row = el;
    for (let i = 0; row && i < 4; i++, row = row.parentElement) {
      if (i > 0 && fast(row).length > 300) break;
      const qm = fast(row).match(QTY_TEXT_RE);
      if (qm) return +(qm[1] || qm[2] || qm[3]);
      const box = row.querySelector && row.querySelector(
        'input[type=number], input[name*="qty" i], input[name*="quantity" i], select[name*="qty" i], select[name*="quantity" i], select[aria-label*="qty" i], select[aria-label*="quantity" i]');
      if (box) {
        const n = parseInt(box.value, 10);
        if (n > 0 && n < 100) return n;
      }
    }
    return 1;
  }

  function lineItemFor(fp) {
    let best = null;
    for (const el of document.querySelectorAll(LABEL_TAGS + ", a")) {
      if (el.children.length > 6) continue;
      const f = fast(el);
      if (f.length < 5 || f.length > 200) continue;
      const name = f.replace(/(-|\u2212)?\s*(\u20b9|(?<![A-Za-z])(?:rs\.?|inr))\s*[\d,.]+/gi, " ");
      const sim = C.similarity(C.fingerprint(name), fp);
      const coverage = sim.shared / Math.max(1, new Set(fp).size);
      if (sim.shared < 3 || coverage < 0.6) continue;
      if (best && (coverage < best.score || (coverage === best.score && f.length >= best.len))) continue;
      if (!isVisible(el)) continue;
      const v = amountForLabel(el);
      if (v === null || v <= 0) continue;
      best = { text: textOf(el), value: v, qty: rowQuantity(el), score: coverage, len: f.length };
    }
    return best;
  }

  /** "Only 3 left" -> {n: 3, text}. */
  function stockClaim() {
    const body = fast(document.body).slice(0, 30000);
    const m = body.match(/\bonly\s+(\d{1,2})\s+(left|remaining)\b|\bsirf\s+(\d{1,2})\s+(bache|bacha|baki|baaki)\b|(?:सिर्फ़|सिर्फ|केवल)\s*(\d{1,2})\s*(?:बचे|बचा|शेष)/i);
    if (!m) return null;
    return { n: +(m[1] || m[3] || m[5]), text: m[0] };
  }

  // ------------------------------------------------- dialogs (DP-04, DP-10)
  /** Pop-ups on screen now: real dialogs, and the fixed overlays most shops
   *  use instead. Each gets a signature from its words (numbers removed, so
   *  a changing countdown inside it does not make it a "new" pop-up). */
  function dialogs() {
    const cands = new Set(document.querySelectorAll("[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open]"));
    for (const el of document.querySelectorAll("body > *, body > * > *, body > * > * > *")) {
      if (cands.size > 40) break;
      const cs = getComputedStyle(el);
      if ((cs.position === "fixed" || cs.position === "sticky") && cs.display !== "none") cands.add(el);
    }
    const vw = window.innerWidth || 1, vh = window.innerHeight || 1;
    const out = [];
    for (const el of cands) {
      if (!isVisible(el)) continue;
      if (el.tagName === "ULTERIOR-ALERT") continue;
      const r = el.getBoundingClientRect();
      const area = (Math.min(r.width, vw) * Math.min(r.height, vh)) / (vw * vh);
      const isDialogRole = el.matches("[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open]");
      if (!isDialogRole && area < 0.12) continue;          // a sticky header or a chat bubble
      const f = fast(el);
      if (f.length < 8 || f.length > 900) continue;
      if (!el.querySelector(CLICKABLE)) continue;
      const sig = C.fnv1a(f.toLowerCase().replace(/[\d:]+/g, "#").slice(0, 160));
      out.push({ el, sig, text: C.clip(textOf(el), 100), area });
    }
    return out;
  }

  // ------------------------------------------------------ DP-04 Forced Action
  function forcedActionFindings() {
    const findings = [];
    const seen = new Set();
    choiceControls().forEach((el) => {
      const label = labelTextFor(el);
      if (!label || label.length > 400 || seen.has(label)) return;
      if (!C.CONSENT_TERMS_RE.test(label) || !C.CONSENT_MARKETING_RE.test(label)) return;
      if (!isVisible(el) && !(el.closest && el.closest("label") && isVisible(el.closest("label")))) return;
      seen.add(label);
      const required = el.required || el.getAttribute("aria-required") === "true";
      findings.push({
        code: "DP-04", tier: required ? "corroborated" : "indicative",
        title: required ? "To continue you must also agree to marketing or data sharing"
                        : "Agreeing to the terms is bundled with marketing or data sharing",
        detail: `One box covers both: "${C.clip(C.redact(label), 140)}"`,
        evidence: { consent_text: C.clip(C.redact(label), 160), required_to_continue: required ? "yes" : "not marked as required" },
      });
    });
    for (const d of dialogs()) {
      const f = fast(d.el);
      if (d.area < 0.5 || !C.APP_WALL_RE.test(f) || !C.APP_WALL_BLOCK_RE.test(f)) continue;
      const ways = [...d.el.querySelectorAll(CLICKABLE)].map(clickableText)
        .concat([...d.el.querySelectorAll("[aria-label]")].map((n) => n.getAttribute("aria-label") || ""));
      if (ways.some((t) => C.CONTINUE_ON_WEB_RE.test(t))) continue;
      findings.push({
        code: "DP-04", tier: "corroborated",
        title: "The page will not let you continue without installing the app",
        detail: `A screen covering ${Math.round(d.area * 100)}% of the page asks you to get the app, with no way to ` +
          `continue on the website: "${C.clip(C.redact(d.text), 90)}"`,
        evidence: { screen_text: C.clip(C.redact(d.text), 120), screen_coverage: `${Math.round(d.area * 100)}%`,
                    way_to_continue_on_web: "none found" },
      });
      break;
    }
    return findings;
  }

  // ------------------------------------------------ DP-05 Subscription Trap
  function subscriptionFindings() {
    const findings = [];
    // Every sign-up button is considered, not just the first: a footer
    // "Subscribe" (newsletter) or a loyalty "Join now" must not stand in for
    // the plan's own button, nor make an ordinary product page with an EMI
    // option look like a subscription.
    const NEWSLETTER_RE = /\b(newsletter|mailing\s+list|email\s+updates?|updates|alerts|notify\s+me)\b/i;
    const withoutEmi = (t) => t.replace(/[^.\n]*\b(emi|emis|instal+ments?|pay\s*later|no[\s-]cost)\b[^.\n]*/gi, " ");
    let cta = null, box = null;
    for (const el of document.querySelectorAll(CLICKABLE)) {
      const f = fast(el);
      if (!f || f.length > 40 || !C.SUBSCRIBE_CTA_RE.test(f) || !isVisible(el)) continue;
      // The offer around the button: climb until there is enough text to
      // hold a price and its terms, but stop BEFORE <body> -- the whole page
      // is not this button's offer.
      let b = el.parentElement || el;
      for (let i = 0; i < 4 && fast(b).length < 300 && b.parentElement && b.parentElement !== document.body
           && b.parentElement !== document.documentElement; i++) {
        b = b.parentElement;
      }
      const offer = withoutEmi(fast(b).slice(0, 1200));
      const recurring = C.RECURRING_BILLING_PATTERNS.some((p) => p.test(offer)) && C.parseAmounts(offer).length > 0;
      if (!recurring) continue;
      if (NEWSLETTER_RE.test(fast(b)) && !/(₹|rs\.?|inr)\s*[\d,]+[^.\n]{0,12}(\/|per\s+|a\s+)(mo|month|yr|year|week)/i.test(offer)) continue;
      cta = el;
      box = b;
      break;
    }
    if (!cta) return findings;
    const body = fast(document.body).slice(0, 40000);

    // Fine print: the trial-to-paid sentence drawn small or faint.
    for (const el of (box || document).querySelectorAll("p, span, small, div, li, label")) {
      if (el.children.length > 3) continue;
      const f = fast(el);
      if (f.length < 15 || f.length > 300 || !C.TRIAL_CONVERTS_RE.test(f) || !isVisible(el)) continue;
      const cs = getComputedStyle(el);
      const size = parseFloat(cs.fontSize) || 16;
      const fg = C.parseColor(cs.color);
      const ratio = fg ? seenContrast(el, fg) : 21;
      if (size >= 11 && ratio >= 3) continue;
      findings.push({
        code: "DP-05", tier: "corroborated",
        title: "What you will be charged after the trial is in fine print",
        detail: `"${C.clip(C.redact(textOf(el)), 110)}" is drawn at ${size.toFixed(0)}px and ${ratio.toFixed(1)}:1 contrast.`,
        evidence: { fine_print: C.clip(C.redact(textOf(el)), 140), font_size: `${size.toFixed(1)}px`,
                    contrast: `${ratio.toFixed(2)}:1 (WCAG AA asks 4.5:1)` },
      });
      break;
    }
    if (!C.CANCEL_INFO_RE.test(body)) {
      findings.push({
        code: "DP-05", tier: "indicative",
        title: "No way to cancel is mentioned where you sign up",
        detail: `"${C.clip(clickableText(cta), 40)}" starts a repeating charge, and nothing on this page says how to ` +
          "cancel it. The full check -- trying the cancel route -- needs the server audit.",
        evidence: { sign_up_button: C.clip(clickableText(cta), 40), cancellation_info_on_page: "none found" },
      });
    }
    return findings;
  }

  // ------------------------------------------- DP-09 Disguised Advertisement
  function sponsoredFindings() {
    const labels = [];
    for (const el of document.querySelectorAll("span, div, p, small, b, strong, em, label, a")) {
      if (el.children.length > 1) continue;
      const f = fast(el);
      if (!f || f.length > 22 || !C.SPONSORED_LABEL_RE.test(f) || !isVisible(el)) continue;
      labels.push(el);
      if (labels.length > 60) break;
    }
    const out = { findings: [], notes: [] };
    if (!labels.length) return out;
    const hard = [];
    for (const el of labels) {
      const cs = getComputedStyle(el);
      const size = parseFloat(cs.fontSize) || 16;
      const fg = C.parseColor(cs.color);
      const ratio = fg ? seenContrast(el, fg) : 21;
      if (ratio < 3 || size < 9 || (ratio < 4.5 && size < 11)) hard.push({ el, size, ratio });
    }
    if (hard.length) {
      const worst = hard.reduce((a, b) => (a.ratio < b.ratio ? a : b));
      out.findings.push({
        code: "DP-09", tier: "corroborated",
        title: `${hard.length} "sponsored" label${hard.length === 1 ? " is" : "s are"} hard to see`,
        detail: `The label that marks a paid placement is drawn at ${worst.size.toFixed(0)}px and ` +
          `${worst.ratio.toFixed(1)}:1 contrast, so the ad reads like an ordinary result.`,
        evidence: { labels_hard_to_see: `${hard.length} of ${labels.length}`, smallest_contrast: `${worst.ratio.toFixed(2)}:1`,
                    label_size: `${worst.size.toFixed(1)}px`, label_text: C.clip(fast(worst.el), 20) },
      });
    } else {
      out.notes.push({ kind: "info", text: `${labels.length} result${labels.length === 1 ? " is" : "s are"} marked as ` +
        "sponsored on this page, and the labels are readable." });
    }
    return out;
  }

  // -------------------------------------------------- DP-13 Rogue Malware
  function rogueFindings() {
    const body = fast(document.body).slice(0, 30000);
    if (!C.SCARE_RE.test(body)) return [];
    // The scare text and the action it pushes must be in the SAME box (the
    // pop-up or card), not merely somewhere on the page: an article that
    // mentions "virus detected" plus any Download button elsewhere is not
    // a rogue alert.
    let scareEl = null, ctas = [], exe = null;
    for (const cand of matchingElements(C.SCARE_RE)) {
      let box = cand;
      for (let i = 0; box && box !== document.body && i < 4; i++, box = box.parentElement) {
        ctas = [...box.querySelectorAll(CLICKABLE)].filter((el) => {
          const f = fast(el);
          return f && f.length <= 40 && C.SCARE_CTA_RE.test(f) && isVisible(el);
        });
        exe = [...box.querySelectorAll("a[href]")].find((a) => C.EXECUTABLE_RE.test(a.getAttribute("href") || ""));
        if (ctas.length || exe) break;
      }
      if (ctas.length || exe) { scareEl = cand; break; }
    }
    if (!scareEl) return [];
    const ev = { warning_text: C.clip(C.redact(textOf(scareEl)), 100),
                 action_offered: ctas.length ? C.clip(clickableText(ctas[0]), 40) : "a download link" };
    if (exe) ev.executable_link = C.clip((exe.getAttribute("href") || "").split("?")[0].split("/").pop(), 60);
    return [{
      // Indicative, never higher: this judges presentation only. Nothing is
      // downloaded or analysed (the same cap the server applies to DP-13).
      code: "DP-13", tier: "indicative",
      title: "A scare message pushes you to download or call",
      detail: `"${ev.warning_text}" next to "${ev.action_offered}". Ulterior judges the presentation only; it never ` +
        "opens or downloads a file.",
      evidence: ev,
    }];
  }

  root.UlteriorDetect = {
    isVisible, textOf, amountNodes, prominentPrice, findTotal, findFees, isCheckoutPage,
    quantities, buyButtons, preselectedFindings, trickWordingFindings, confirmShamingFindings,
    interferenceFindings, findCountdowns, urgencyNotes, CLICKABLE, clickableText, looksLikeCartOrCheckout,
    lineItemFor, stockClaim, dialogs, forcedActionFindings, subscriptionFindings, sponsoredFindings,
    rogueFindings, fast,
  };
})(globalThis);
