/**
 * Unit tests for extension/src/core.js -- the arithmetic and vocabularies
 * the findings rest on. Plain Node, no browser, no dependencies.
 * Run directly with `node tests/extension_core.test.js`, or through pytest
 * (tests/test_extension_static.py), which skips it if Node is not installed.
 */
const assert = require("assert");
const C = require("../extension/src/core.js");

let failures = 0;
function test(name, fn) {
  try { fn(); console.log("PASS", name); }
  catch (e) { failures++; console.log("FAIL", name, "\n  ", e.message); }
}

test("Indian money formats parse", () => {
  const v = (s) => C.parseAmounts(s).map((a) => a.value);
  assert.deepStrictEqual(v("₹1,23,456.50"), [123456.5]);
  assert.deepStrictEqual(v("Rs. 499"), [499]);
  assert.deepStrictEqual(v("Rs 499/-"), [499]);
  assert.deepStrictEqual(v("INR 1,299"), [1299]);
  assert.deepStrictEqual(v("₹ 2,499"), [2499]);
  assert.deepStrictEqual(v("-₹50"), [-50]);
  assert.deepStrictEqual(v("Base ₹4,599 + fee ₹350"), [4599, 350]);
  assert.deepStrictEqual(v("2h 15m, gate 12"), []);
});

test("formatINR uses lakh grouping", () => {
  assert.strictEqual(C.formatINR(123456.5), "₹1,23,456.50");
  assert.strictEqual(C.formatINR(4599), "₹4,599");
  assert.strictEqual(C.formatINR(572.8), "₹572.80");
});

test("fee labels match the usual names and not taxes", () => {
  for (const s of ["Convenience fee", "Convenience Fees", "Platform fee", "Handling charges",
                   "Internet handling fee", "Booking fee", "Small cart fee", "Rain fee"]) {
    assert.ok(C.FEE_LABEL_RE.test(s), s);
  }
  for (const s of ["GST", "Taxes", "Base fare", "Discount"]) assert.ok(!C.FEE_LABEL_RE.test(s), s);
});

test("benefits are not add-ons", () => {
  assert.ok(C.BENEFIT_RE.test("Use my wallet balance"));
  assert.ok(C.BENEFIT_RE.test("Apply coupon SAVE10"));
  assert.ok(!C.BENEFIT_RE.test("Add travel insurance ₹199"));
  assert.ok(C.PAID_ADDON_RE.test("Contribute ₹2 to charity"));
});

test("'in stock' alone is not urgency", () => {
  assert.ok(!C.URGENCY_PATTERNS.some((p) => p.test("In stock")));
  assert.ok(C.URGENCY_PATTERNS.some((p) => p.test("Only 2 left in stock")));
});

test("countdowns parse; flight durations do not", () => {
  assert.strictEqual(C.parseCountdown("09:59"), 599);
  assert.strictEqual(C.parseCountdown("1:02:03"), 3723);
  assert.strictEqual(C.parseCountdown("08m 12s"), 492);
  assert.strictEqual(C.parseCountdown("2h 15m"), null);
});

test("site keys group subdomains", () => {
  assert.strictEqual(C.siteOf("www.flights.makemytrip.com"), "makemytrip.com");
  assert.strictEqual(C.siteOf("www.amazon.in"), "amazon.in");
  assert.strictEqual(C.siteOf("shop.example.co.in"), "example.co.in");
  assert.strictEqual(C.siteOf("127.0.0.1"), "127.0.0.1");
});

test("product keys keep the id and drop the rest", () => {
  assert.strictEqual(C.productKey("https://www.amazon.in/Some-Name/dp/B0ABCDEF12/ref=x?tag=y"), "amazon.in:B0ABCDEF12");
  assert.strictEqual(C.productKey("https://www.flipkart.com/x/p/itmabc123?pid=MOBG123&lid=1"), "flipkart.com:MOBG123");
  assert.strictEqual(C.productKey("https://www.myntra.com/shoes/x/y/1234567/buy"), "myntra.com:1234567");
  assert.strictEqual(C.productKey("https://shop.example.com/"), null);
});

test("drip judgement", () => {
  const j = (start, total, qty, fees) => C.judgeJourney(start, total, qty, fees).kind;
  assert.strictEqual(j({ price: 4599 }, 5148, 1, [{ value: 350 }]), "drip");
  assert.strictEqual(j({ price: 4599 }, 4599, 1, []), "match");
  assert.strictEqual(j({ price: 250 }, 572.8, 2, [{ value: 70.8 }]), "drip");
  assert.strictEqual(j({ price: 250 }, 500, 2, []), "match");
  assert.strictEqual(j({ price: 4599 }, 5148, 1, []), "unexplained");
  assert.strictEqual(j({ price: 4599, disclosed: true }, 5148, 1, [{ value: 350 }]), "disclosed");
});

test("contrast matches WCAG reference values", () => {
  const r = C.contrastRatio({ r: 0, g: 0, b: 0 }, { r: 255, g: 255, b: 255 });
  assert.ok(Math.abs(r - 21) < 0.01, r);
  const g = C.contrastRatio({ r: 118, g: 118, b: 118 }, { r: 255, g: 255, b: 255 });
  assert.ok(g > 4.5 && g < 4.6, g);
});

test("redaction removes contact details and booking refs", () => {
  const s = C.redact("Mail a@b.co, call +91 98765 43210, PNR: AB12CD, PAN ABCDE1234F");
  assert.ok(!/a@b\.co|98765|AB12CD|ABCDE1234F/.test(s), s);
});

test("the report names the site, never a URL, and carries the disclaimer", () => {
  const t = C.buildReport("example.com", [{ code: "DP-08", tier: "corroborated", title: "t", detail: "d", evidence: { fees_first_shown_here: "x" } }], new Date(0));
  assert.ok(t.includes("example.com") && !t.includes("http"));
  assert.ok(t.includes("not a legal determination") && t.includes("1915"));
  assert.ok(t.includes("fees first shown here: x"));
});

test("Hindi and Hinglish vocabulary", () => {
  const N = (x) => C.normalizeQuotes(x);
  assert.ok(C.URGENCY_PATTERNS.some((p) => p.test("Jaldi karein! Sirf 2 bache hain")));
  assert.ok(C.URGENCY_PATTERNS.some((p) => p.test(N("जल्दी करें! सिर्फ़ 2 बचे हैं"))));
  assert.ok(C.FEE_LABEL_RE.test(N("सुविधा शुल्क")));
  assert.ok(C.TOTAL_LABELS.some((r) => r.test(N("कुल राशि"))));
  assert.ok(C.DECLINE_RE.test(N("नहीं, धन्यवाद")));
  assert.ok(C.GUILT_PATTERNS.some((p) => p.test(N("नहीं, मुझे बचत नहीं चाहिए"))));
  assert.ok(C.PAID_ADDON_RE.test(N("₹5 दान करें")));
  assert.deepStrictEqual(C.parseAmounts("रु. 499").map((a) => a.value), [499]);
  // A nukta typed as two code points still matches.
  assert.ok(C.URGENCY_PATTERNS.some((p) => p.test(N("सिर\u094d\u092b\u093c 2 बचे"))));
});

test("same product across shops by fingerprint, not by name", () => {
  const a = C.fingerprint("Apple iPhone 15 (Blue, 128 GB)");
  assert.ok(C.sameProduct(a, C.fingerprint("Apple iPhone 15 128GB - Blue")));
  assert.ok(!C.sameProduct(a, C.fingerprint("Apple iPhone 15 Pro (Black, 256 GB)")));
  assert.ok(a.every((h) => /^[0-9a-f]{8}$/.test(h)), "fingerprints are codes, not words");
});

test("a report payload carries no URL and is capped", () => {
  const p = C.buildReportPayload({ site: "x.in", page: "checkout", scannedAt: 0,
    findings: Array.from({ length: 30 }, () => ({ code: "DP-08", tier: "corroborated", title: "mail a@b.co",
      detail: "d".repeat(900), evidence: { k: "v" } })) });
  assert.strictEqual(p.findings.length, 20);
  assert.ok(!JSON.stringify(p).includes("http"));
  assert.ok(!JSON.stringify(p).includes("a@b.co"));
  assert.ok(p.findings[0].detail.length <= 600);
});

test("all thirteen patterns are listed, two of them honestly partial", () => {
  assert.strictEqual(C.EXTENSION_COVERAGE.length, 13);
  assert.deepStrictEqual(C.EXTENSION_COVERAGE.filter((r) => r[1] === "partial").map((r) => r[0]), ["DP-05", "DP-13"]);
});

test("deal verdict needs a week of history and says so", () => {
  const now = Date.now(), D = C.DAY;
  assert.strictEqual(C.dealVerdict(100, [{ p: 100, t: now }], now).kind, "unknown");
  const pts = [{ p: 1299, t: now - 60 * D }, { p: 1349, t: now - 40 * D }, { p: 1299, t: now - 20 * D }, { p: 1199, t: now - 5 * D }];
  assert.strictEqual(C.dealVerdict(1199, pts, now).kind, "good");
  assert.strictEqual(C.dealVerdict(1499, pts, now).kind, "high");
  assert.strictEqual(C.dealVerdict(1290, pts, now).kind, "fair");
});

test("a discount against an M.R.P. it never sold at is called out", () => {
  const now = Date.now(), D = C.DAY;
  const pts = [{ p: 1299, t: now - 60 * D }, { p: 1399, t: now - 30 * D }, { p: 1299, t: now - 2 * D }];
  const d = C.discountCheck(2499, 1299, pts, now);
  assert.strictEqual(d.kind, "warn");
  assert.ok(d.text.includes("never sold above ₹1,399"), d.text);
  assert.strictEqual(C.discountCheck(1399, 1299, pts, now).kind, "ok");
  assert.strictEqual(C.discountCheck(null, 1299, pts, now), null);
});

test("own readings win over community ones on the same day", () => {
  const t = Date.UTC(2026, 9, 1, 12);
  const m = C.mergeSeries([{ p: 100, t, src: "you" }], [{ p: 120, t: t + 3600e3, src: "community" }, { p: 130, t: t - C.DAY }]);
  assert.deepStrictEqual(m.map((x) => x.p), [130, 100]);
});

test("watched links drop their query string, except a product id", () => {
  // Tracking and search parameters go; the product-id parameter stays,
  // because on query-routed shops it is the only thing naming the product.
  assert.strictEqual(C.cleanProductUrl("https://www.flipkart.com/x/p/itm1?pid=A&affid=me&q=secret"), "https://www.flipkart.com/x/p/itm1?pid=A");
  assert.strictEqual(C.cleanProductUrl("https://www.flipkart.com/x/p/itm1?affid=me&q=secret"), "https://www.flipkart.com/x/p/itm1");
  assert.strictEqual(C.cleanProductUrl("javascript:alert(1)"), null);
  assert.strictEqual(C.suggestTarget(69900), 66400);
});

// --- second code review (November 2026) -----------------------------------
test("'rs' and 'inr' inside a word are not rupees", () => {
  const v = (s) => C.parseAmounts(s).map((a) => a.value);
  assert.deepStrictEqual(v("SNEAKERS 55"), []);
  assert.deepStrictEqual(v("OFFERS 200 and YEARS 2"), []);
  assert.deepStrictEqual(v("Rs. 499"), [499]);
});

test("a spaced minus is a discount only at the start or after '('", () => {
  const v = (s) => C.parseAmounts(s).map((a) => a.value);
  assert.deepStrictEqual(v("- ₹ 130"), [-130]);
  assert.deepStrictEqual(v("(- ₹ 130)"), [-130]);
  assert.deepStrictEqual(v("Convenience Fee - ₹49"), [49]);
});

test("query-routed shops keep their product id in the key", () => {
  const a = C.productKey("http://127.0.0.1:8000/shop/product.php?id=1");
  const b = C.productKey("http://127.0.0.1:8000/shop/product.php?id=2");
  assert.notStrictEqual(a, b);
  assert.strictEqual(C.productKey("http://127.0.0.1:8000/shop/product.php?id=1&utm_source=x"), a);
});

test("phones are redacted, but not the tail of a longer code", () => {
  assert.strictEqual(C.redact("Call 9876543210 now"), "Call [PHONE] now");
  assert.strictEqual(C.redact("Call +91 98765 43210"), "Call [PHONE]");
  assert.strictEqual(C.redact("SKU9876543210"), "SKU9876543210");
});

// --- third code review (November 2026) ------------------------------------
test("deadline wording with a clock is urgency", () => {
  assert.ok(C.URGENCY_PATTERNS.some((p) => p.test("Deal ends in 00:04:59")));
  assert.ok(C.URGENCY_PATTERNS.some((p) => p.test("Offer expires in 2h")));
});

test("wallet balance and cashback are benefits, not add-ons", () => {
  assert.ok(C.BENEFIT_RE.test("Use my \u20b950 wallet balance"));
  assert.ok(C.BENEFIT_RE.test("Apply \u20b9100 cashback"));
});

// --- fourth code review (November 2026) -----------------------------------
test("'no hidden fees' and 'inclusive of all fees' are not a disclosure of extra charges", () => {
  for (const t of ["Inclusive of all fees", "No hidden fees", "Zero delivery charges", "Delivery charges: FREE"]) {
    assert.strictEqual(C.disclosesCharges(t), false, t);
  }
  assert.strictEqual(C.disclosesCharges("+ \u20b940 delivery fee"), true);
  assert.strictEqual(C.disclosesCharges("Convenience fee \u20b999 extra"), true);
});

test("a neutral decline is not confirmshaming", () => {
  assert.strictEqual(C.isShaming("No, I don't need warranty"), false);
  assert.strictEqual(C.isShaming("No, continue without protection"), false);
  assert.strictEqual(C.isShaming("No thanks, I don't like discounts"), true);
  assert.strictEqual(C.isShaming("No, I don't want to save money"), true);
});

test("timestamps are not card numbers", () => {
  assert.strictEqual(C.redact("2026-11-28 15-08-30"), "2026-11-28 15-08-30");
  assert.strictEqual(C.redact("4111 1111 1111 1111"), "[CARD]");
});

// --- fifth code review (November 2026) ------------------------------------
test("taxes-and-fees-included wording is not a disclosure of extra charges", () => {
  for (const t of ["Inclusive of all taxes and fees", "All fees included", "\u20b90 delivery fee",
                   "Delivery charges are included"]) {
    assert.strictEqual(C.disclosesCharges(t), false, t);
  }
});

// --- sixth code review (November 2026) ------------------------------------
test("terms bundled with marketing is forced action; optional or lawful boxes are not", () => {
  const bundled = (l) => C.CONSENT_TERMS_RE.test(l) && C.CONSENT_MARKETING_RE.test(l);
  assert.strictEqual(bundled("I agree to receive promotional offers"), false);
  assert.strictEqual(bundled("I have read the Privacy Policy, including how my data is shared with third parties"), false);
  assert.strictEqual(bundled("I accept the Privacy Policy and agree you may share my details with delivery partners to fulfil this order"), false);
  assert.strictEqual(bundled("I accept the Terms to receive order updates via SMS"), false);
  assert.strictEqual(bundled("I agree to the Terms of Use and to receive promotional offers on WhatsApp"), true);
  assert.strictEqual(bundled("I agree to the Terms and to share my data with advertisers"), true);
});

test("multi-day timers and time ranges", () => {
  assert.strictEqual(C.parseCountdown("02:14:33:12"), 2 * 86400 + 14 * 3600 + 33 * 60 + 12);
  assert.notStrictEqual(C.parseCountdown("02:14:33:12"), C.parseCountdown("02:14:33:10"));
  assert.strictEqual(C.parseCountdown("10:00 - 18:00"), null);
  assert.strictEqual(C.parseCountdown("00:04:59"), 299);
});

test("formatINR carries paise into rupees", () => {
  assert.strictEqual(C.formatINR(99.999), "\u20b9100");
  assert.strictEqual(C.formatINR(1299.5), "\u20b91,299.50");
});

test("a size chart is not the kind of pop-up that nags", () => {
  assert.strictEqual(C.DIALOG_HINT_RE.test("Size chart: chest, waist, length in cm"), false);
  assert.strictEqual(C.DIALOG_HINT_RE.test("Get 10% off your first order! Sign up"), true);
});

if (failures) { console.log(`${failures} failed`); process.exit(1); }
console.log("all passed");
