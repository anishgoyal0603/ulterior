/**
 * Runs extension/content.js in Node (not a browser) to test the pure-logic
 * pieces: LRU cache eviction and the urgency/guilt regex lists. The DOM-
 * dependent parts (querySelectorAll, chrome.storage) aren't exercised here --
 * those are covered by the fact that this is the SAME file loaded by the
 * real extension, syntax-checked with `node --check` in CI.
 */
const assert = require("assert");

// Minimal shims so content.js's module-load-time code doesn't throw when
// required outside a real browser/extension context.
global.window = { addEventListener: () => {} };
global.document = { body: {}, querySelectorAll: () => [] };
global.location = { hostname: "test.local" };
global.chrome = { storage: { local: { set: () => {} } }, runtime: { sendMessage: () => {} } };
global.sessionStorage = { getItem: () => null, setItem: () => {} };
global.MutationObserver = class { observe() {} disconnect() {} };

const {
  cacheGet, cacheSet, evalCache, CACHE_MAX_ENTRIES, URGENCY_PATTERNS, GUILT_PATTERNS, normalizeQuotes,
  RECURRING_BILLING_PATTERNS, DOUBLE_NEGATIVE_PATTERNS, OPT_OUT_LABEL_PATTERNS,
} = require("../extension/content.js");

let failures = 0;
function test(name, fn) {
  try {
    fn();
    console.log(`PASS: ${name}`);
  } catch (e) {
    failures++;
    console.log(`FAIL: ${name}\n  ${e.message}`);
  }
}

test("cache miss returns undefined", () => {
  assert.strictEqual(cacheGet("nonexistent-key"), undefined);
});

test("cache set then get returns the stored value", () => {
  cacheSet("k1", { pattern: "DP-01" });
  assert.deepStrictEqual(cacheGet("k1"), { pattern: "DP-01" });
});

test("cache evicts the OLDEST entry once over CACHE_MAX_ENTRIES", () => {
  evalCache.clear();
  for (let i = 0; i < CACHE_MAX_ENTRIES; i++) cacheSet(`key${i}`, i);
  assert.strictEqual(evalCache.size, CACHE_MAX_ENTRIES);
  cacheSet("one-more", 999); // pushes size over the cap
  assert.strictEqual(evalCache.size, CACHE_MAX_ENTRIES, "cache must not grow past the cap");
  assert.strictEqual(cacheGet("key0"), undefined, "oldest entry (key0) should have been evicted");
  assert.strictEqual(cacheGet("key1"), 1, "second-oldest entry should still be present");
});

test("cacheGet refreshes recency (LRU, not FIFO)", () => {
  evalCache.clear();
  cacheSet("a", 1);
  cacheSet("b", 2);
  cacheGet("a"); // touch 'a' -- it should no longer be the oldest
  cacheSet("c", 3);
  // insertion order is now: b, a, c (a was moved to the end on touch)
  const keysInOrder = Array.from(evalCache.keys());
  assert.deepStrictEqual(keysInOrder, ["b", "a", "c"]);
});

test("urgency patterns catch real Mathur-et-al.-style phrasing", () => {
  const realExamples = [
    "Hurry, only 2 left in stock!",
    "This item is in high demand",
    "20 units left",
    "96 item(s) left in stock!",
  ];
  for (const text of realExamples) {
    const matched = URGENCY_PATTERNS.some((p) => p.test(text));
    assert.ok(matched, `should match urgency pattern: "${text}"`);
  }
});

test("guilt patterns catch real Mathur-et-al. confirm-shaming phrasing (curly quotes included)", () => {
  const realExamples = [
    "No thanks, I don\u2019t mind missing out",   // curly apostrophe, real example
    "No Thanks, I like paying full price.",
    "I'd rather pay more",
  ];
  for (const text of realExamples) {
    const normalized = normalizeQuotes(text);
    const matched = GUILT_PATTERNS.some((p) => p.test(normalized));
    assert.ok(matched, `should match guilt pattern after normalization: "${text}"`);
  }
});

test("guilt patterns do not flag neutral button text", () => {
  const neutralExamples = ["Continue to checkout", "Add to cart", "Sign in", "View cart"];
  for (const text of neutralExamples) {
    const matched = GUILT_PATTERNS.some((p) => p.test(normalizeQuotes(text)));
    assert.ok(!matched, `must NOT match neutral text: "${text}"`);
  }
});


// --- DP-11 / DP-12 parity with the backend ------------------------------
//
// The extension and the server must agree about what a page contains. When
// they disagree, the user shopping with the extension sees a narrower
// picture than an audit of the same page would report, with nothing to
// indicate anything is missing -- which is exactly the failure mode this
// project exists to complain about.

test("recurring-billing patterns catch pre-selected paid tier labels", () => {
  const labels = [
    "Pro - Rs 999 billed monthly, renews automatically",
    "Premium Annual - Rs 4,999 billed annually, auto-renews",
    "Start your free trial",
    "Rs 499 per month",
    "Rs 99/mo",
  ];
  for (const label of labels) {
    assert.ok(RECURRING_BILLING_PATTERNS.some((p) => p.test(label)), `missed recurring label: ${label}`);
  }
});

test("recurring-billing patterns do not flag a one-off charge", () => {
  const labels = [
    "Basic - Rs 199 one-time day pass",
    "Free - Rs 0, no card required",
    "Order Protection Insurance - Rs 49",
  ];
  for (const label of labels) {
    assert.ok(!RECURRING_BILLING_PATTERNS.some((p) => p.test(label)), `false positive: ${label}`);
  }
});

test("double-negative patterns catch the classic inverted opt-out", () => {
  const labels = [
    "Uncheck this box if you do not wish to receive promotional emails",
    "Untick to not receive offers",
    "Do not uncheck this box",
  ];
  for (const label of labels) {
    assert.ok(DOUBLE_NEGATIVE_PATTERNS.some((p) => p.test(normalizeQuotes(label))), `missed: ${label}`);
  }
});

test("double-negative patterns do not flag ordinary consent wording", () => {
  const labels = [
    "Email me order updates",
    "I accept the Terms of Service",
    "Send me marketing emails (optional)",
  ];
  for (const label of labels) {
    assert.ok(!DOUBLE_NEGATIVE_PATTERNS.some((p) => p.test(label)), `false positive: ${label}`);
  }
});

test("opt-out wording is recognised so a pre-ticked one can be flagged", () => {
  assert.ok(OPT_OUT_LABEL_PATTERNS.some((p) => p.test("Do not send me order updates")));
  assert.ok(!OPT_OUT_LABEL_PATTERNS.some((p) => p.test("Send me order updates")));
});

console.log(`\n${failures === 0 ? "ALL TESTS PASSED" : failures + " TEST(S) FAILED"}`);
process.exit(failures === 0 ? 0 : 1);
