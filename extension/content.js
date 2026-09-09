/**
 * Content script: runs the same provable Layer-1 checks as the backend
 * engine (detectors/layer1_rules.py), client-side, so a shopper gets a
 * warning BEFORE they pay -- not after, in an audit report they'll never
 * read. Intentionally does NOT call the backend API for this; it should
 * work standing alone, offline, on any page.
 *
 * Price-across-navigation (drip pricing) is tracked via sessionStorage,
 * keyed by hostname, so a price seen on a listing page can be compared
 * against the price seen later on a checkout page of the SAME site,
 * across full page loads.
 */

(function () {
  const HOSTNAME = location.hostname;
  const STORAGE_KEY = `dpshield_first_price_${HOSTNAME}`;

  // Kept in sync with capture/state_extractor.py's URGENCY_PATTERNS. That
  // list was validated against Mathur et al.'s real 2019 crawl data (94.1%
  // detection); this extension was still shipping the ORIGINAL pre-validation
  // list (~72% on the same real data) until this fix -- found while doing
  // an unrelated cache cleanup, worth fixing immediately rather than noting
  // for later.
  const URGENCY_PATTERNS = [
    /only\s+\d+\s+(left|remaining|seats?|units?|at this price)/i,
    /hurry/i, /offer ends?/i, /limited time/i,
    /\d+\s+people (are\s+)?(viewing|booked|bought|seeing)/i,
    /selling fast/i, /sell(ing)? out (fast|quickly)/i, /\bwill sell out\b/i,
    /high[\s-]?demand/i, /strong demand/i, /in high demand/i,
    /\d+\s+claimed/i, /claimed!/i,
    /low(\s+in)? stock/i, /limited stock/i,
    /reserved for \d+/i, /sale ends? (once|when)/i,
    /\d+[^.\n]{0,20}(left|in stock|remaining)/i,
    /\bin stock\b/i,
  ];

  const GUILT_PATTERNS = [
    /\bi don'?t (like|want|need|care about|mind|hate|despise)\b/i,
    /\bi'?d?\s*(rather|prefer)\s*(to\s*)?(not\s+)?(pay|have|save|win)\b/i,
    /\bi don'?t feel lucky\b/i,
    /\bi want to pay (more|the full price)\b/i,
    /\bi do not want\b/i,
    /\baccept (all )?(the )?risk\b/i,
    /\bwithout (a )?(protection|insurance|discount)\b/i,
    /\bi'?ll (risk|let this offer|be the last)\b/i,
    /pay(ing)?\s*full price/i,
    /like (paying )?full price/i,
    /don'?t (mind )?miss(ing)? out/i,
    /don'?t want \d+%/i,
  ];

  /**
   * Kept in sync with detectors/vocab.py and detectors/layer3_language.py.
   *
   * The extension previously carried only the DP-01/DP-02/DP-03/DP-08 rules,
   * which meant a user shopping with it saw a strictly narrower picture than
   * the server would report on the same page -- and had no way to know that.
   * These two lists close the gap for the patterns a single page can decide
   * on its own. DP-04, DP-05 and DP-10 are deliberately NOT here: they are
   * properties of a multi-step journey, and a content script that guessed at
   * them from one page would be reporting something it cannot see.
   */
  const RECURRING_BILLING_PATTERNS = [
    /\bauto[\s-]?renew(s|al|ing)?\b/i,
    /\brecurring\b/i,
    /\bsubscription\b/i,
    /\bper\s+(month|year|week)\b/i,
    /\/\s*(mo|month|yr|year|week)\b/i,
    /\bbilled\s+(monthly|annually|yearly)\b/i,
    /\bmonthly\b/i, /\byearly\b/i, /\bannually\b/i,
    /\bfree trial\b/i,
  ];

  const DOUBLE_NEGATIVE_PATTERNS = [
    /\bunche?ck\b[^.]{0,60}\b(if|to)\b[^.]{0,40}\b(not|don'?t|do not|no longer|never)\b/i,
    /\buntick\b[^.]{0,60}\b(if|to)\b[^.]{0,40}\b(not|don'?t|do not)\b/i,
    /\b(do not|don'?t)\b[^.]{0,40}\b(unche?ck|untick|opt out)\b/i,
    /\b(not|never)\b[^.]{0,30}\bdisagree\b/i,
  ];

  const OPT_OUT_LABEL_PATTERNS = [
    /\bdo not\b/i, /\bdon'?t\b/i, /\bopt out\b/i, /\bunsubscribe\b/i,
    /\bno longer\b/i, /\bstop (sending|receiving)\b/i,
  ];

  function normalizeQuotes(s) {
    return s.replace(/\u2019/g, "'").replace(/\u2018/g, "'");
  }

  function labelTextFor(input) {
    if (input.id) {
      const explicit = document.querySelector(`label[for="${input.id}"]`);
      if (explicit && explicit.innerText) return explicit.innerText.trim();
    }
    return (input.parentElement && input.parentElement.innerText || "").trim();
  }

  /**
   * DP-12 SaaS Billing: a control already selected on load whose own label
   * commits the user to a repeating charge. Radios matter as much as
   * checkboxes here -- a pre-selected paid tier is nearly always a radio.
   */
  function checkRecurringPreselection() {
    const flags = [];
    document.querySelectorAll("input[type=checkbox], input[type=radio]").forEach((el) => {
      if (!el.checked) return;
      const label = normalizeQuotes(labelTextFor(el));
      if (!label || !RECURRING_BILLING_PATTERNS.some((p) => p.test(label))) return;
      flags.push({
        pattern: "DP-12", name: "SaaS Billing",
        detail: `Already selected on load, and commits you to a repeating charge: `
              + `"${redact(label.slice(0, 120))}"`,
      });
    });
    return flags;
  }

  /**
   * DP-11 Trick Wording: a double negative, or a pre-ticked control whose
   * label means "do not" -- where the tick and the wording point in opposite
   * directions and your actual state is the opposite of what it looks like.
   */
  function checkTrickWording() {
    const flags = [];
    document.querySelectorAll("input[type=checkbox], input[type=radio]").forEach((el) => {
      const label = normalizeQuotes(labelTextFor(el));
      if (!label) return;
      if (DOUBLE_NEGATIVE_PATTERNS.some((p) => p.test(label))) {
        flags.push({
          pattern: "DP-11", name: "Trick Wording",
          detail: `Double negative -- a negative action is required to express a negative `
                + `preference: "${redact(label.slice(0, 120))}"`,
        });
        return;
      }
      if (el.checked && OPT_OUT_LABEL_PATTERNS.some((p) => p.test(label))) {
        flags.push({
          pattern: "DP-11", name: "Trick Wording",
          detail: `Ticked by default while worded as an opt-out, so your actual setting is `
                + `the opposite of how it reads: "${redact(label.slice(0, 120))}"`,
        });
      }
    });
    return flags;
  }

  /**
   * PRIVACY: this content script runs on pages the user visits, which may
   * include logged-in sessions containing their name, address, email, phone,
   * or masked card number. Any page text we quote back into a flag detail is
   * therefore redacted before it is written to chrome.storage.local.
   * Mirrors app/privacy.py server-side.
   */
  const PII_PATTERNS = [
    [/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, "[EMAIL_REDACTED]"],
    [/\b(?:\d[ -]*?){13,19}\b/g, "[CARD_REDACTED]"],
    [/\b\d{4}[ -]?\d{4}[ -]?\d{4}\b/g, "[ID_REDACTED]"],
    [/(?:\+?91[\s-]?|\b0)?[6-9](?:[\s-]?\d){9}\b/g, "[PHONE_REDACTED]"],
  ];

  function redact(text) {
    if (!text) return text;
    let out = text;
    for (const [pattern, replacement] of PII_PATTERNS) {
      out = out.replace(pattern, replacement);
    }
    return out;
  }

  // ---------------------------------------------------------------------
  // Evaluation cache: avoids re-running regex + redact() on the SAME
  // element text every time the MutationObserver fires. On a page with a
  // live chat widget, ad refresh, or view counter, the debounced scan can
  // fire dozens of times a minute even though the checkout/cart content
  // itself never changes -- this cache makes those re-fires near-free.
  // Simple LRU: a Map preserves insertion order, so the oldest key is
  // always keys().next().value; delete+reinsert on hit refreshes recency.
  // ---------------------------------------------------------------------
  const CACHE_MAX_ENTRIES = 500;
  const evalCache = new Map();

  function cacheGet(key) {
    if (!evalCache.has(key)) return undefined;
    const value = evalCache.get(key);
    evalCache.delete(key);
    evalCache.set(key, value); // move to most-recently-used position
    return value;
  }

  function cacheSet(key, value) {
    evalCache.set(key, value);
    if (evalCache.size > CACHE_MAX_ENTRIES) {
      const oldestKey = evalCache.keys().next().value;
      evalCache.delete(oldestKey);
    }
  }

  function findSchemaOrgPrice() {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of scripts) {
      try {
        const data = JSON.parse(s.textContent);
        if (data.offers && data.offers.price) return parseFloat(data.offers.price);
      } catch (e) { /* not valid JSON-LD, skip */ }
    }
    return null;
  }

  function findDataPrice() {
    const el = document.querySelector("[data-price]");
    return el ? parseFloat(el.getAttribute("data-price")) : null;
  }

  function checkPrechecked() {
    const flags = [];
    document.querySelectorAll('input[type=checkbox]:checked').forEach((cb) => {
      const label = (cb.parentElement && cb.parentElement.innerText || "").toLowerCase();
      const cacheKey = `cb:${label}`;
      const cached = cacheGet(cacheKey);
      if (cached !== undefined) {
        if (cached) flags.push(cached);
        return;
      }
      let flag = null;
      if (label.includes("₹") || label.includes("insurance") || label.includes("protection")
          || label.match(/rs\.?\s*\d/)) {
        flag = {
          pattern: "DP-02", name: "Basket Sneaking",
          detail: `A checkbox was pre-checked for what looks like a paid add-on: "${redact(cb.parentElement.innerText.trim().slice(0, 80))}"`,
        };
      }
      cacheSet(cacheKey, flag);
      if (flag) flags.push(flag);
    });
    return flags;
  }

  function checkUrgencyLanguage() {
    const flags = [];
    const bodyText = normalizeQuotes(document.body.innerText);
    // Cache keyed on a cheap signature of the WHOLE body text, not per
    // element -- unlike checkbox/button checks, urgency banners aren't
    // reliably tied to any specific interactive element (a plain <p> banner
    // counts too), so per-element scoping would cost real recall. Skipping
    // the entire regex sweep when nothing has changed is the honest win here.
    const bodySignature = `body:${bodyText.length}:${bodyText.slice(0, 120)}`;
    const cached = cacheGet(bodySignature);
    if (cached !== undefined) return cached;

    for (const pattern of URGENCY_PATTERNS) {
      if (pattern.test(bodyText)) {
        flags.push({
          pattern: "DP-01", name: "False Urgency (unverified)",
          detail: `Page uses urgency language matching "${pattern}". Reload later to see if it's fabricated.`,
        });
      }
    }
    cacheSet(bodySignature, flags);
    return flags;
  }

  function checkConfirmShaming() {
    const flags = [];
    document.querySelectorAll("button, a").forEach((el) => {
      const rawText = el.innerText || "";
      const text = normalizeQuotes(rawText);
      const cacheKey = `btn:${text}`;
      const cached = cacheGet(cacheKey);
      if (cached !== undefined) {
        if (cached) flags.push(cached);
        return;
      }
      let flag = null;
      if (text.length > 8 && GUILT_PATTERNS.some((p) => p.test(text))) {
        flag = {
          pattern: "DP-03", name: "Confirm Shaming",
          detail: `Decline option worded to guilt the user: "${redact(rawText.trim().slice(0, 120))}"`,
        };
      }
      cacheSet(cacheKey, flag);
      if (flag) flags.push(flag);
    });
    return flags;
  }

  function checkDripPricing() {
    const flags = [];
    const currentPrice = findSchemaOrgPrice() || findDataPrice();
    if (currentPrice == null) return flags;

    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (!stored) {
      sessionStorage.setItem(STORAGE_KEY, String(currentPrice));
      return flags;
    }
    const firstPrice = parseFloat(stored);
    if (currentPrice > firstPrice * 1.02) {
      flags.push({
        pattern: "DP-08", name: "Drip Pricing",
        detail: `Price seen earlier on this site was ₹${firstPrice}; this page shows ₹${currentPrice} -- `
              + `₹${(currentPrice - firstPrice).toFixed(2)} was added without being shown upfront.`,
      });
    }
    return flags;
  }

  function runAllChecks() {
    const flags = [
      ...checkPrechecked(),
      ...checkUrgencyLanguage(),
      ...checkConfirmShaming(),
      ...checkDripPricing(),
      ...checkRecurringPreselection(),
      ...checkTrickWording(),
    ];
    // PRIVACY: store only the hostname, never location.href -- query strings
    // routinely carry session tokens, order ids, and email addresses.
    chrome.storage.local.set({ [`flags_${HOSTNAME}`]: flags, lastHost: HOSTNAME });
    chrome.runtime.sendMessage({ type: "FLAGS_UPDATED", count: flags.length });
    return flags;
  }

  // Run once on load, and again if the page is a SPA that mutates the DOM
  // after initial render (debounced to avoid re-scanning on every keystroke).
  let debounceTimer = null;
  const observer = new MutationObserver(() => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runAllChecks, 800);
  });

  window.addEventListener("load", () => {
    runAllChecks();
    observer.observe(document.body, { childList: true, subtree: true });
  });

  // Exposed for testing only (tests/test_extension_cache.js runs this file
  // in Node against a mocked DOM/chrome global, not in a real browser).
  if (typeof module !== "undefined") {
    module.exports = {
      cacheGet, cacheSet, evalCache, CACHE_MAX_ENTRIES,
      URGENCY_PATTERNS, GUILT_PATTERNS, normalizeQuotes,
      RECURRING_BILLING_PATTERNS, DOUBLE_NEGATIVE_PATTERNS, OPT_OUT_LABEL_PATTERNS,
    };
  }
})();
