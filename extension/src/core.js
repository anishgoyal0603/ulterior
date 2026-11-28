/**
 * Ulterior extension -- shared, DOM-free logic.
 *
 * Everything here is pure: money parsing, the pattern vocabularies, site
 * keys, PII redaction, colour contrast and the evidence report. Keeping it
 * free of `document` and `chrome` means it runs unchanged in Node, so
 * tests/extension_core.test.js can check the arithmetic the findings rest on
 * without a browser.
 *
 * NETWORK: the checks never make a network request. The one exception is
 * "Report this page" in the popup, which is OFF by default, needs a separate
 * browser permission the person grants, shows exactly what will be sent, and
 * sends only when they press Send (popup.js, sendReport). A test fails the
 * build if a network call appears anywhere else.
 */
(function (root) {
  "use strict";

  const VERSION = "2.2.0";

  // ---------------------------------------------------------------- money
  //
  // Indian prices come as ₹1,23,456.50, Rs. 499, Rs 499/-, INR 1,299,
  // रु. 499 and "₹ 2,499" with a space. The lakh grouping (1,23,456) is why this cannot be
  // a plain thousands-separator regex.
  //
  // A minus counts only when it touches the amount ("-₹50" off a wallet). In
  // "Delivery Fee - ₹49" the dash is punctuation, and reading it as a minus
  // turned a fee into a refund.
  //
  // "rs" and "inr" count only when they are not the end of a word: without
  // the look-behind, "SNEAKERS 55", "OFFERS 200" and "YEARS 2" were read as
  // rupees. A minus with a space after it ("- \u20b9 130", the way MakeMyTrip
  // writes a discount) counts only when it starts the text or follows "(" --
  // never after a word, so "Fee - \u20b949" stays a charge.
  const AMOUNT_RE =
    /((?:-|\u2212)(?=\u20b9|(?<![A-Za-z])(?:rs\.?|inr)|\u0930\u0941)|(?:^|(?<=\())\s*(?:-|\u2212)\s(?=\s*(?:\u20b9|(?<![A-Za-z])(?:rs\.?|inr)|\u0930\u0941)))?\s*(?:\u20b9|(?<![A-Za-z])(?:rs\.?|inr)|\u0930\u0941\u092a\u092f\u0947|\u0930\u0941\.?)\s*([0-9]{1,3}(?:,[0-9]{2,3})+(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)(?:\s*\/-)?/gi;

  function parseAmounts(text) {
    const out = [];
    if (!text) return out;
    AMOUNT_RE.lastIndex = 0;
    let m;
    while ((m = AMOUNT_RE.exec(text)) !== null) {
      const value = parseFloat(m[2].replace(/,/g, ""));
      if (!isFinite(value)) continue;
      out.push({ value: m[1] ? -value : value, raw: m[0].trim(), index: m.index });
    }
    return out;
  }

  function formatINR(value) {
    if (value === null || value === undefined || !isFinite(value)) return "";
    const neg = value < 0;
    const abs = Math.abs(value);
    // Rupees and paise from ONE rounding: 99.999 is ₹100, not "₹99.100".
    const cents = Math.round(abs * 100);
    const whole = Math.floor(cents / 100);
    const paise = cents % 100;
    const s = String(whole);
    // Indian grouping: last three digits, then pairs.
    let grouped = s;
    if (s.length > 3) {
      const head = s.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
      grouped = head + "," + s.slice(-3);
    }
    return (neg ? "-" : "") + "₹" + grouped + (paise ? "." + String(paise).padStart(2, "0") : "");
  }

  // ---------------------------------------------------------- vocabularies
  //
  // English first, then Hindi (Devanagari) and Hinglish (Hindi in Latin
  // letters), because Indian shops mix all three: "Jaldi karein! Sirf 2 bache
  // hain", "सुविधा शुल्क". Devanagari alternatives sit OUTSIDE the \b groups on
  // purpose: JavaScript's \b only knows ASCII letters, so \bबीमा\b never matches.
  // All page text is NFC-normalised (see normalizeQuotes) before it is tested,
  // so a nukta typed either way ("ड़" or "ड" + "़") still matches.

  // Fees that are typically revealed only at the last step. Taxes are left
  // out on purpose: whether GST belongs in a displayed price depends on the
  // product and the display, and a tax line is not by itself a trick.
  const FEE_LABEL_RE = new RegExp([
    "convenience\\s*(fees?|charges?)", "platform\\s*(fees?|charges?)", "handling\\s*(fees?|charges?)",
    "internet\\s*handling", "booking\\s*(fees?|charges?)", "service\\s*(fees?|charges?)",
    "payment\\s*(gateway|processing)\\s*(fees?|charges?)?", "processing\\s*(fees?|charges?)",
    "packag(ing|e)\\s*(fees?|charges?)", "packing\\s*(fees?|charges?)",
    "small\\s*(cart|order)\\s*(fees?|charges?)", "surge\\s*(fees?|charges?)",
    "rain\\s*(fees?|charges?)", "late\\s*night\\s*(fees?|charges?)",
    "delivery\\s*(partner\\s*)?(fees?|charges?)", "shipping\\s*(fees?|charges?)",
    "marketplace\\s*fees?", "order\\s*processing", "other\\s*charges",
    "restaurant\\s*(packaging\\s*)?charges?", "fees?\\s*(and|&)\\s*surcharges?",
    "airport\\s*(development|user)\\s*fees?", "ticket\\s*booking\\s*(fees?|charges?)",
    "सुविधा\\s*शुल्क|प्लेटफ़ॉर्म\\s*शुल्क|प्लेटफॉर्म\\s*शुल्क|हैंडलिंग\\s*शुल्क|डिलीवरी\\s*शुल्क|बुकिंग\\s*शुल्क|सेवा\\s*शुल्क|पैकेजिंग\\s*शुल्क|सुविधा\\s*प्रभार",
  ].join("|"), "i");

  // Ordered strongest first. The first label in this list found on a page is
  // the one whose amount is treated as the total.
  const TOTAL_LABELS = [
    /\b(total\s+)?amount\s+payable\b/i, /\btotal\s+payable\b/i, /\bnet\s+payable\b/i,
    /\byou(\s+will)?\s+pay\b/i, /\bamount\s+to\s+(be\s+)?paid\b/i, /\bto\s+pay\b/i,
    /कुल\s*(देय\s*)?(राशि|देय|भुगतान|योग)/, /(देय|भुगतान)\s*राशि/,
    /\bgrand\s+total\b/i, /\bfinal\s+(amount|price|total)\b/i, /\border\s+total\b/i,
    /\btotal\s+(amount|price|fare|cost)\b/i, /^\s*total\s*:?\s*$/i,
    /^\s*(order\s+|grand\s+)?total\s*[:\-]?\s*(₹|rs\.?|inr)\s*[\d,]+(\.\d+)?\s*$/i,
  ];

  const PAY_BUTTON_RE = /\b(pay|proceed\s+to\s+pay|pay\s+now|place\s+order|confirm\s+(and|&)\s+pay|make\s+payment|pay\s+karein)\b|भुगतान\s*करें|अभी\s*भुगतान/i;

  const BUY_VERB_RE = /\b(book(\s+now)?|select|buy(\s+now)?|add\s+to\s+(cart|bag|basket)|reserve|choose|view\s+(fares|seats|deal)|get\s+tickets|book\s+tickets|continue|proceed|subscribe|start\s+(free\s+)?trial|join\s+now)\b|अभी\s*खरीदें|कार्ट\s*में\s*(जोड़ें|डालें)|बुक\s*करें|टिकट\s*बुक/i;

  // Add-ons that cost money when left ticked.
  const PAID_ADDON_RE = /\b(insur(e|ance)|protect(ion|ed)?|warranty|secure\s+(my|your)\s+(trip|order|booking)|donat(e|ion)|contribut(e|ion)|charity|tip\b|round\s+up|priority|express|gift\s*wrap|membership|cover(age)?\b|assurance|guarantee|extended|meal|add[\s-]?on|travel\s+insurance|fast\s*forward|premium)\b|बीमा|दान|सहयोग\s*राशि|सुरक्षा\s*कवच/i;

  // Things that are ticked FOR you but in your favour, or are not purchases at
  // all. A pre-ticked "Use ₹50 wallet balance" is not basket sneaking.
  const BENEFIT_RE = /\b(wallet|balance|cash\s*back|coupon|discount|offer\s+applied|points|super\s*coins|credits?|rewards?|save|savings|use\s+my|apply|terms|agree|remember\s+me|keep\s+me|signed\s+in|notify|whats\s*app|updates|newsletter|gst\s+invoice|billing\s+address|same\s+as)\b|वॉलेट|कैशबैक|कूपन|छूट|शर्तें|सहमत/i;

  const DECLINE_RE = /^\s*(no[,.]?\s*thanks?|no\s+thank\s+you|skip(\s+this)?|not\s+now|maybe\s+later|remind\s+me\s+later|decline|i'?ll\s+(pass|skip)|continue\s+without|no[,.]?\s+i|i\s+don'?t\s+want|i\s+do\s+not\s+want|nahi[,.]?\s*(chahiye|thanks)|abhi\s+nahi)\b|^\s*(नहीं,?\s*धन्यवाद|अभी\s*नहीं|छोड़ें|रहने\s*दें|मुझे\s*नहीं\s*चाहिए)/i;

  // Kept in sync with capture/state_extractor.py's URGENCY_PATTERNS, minus
  // the bare "in stock" catch-all: on a real store that line is on every
  // product page, and as a page-level flag it only ever produced noise.
  const URGENCY_PATTERNS = [
    /only\s+\d+\s+(left|remaining|seats?|units?|at this price)/i,
    /\bhurry\b/i, /offer ends?/i, /limited time/i,
    /\d+\s+people (are\s+)?(viewing|booked|bought|seeing)/i,
    /selling fast/i, /sell(ing)? out (fast|quickly)/i, /\bwill sell out\b/i,
    /high[\s-]?demand/i, /in high demand/i,
    /\d+\s+claimed/i, /low(\s+in)? stock/i, /limited stock/i,
    /reserved for \d+/i, /sale ends? (once|when|in)/i,
    /(deal|offer) ends? in/i, /\bends in\s*:?\s*\d/i, /\bexpires in\b/i,
    /\bfilling fast\b/i, /\balmost (gone|sold out|full)\b/i,
    // Hinglish
    /\bjaldi\s+(karein|karo|kare|kijiye)\b/i, /\bsirf\s+\d+\s+(bache|bacha|baki|baaki|left)\b/i,
    /\boffer\s+(khatam|samapt)\b/i, /\bstock\s+khatam\b/i,
    // Hindi
    /जल्दी\s*करें/i, /(सिर्फ़|सिर्फ|केवल)\s*\d+\s*(बचे|बचा|शेष)/i, /ऑफ़र\s*(खत्म|समाप्त)/i, /ऑफर\s*(खत्म|समाप्त)/i, /सीमित\s*समय/i, /तेज़ी\s*से\s*बिक/i,
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
    /\b(i'?ll|i will) take (the|my) (risk|chances?)\b/i,
    /\bunprotected\b/i,
    /pay(ing)?\s*full price/i,
    /like (paying )?full price/i,
    /don'?t (mind )?miss(ing)? out/i,
    /don'?t want \d+%/i,
    // Hinglish
    /\bmujhe\s+(bachat|discount|offer|suraksha|protection)\s+nahi\s+chahiye\b/i,
    /\bmain\s+risk\s+(lunga|lungi|le\s+lunga)\b/i,
    // Hindi
    /मुझे\s*(बचत|छूट|सुरक्षा)\s*नहीं\s*चाहिए/i, /मैं\s*जोखिम\s*(लूंगा|लूँगा|लूंगी|उठाऊंगा)/i, /बिना\s*(सुरक्षा|बीमा)\s*के/i,
  ];

  const RECURRING_BILLING_PATTERNS = [
    /\bauto[\s-]?renew(s|al|ing)?\b/i, /\brecurring\b/i, /\bsubscription\b/i,
    /\bper\s+(month|year|week)\b/i, /\/\s*(mo|month|yr|year|week)\b/i,
    /\bbilled\s+(monthly|annually|yearly)\b/i,
    /\bmonthly\b/i, /\byearly\b/i, /\bannually\b/i, /\bfree trial\b/i,
    /हर\s*महीने/i, /मासिक/i, /वार्षिक/i, /सदस्यता/i, /ऑटो[\s-]?रिन्यू/i,
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

  // Quantity words. Listing prices for tickets and fares are usually per
  // person, so "2 tickets" at ₹250 means ₹500 is the honest expectation.
  // Kept in step with _is_shaming in detectors/layer3_language.py. "I don't
  // want/need ..." and "without protection" are also how a NEUTRAL decline is
  // written ("No, I don't need a warranty"); alone they count only when the
  // line also puts a value or a self-judgement on saying no.
  const SOFT_GUILT = [0, 4, 6];        // indexes into GUILT_PATTERNS
  const STRONG_DONT_RE = /\bi don'?t (like|care about|mind|hate|despise)\b/i;
  const VALUE_WORDS_RE = /\b(sav(e|ing|ings)|discounts?|deals?|offers?|money|free|benefits?|better|smart|win(ning|s)?|lucky|best|cheap|rather|kick|protect(ed|ing)?\s+(my|me|myself)|secure\s+my|safe(ty)?|exclusive|rewards?|full\s+price|pay\s+more|miss(ing)?\s+out|risk|luck|style|stylish|healthy|happy|fun|great|amazing|special|vip|perks?)\b/i;
  function isShaming(text) {
    const t = normalizeQuotes(text);
    const hits = GUILT_PATTERNS.map((p, i) => (p.test(t) ? i : -1)).filter((i) => i >= 0);
    if (!hits.length) return false;
    if (STRONG_DONT_RE.test(t)) return true;
    if (hits.every((i) => SOFT_GUILT.includes(i)) && !VALUE_WORDS_RE.test(t)) return false;
    return true;
  }

  /** Does this text tell the shopper that charges come ON TOP of the price?
   *  "Inclusive of all fees", "No hidden fees", "Zero delivery charges" and
   *  "Delivery charges: FREE" say the opposite, and used to count as a
   *  disclosure -- which switched DP-08 off for the checkout that then added
   *  fees. Negated and inclusive mentions are removed before the test. */
  const NEGATED_CHARGE_RE = new RegExp([
    // "No hidden fees", "Zero delivery charges", "Inclusive of all fees"
    "\\b(no|zero|free|without|waived|nil|incl\\.?|including|inclusive(\\s+of)?|includes?)(\\s+(all|any|hidden|extra|additional|delivery|shipping|convenience|platform|booking|service|handling|other))*\\s+(fees?|charges?)\\b",
    // "Inclusive of all taxes and fees"
    "\\binclusive\\s+of[^.;\\n]{0,40}\\b(fees?|charges?)\\b",
    // "All fees included", "Delivery charges are included"
    "\\b(fees?|charges?)\\s+(are\\s+)?(included|inclusive|waived)\\b",
    // "Delivery charges: FREE", "Fee ₹0"
    "\\b(fees?|charges?)\\s*[:\\-–]?\\s*(free|nil|waived|₹\\s*0(?![\\d.,])|0(?![\\d.,]))",
    // "₹0 delivery fee"
    "(₹|rs\\.?|inr)\\s*0(?![\\d.,])\\s+(\\w+\\s+)?(fees?|charges?)\\b",
  ].join("|"), "gi");
  const DISCLOSES_CHARGES_RE = /\b(fees?|charges?|excl\.?|exclusive\s+of|plus\s+taxes)\b|\+\s*(₹|rs\.?\s*\d)/i;
  function disclosesCharges(text) {
    return DISCLOSES_CHARGES_RE.test(String(text || "").replace(NEGATED_CHARGE_RE, " "));
  }

  const QTY_RE = /(?:\b|^)(\d{1,2})\s*(tickets?|seats?|adults?|travell?ers?|passengers?|guests?|persons?|people|pax|टिकट|यात्री|व्यक्ति)/gi;
  const ITEM_COUNT_RE = /\b(\d{1,2})\s*items?\b|\bqty\s*[:.]?\s*(\d{1,2})\b|\bquantity\s*[:.]?\s*(\d{1,2})\b/gi;

  // ------------------------------------------------- DP-04 Forced Action
  // A box you must tick to proceed, worded so that ticking it ALSO signs you
  // up for marketing or data sharing.
  // The TERMS themselves must be named: "I agree to receive offers" on its
  // own is an optional marketing box, not a bundle with the terms.
  const CONSENT_TERMS_RE = /\b(terms(\s+(of\s+(use|service)|and\s+conditions|&\s*conditions))?|t\s*&\s*c'?s?|conditions\s+of\s+(use|sale)|privacy\s+policy|user\s+agreement)\b|शर्तों|नियम\s*(व|और)\s*शर्तें/i;
  // Kept in step with MARKETING_CONSENT_TERMS in detectors/layer1_flow.py:
  // selling TO the shopper, or their data to advertisers. Not the lawful
  // notice ("how my data is shared with third parties"), not "delivery
  // partners to fulfil this order", not "order updates via SMS".
  const CONSENT_MARKETING_RE = /\b(marketing|promotional|promotions?|newsletters?|personali[sz]ed\s+ads|targeted\s+ads|advertis(ing|ers?)|affiliates?|offers?\s+(and|&)\s+updates|(offers?|deals?)\b[^.]{0,40}\b(whats\s*app|sms|emails?)|(whats\s*app|sms|emails?)\b[^.]{0,40}\b(offers?|deals?|promotions?)|receive\s+(exclusive\s+|special\s+)?(offers?|deals?)|share\s+(my|your)\s+(data|details|information)\b[^.]{0,60}\b(advertis\w*|affiliates?|marketing\s+partners?))\b|प्रचार|ऑफ़र|ऑफर|विज्ञापन/i;
  // A screen that will not let you continue on the web.
  const APP_WALL_RE = /\b(download|install|get|open)\s+(the\s+|our\s+)?app\b|ऐप\s*(डाउनलोड|इंस्टॉल)/i;
  const APP_WALL_BLOCK_RE = /\b(to\s+(continue|proceed|view|see|unlock|access|book|order)|continue\s+(in|on)\s+(the\s+)?app|only\s+(on|in)\s+(the\s+)?app|available\s+only\s+on\s+(the\s+)?app)\b/i;
  const CONTINUE_ON_WEB_RE = /\b(continue\s+(on|in)\s+(the\s+)?(web|browser|website|site)|not\s+now|maybe\s+later|no\s+thanks|close|dismiss|skip|use\s+(the\s+)?(web|website))\b|^\s*[x×✕✖]\s*$/i;

  // ---------------------------------------------- DP-05 Subscription Trap
  const SUBSCRIBE_CTA_RE = /\b(subscribe(\s+now)?|start\s+(my\s+|your\s+)?(free\s+)?trial|try\s+(it\s+)?free|join\s+(now|prime|plus|pass|membership)|activate\s+(membership|plan|trial)|get\s+(prime|plus|pass|membership|one|premium)|upgrade\s+(now|to))\b|सदस्यता\s*लें|फ्री\s*ट्रायल/i;
  const CANCEL_INFO_RE = /\b(cancel(\s+(anytime|any\s+time|online|at\s+any\s+time|in\s+(one|1)\s+click|from\s+(your\s+)?(account|settings|profile)))|how\s+to\s+cancel|cancellation\s+(policy|terms)|no\s+commitment|cancel\s+before)\b|कभी\s*भी\s*रद्द/i;
  const TRIAL_CONVERTS_RE = /\b(after\s+(the\s+)?(free\s+)?trial|trial\s+(ends|period)|then\s+(₹|rs\.?|inr)\s*[\d,]+|auto[\s-]?renews?|will\s+be\s+charged|charged\s+automatically)\b/i;

  // ---------------------------------------- DP-09 Disguised Advertisement
  const SPONSORED_LABEL_RE = /^\s*(sponsored|ad|ads|advertisement|promoted|paid\s+partnership|प्रायोजित|विज्ञापन)\s*$/i;

  // ---------------------------------------------------- DP-13 Rogue Malware
  // Presentation only: scare text next to a "fix it" button. Nothing is ever
  // downloaded or opened to check a file.
  const SCARE_RE = /\b(your\s+(device|phone|mobile|computer|pc|system|browser)\s+(is|has\s+been|may\s+be|might\s+be)\s+(infected|at\s+risk|hacked|compromised|damaged)|virus(es)?\s+(detected|found)|\d+\s+viruses|(critical\s+)?security\s+(alert|warning)|battery\s+(is\s+)?(damaged|infected)|trojan|malware\s+(detected|found))\b|वायरस|संक्रमित/i;
  const SCARE_CTA_RE = /\b(download|install|scan\s+now|clean\s+now|remove\s+(now|virus(es)?)|fix\s+now|repair\s+now|update\s+now|protect\s+now|call\s+(now|support|us))\b|अभी\s*(साफ़|साफ|ठीक)\s*करें/i;
  const EXECUTABLE_RE = /\.(exe|apk|msi|dmg|bat|scr|cmd|vbs|jar|pkg)(\?|#|$)/i;

  // ---------------------------------------------------- DP-10 Nagging
  const DIALOG_HINT_RE = /\b(sign\s*in|sign\s*up|log\s*in|login|register|download|install|app|notifications?|subscribe|offers?|discount|coupon|rate|review|location|newsletter|updates?|membership|premium|upgrade|whats\s*app)\b|%\s*off\b|लॉगिन|ऐप|ऑफ़र/i;

  // ------------------------------------------------------------- privacy
  const PII_PATTERNS = [
    [/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, "[EMAIL]"],
    // Card shapes only (kept in step with app/privacy.py): timestamps such
    // as "2026-11-28 15-08-30" are not card numbers.
    [/\b(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{4,5})\b/g, "[CARD]"],
    [/\b\d{4}[ -]?\d{4}[ -]?\d{4}\b/g, "[ID]"],
    // Not inside a longer code: "SKU9876543210" is not a phone number.
    [/(?:\+?91[\s-]?|(?<![A-Za-z0-9])0|(?<![A-Za-z0-9]))[6-9](?:[\s-]?\d){9}\b/g, "[PHONE]"],
    [/\b[A-Z]{5}[0-9]{4}[A-Z]\b/g, "[PAN]"],
    [/\b(pnr|booking\s*id|order\s*id|booking\s*ref(erence)?)\s*(no\.?|number)?\s*[:#-]?\s*[A-Z0-9-]{5,}/gi, "$1 [REF]"],
  ];

  function redact(text) {
    if (!text) return text;
    let out = String(text);
    for (const [pattern, replacement] of PII_PATTERNS) out = out.replace(pattern, replacement);
    return out;
  }

  /** Curly quotes to straight ones, and Unicode NFC so a Hindi nukta typed
   *  as one character or as two still matches the vocabularies above. */
  function normalizeQuotes(s) {
    return String(s || "").normalize("NFC").replace(/[‘’]/g, "'").replace(/[“”]/g, '"');
  }

  function clip(s, n) {
    s = String(s || "").replace(/\s+/g, " ").trim();
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  // ---------------------------------------------------------------- sites
  // Where the extension runs on its own. Everywhere else it waits for you to
  // press "Scan this page". manifest.json lists the same sites; a test
  // fails if the two lists drift apart.
  const SUPPORTED_SITES = [
    // shopping
    "amazon.in", "flipkart.com", "myntra.com", "ajio.com", "meesho.com", "nykaa.com",
    "nykaafashion.com", "tatacliq.com", "jiomart.com", "snapdeal.com", "firstcry.com",
    "croma.com", "reliancedigital.in", "lenskart.com", "bigbasket.com",
    // food and quick commerce
    "swiggy.com", "zomato.com", "blinkit.com", "zeptonow.com",
    // tickets and travel
    "bookmyshow.com", "district.in", "makemytrip.com", "goibibo.com", "cleartrip.com",
    "yatra.com", "ixigo.com", "easemytrip.com", "redbus.in", "trivago.in", "booking.com",
    "agoda.com", "goindigo.in", "airindia.com", "spicejet.com", "akasaair.com", "oyorooms.com",
  ];

  const TWO_PART_TLDS = new Set(["co.in", "net.in", "org.in", "gov.in", "ac.in", "firm.in",
                                 "gen.in", "ind.in", "co.uk", "com.au", "co.jp", "com.sg"]);

  /** "www.flights.makemytrip.com" -> "makemytrip.com". A booking that opens
   *  its review page on another subdomain is still the same journey. */
  function siteOf(hostname) {
    const h = String(hostname || "").toLowerCase().replace(/\.$/, "");
    if (/^\d+\.\d+\.\d+\.\d+$/.test(h) || !h.includes(".")) return h;
    const parts = h.split(".");
    const lastTwo = parts.slice(-2).join(".");
    if (TWO_PART_TLDS.has(lastTwo) && parts.length >= 3) return parts.slice(-3).join(".");
    return lastTwo;
  }

  const PRODUCT_ID_PARAMS = ["product_id", "productId", "productid", "pid", "item_id", "itemId", "sku", "variant", "id"];

  /** "product_id=42" from a query-routed product URL, or "". */
  function productIdParam(u) {
    for (const name of PRODUCT_ID_PARAMS) {
      const v = u.searchParams.get(name);
      if (v && /^[A-Za-z0-9_-]{1,40}$/.test(v)) return `${name}=${v}`;
    }
    return "";
  }

  /** A stable id for "this product", for the on-device price history.
   *  Only the product identifier is kept, never the full URL. */
  function productKey(urlString, canonicalHref) {
    let u;
    try { u = new URL(urlString); } catch (e) { return null; }
    const site = siteOf(u.hostname);
    const path = u.pathname;
    let m;
    if (/^amazon\./.test(site) && (m = path.match(/\/(?:dp|gp\/product|gp\/aw\/d)\/([A-Z0-9]{10})/i))) {
      return `${site}:${m[1].toUpperCase()}`;
    }
    if (site === "flipkart.com") {
      const pid = u.searchParams.get("pid");
      if (pid) return `${site}:${pid}`;
      if ((m = path.match(/\/p\/(itm[a-z0-9]+)/i))) return `${site}:${m[1]}`;
    }
    if (site === "myntra.com" && (m = path.match(/\/(\d{5,})\/buy/))) return `${site}:${m[1]}`;
    if (site === "ajio.com" && (m = path.match(/\/p\/([a-z0-9_]+)/i))) return `${site}:${m[1]}`;
    if (site === "meesho.com" && (m = path.match(/\/p\/([a-z0-9]+)/i))) return `${site}:${m[1]}`;
    if (site === "nykaa.com" && (m = path.match(/\/p\/(\d+)/))) return `${site}:${m[1]}`;
    // Anything else: the canonical path if the page declares one, else ours.
    let p = path;
    if (canonicalHref) {
      try {
        const c = new URL(canonicalHref, urlString);
        if (siteOf(c.hostname) === site) p = c.pathname;
      } catch (e) { /* keep our own path */ }
    }
    p = p.replace(/\/+$/, "") || "/";
    // Stores that route products by query string (OpenCart's
    // ?route=product/product&product_id=42, many PHP shops) would otherwise
    // collapse every product into one key. Only a known product-id
    // parameter is kept -- never the rest of the query.
    const idParam = productIdParam(u);
    if (idParam) return `${site}:${p.slice(0, 120)}?${idParam}`;
    if (p === "/") return null;
    return `${site}:${p.slice(0, 160)}`;
  }

  // -------------------------------------------------------------- colour
  function parseColor(str) {
    const m = String(str || "").match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+%?))?\s*\)/i);
    if (!m) return null;
    let a = 1;
    if (m[4] !== undefined) a = m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
    return { r: +m[1], g: +m[2], b: +m[3], a };
  }

  function luminance(c) {
    const ch = [c.r, c.g, c.b].map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  }

  /** WCAG 2.1 contrast ratio, 1..21. */
  function contrastRatio(fg, bg) {
    const a = luminance(fg), b = luminance(bg);
    const hi = Math.max(a, b), lo = Math.min(a, b);
    return (hi + 0.05) / (lo + 0.05);
  }

  /** Blend a translucent colour over an opaque one. */
  function over(top, bottom) {
    const a = top.a === undefined ? 1 : top.a;
    return { r: top.r * a + bottom.r * (1 - a), g: top.g * a + bottom.g * (1 - a),
             b: top.b * a + bottom.b * (1 - a), a: 1 };
  }

  // ------------------------------------------------------------- timers
  /** "09:59", "1:02:03", "08m 12s", "2h 10m 5s" -> seconds. A bare "2h 15m"
   *  (no seconds) returns null: that is how flight DURATIONS are written,
   *  and a duration is not a countdown. */
  function parseCountdown(text) {
    const t = String(text || "");
    // Up to DD:HH:MM:SS, never part of a longer clock (the old pattern read
    // "02:14:33:12" as 02:14:33 and an honest multi-day timer looked
    // frozen), and never one end of a time range ("10:00 - 18:00").
    let m = t.match(/(?<![:\d])(?:(\d{1,2}):)?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![:\d])(?!\s*(?:[-–—]|to)\s*\d{1,2}:\d{2})/);
    if (m && !/\d{1,2}:\d{2}\s*(?:[-–—]|to)\s*$/.test(t.slice(0, m.index))) {
      if (m[1] !== undefined && m[2] !== undefined) return (+m[1]) * 86400 + (+m[2]) * 3600 + (+m[3]) * 60 + (+m[4]);
      return (+(m[1] || m[2] || 0)) * 3600 + (+m[3]) * 60 + (+m[4]);
    }
    m = t.match(/\b(?:(\d{1,2})\s*h(?:rs?|ours?)?\s*)?(\d{1,2})\s*m(?:in(?:s|utes?)?)?\s*(\d{1,2})\s*s(?:ec(?:s|onds?)?)?\b/i);
    if (m) return (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]);
    return null;
  }

  function fmtClock(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
    return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  // ------------------------------------------------------------- journey
  /**
   * The drip-pricing decision, kept pure so it can be tested on numbers.
   *
   *   start  -- {price, source, disclosed}: what the shopper picked
   *   total  -- the checkout total
   *   qty    -- persons/tickets (listing prices are usually per person)
   *   fees   -- [{label, value}] named fee lines found on the checkout page
 *   addOns -- the total of add-ons found pre-selected on this page
   *
   * Returns {kind: "match" | "drip" | "unexplained" | "disclosed", ...}.
   * Only "drip" becomes a finding. The others are shown as notes, because a
   * higher total that we cannot tie to named fees could be the shopper's own
   * choices (a seat, a meal, a bigger size), and saying otherwise would be a
   * guess dressed up as an observation.
   */
  function judgeJourney(start, total, qty, fees, addOns) {
    qty = qty && qty > 0 ? qty : 1;
    const expected = start.price * qty;
    // Add-ons already flagged as pre-selected are explained by that finding;
    // what is left over is what the fees have to account for.
    const addOnSum = addOns > 0 ? addOns : 0;
    const diff = total - expected - addOnSum;
    const tolerance = Math.max(1, expected * 0.02);
    const feeSum = (fees || []).reduce((s, f) => s + (f.value > 0 ? f.value : 0), 0);
    const base = { expected, total, diff, feeSum, qty, addOnSum, start: start.price };
    if (diff <= tolerance) return Object.assign({ kind: "match" }, base);
    if (start.disclosed) return Object.assign({ kind: "disclosed" }, base);
    if (feeSum > 0 && feeSum >= diff * 0.5) return Object.assign({ kind: "drip" }, base);
    return Object.assign({ kind: "unexplained" }, base);
  }

  // ------------------------------------------------- price comparison
  //
  // To recognise the same product on another shop without storing its name,
  // the title is cut into words and each word is stored as a short scrambled
  // code (FNV-1a hash). Two products match when most of their codes match.
  // "Apple iPhone 15 (Blue, 128 GB)" and "Apple iPhone 15 128GB - Blue" give
  // the same set. Kept on the device, like the rest of the history.
  const STOPWORDS = new Set(["the", "a", "an", "and", "or", "for", "with", "of", "in", "on", "by", "to",
    "from", "new", "latest", "buy", "online", "best", "price", "india", "at", "pack", "combo", "set",
    "men", "women", "mens", "womens", "unisex", "kids", "free", "delivery", "offer", "sale"]);

  function titleTokens(title) {
    let t = normalizeQuotes(title).toLowerCase();
    t = t.replace(/(\d+(?:\.\d+)?)\s*(gb|tb|mb|ml|l|ltr|litre|liter|kg|g|gm|mm|cm|inch|in|w|mah|hz)\b/g, "$1$2");
    t = t.replace(/[^\p{L}\p{N}]+/gu, " ");
    const out = [];
    for (const w of t.split(" ")) {
      if (!w || STOPWORDS.has(w)) continue;
      if (w.length < 2 && !/\d/.test(w)) continue;
      if (!out.includes(w)) out.push(w);
      if (out.length >= 14) break;
    }
    return out;
  }

  function fnv1a(str) {
    let h = 0x811c9dc5;
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return ("0000000" + h.toString(16)).slice(-8);
  }

  function fingerprint(title) {
    return titleTokens(title).map(fnv1a);
  }

  /** Share of codes the two fingerprints have in common (Jaccard), and how
   *  many. A match needs both: most of the words, and at least three. */
  function similarity(a, b) {
    if (!a || !b || !a.length || !b.length) return { score: 0, shared: 0 };
    const A = new Set(a), B = new Set(b);
    let shared = 0;
    for (const x of A) if (B.has(x)) shared++;
    return { score: shared / (A.size + B.size - shared), shared };
  }

  function sameProduct(a, b) {
    const s = similarity(a, b);
    return s.score >= 0.6 && s.shared >= 3;
  }

  // Where "Compare prices" can look. Plain search links that open in a new
  // tab only when the person clicks one. AFFILIATE stays empty until the team
  // has affiliate accounts; a tagged link must also be labelled as such.
  const COMPARE_SITES = [
    { site: "amazon.in", name: "Amazon", url: "https://www.amazon.in/s?k=" },
    { site: "flipkart.com", name: "Flipkart", url: "https://www.flipkart.com/search?q=" },
    { site: "croma.com", name: "Croma", url: "https://www.croma.com/searchB?q=" },
    { site: "reliancedigital.in", name: "Reliance Digital", url: "https://www.reliancedigital.in/search?q=" },
    { site: "myntra.com", name: "Myntra", url: "https://www.myntra.com/" },
    { site: "ajio.com", name: "AJIO", url: "https://www.ajio.com/search/?text=" },
    { site: "nykaa.com", name: "Nykaa", url: "https://www.nykaa.com/search/result/?q=" },
  ];
  const AFFILIATE = {};

  function compareLinks(title, currentSite) {
    const words = titleTokens(title).slice(0, 8).join(" ");
    if (!words) return [];
    return COMPARE_SITES.filter((c) => c.site !== currentSite).slice(0, 4).map((c) => ({
      name: c.name,
      url: c.site === "myntra.com" ? c.url + encodeURIComponent(words.replace(/ /g, "-"))
                                   : c.url + encodeURIComponent(words),
    }));
  }

  // ---------------------------------------------- opt-in page reports
  const REPORT_ORIGIN = "https://ulterior-api-production.up.railway.app";
  const REPORT_PATH = "/extension-reports";
  const COMMUNITY_PATHS = { put: "/prices", history: "/prices/history", latest: "/prices/latest", drops: "/prices/drops" };

  // ------------------------------------------------------- price tracking
  const DAY = 24 * 3600 * 1000;

  /** Combine your own price readings with community daily prices into one
   *  series, oldest first. Community days you also have a reading for keep
   *  yours (you saw it). */
  function mergeSeries(own, community) {
    const byDay = new Map();
    for (const c of community || []) byDay.set(Math.floor(c.t / DAY), { p: c.p, t: c.t, src: "community" });
    for (const o of own || []) byDay.set(Math.floor(o.t / DAY), { p: o.p, t: o.t, src: "you" });
    return [...byDay.values()].sort((a, b) => a.t - b.t);
  }

  function summarize(points, now, days) {
    const from = days ? now - days * DAY : -Infinity;
    const pts = (points || []).filter((x) => x.t >= from && x.p > 0);
    if (!pts.length) return null;
    let low = pts[0], high = pts[0], sum = 0;
    for (const x of pts) {
      if (x.p < low.p) low = x;
      if (x.p > high.p) high = x;
      sum += x.p;
    }
    return { count: pts.length, low: low.p, lowAt: low.t, high: high.p, highAt: high.t,
             avg: sum / pts.length, first: pts[0].t, points: pts };
  }

  /**
   * Is today's price a good one? Only said when there is enough history to
   * say it honestly: at least 3 readings spread over at least 7 days.
   * Kinds: "good" | "fair" | "high" | "unknown".
   */
  function dealVerdict(current, points, now) {
    now = now || Date.now();
    const s = summarize(points, now, 90);
    if (!s || s.count < 3 || now - s.first < 7 * DAY) {
      return { kind: "unknown", label: "Not enough price history yet",
               detail: `${s ? s.count : 0} price${s && s.count === 1 ? "" : "s"} recorded so far. Ulterior judges a price once it has seen it over at least a week.` };
    }
    const days = Math.max(7, Math.round((now - s.first) / DAY));
    if (current <= s.low * 1.01) {
      return { kind: "good", label: `Lowest price in ${days} days`,
               detail: `Average over that time: ${formatINR(Math.round(s.avg))}. Highest: ${formatINR(s.high)}.` };
    }
    const pct = Math.round(((current - s.avg) / s.avg) * 100);
    if (pct <= -5) {
      return { kind: "good", label: `${-pct}% below its usual price`,
               detail: `${days}-day average ${formatINR(Math.round(s.avg))}; lowest ${formatINR(s.low)}.` };
    }
    if (pct >= 5) {
      return { kind: "high", label: `${pct}% above its usual price`,
               detail: `${days}-day average ${formatINR(Math.round(s.avg))}; it has been as low as ${formatINR(s.low)}. Waiting may pay.` };
    }
    return { kind: "fair", label: "About its usual price",
             detail: `${days}-day average ${formatINR(Math.round(s.avg))}; lowest ${formatINR(s.low)}.` };
  }

  /**
   * "70% off M.R.P." is only a saving if the product ever sold near that
   * M.R.P. If in 90 days it never sold above a fraction of it, the real
   * saving is measured against what it actually sold for.
   */
  function discountCheck(mrp, current, points, now) {
    now = now || Date.now();
    if (!mrp || !current || mrp <= current * 1.02) return null;
    const off = Math.round((1 - current / mrp) * 100);
    const s = summarize(points, now, 90);
    if (!s || s.count < 3 || now - s.first < 7 * DAY) {
      return { kind: "info", text: `Shown as ${off}% off an M.R.P. of ${formatINR(mrp)}. Ulterior has not seen ` +
        "enough prices yet to check whether it ever sold at that M.R.P." };
    }
    if (s.high < mrp * 0.8) {
      const real = Math.max(0, s.high - current);
      const realPct = Math.round((real / s.high) * 100);
      return { kind: "warn", text: `Shown as ${off}% off ${formatINR(mrp)}, but in the last 90 days it never sold ` +
        `above ${formatINR(s.high)}. Against what it actually sold for, the saving is ${formatINR(real)} (${realPct}%).` };
    }
    return { kind: "ok", text: `${off}% off M.R.P. - and it did sell at up to ${formatINR(s.high)} in the last 90 days.` };
  }

  /** Product link kept for a watched item: scheme, host and path only. The
   *  query string (search terms, tracking ids, session data) is dropped. */
  function cleanProductUrl(u) {
    try {
      const x = new URL(u);
      if (!/^https?:$/.test(x.protocol)) return null;
      const idParam = productIdParam(x);   // the product id only, nothing else
      return x.origin + x.pathname + (idParam ? "?" + idParam : "");
    } catch (e) { return null; }
  }

  function suggestTarget(current) {
    const t = current * 0.95;
    const step = t >= 10000 ? 100 : t >= 1000 ? 10 : 1;
    return Math.max(1, Math.floor(t / step) * step);
  }

  /** Exactly what "Report this page" sends -- the same object the person is
   *  shown before they press Send. No URL, no account, no identifier. */
  function buildReportPayload(results) {
    return {
      site: results.site,
      version: VERSION,
      page_type: results.page,
      captured_at: new Date(results.scannedAt || Date.now()).toISOString(),
      findings: (results.findings || []).slice(0, 20).map((f) => ({
        code: f.code, tier: f.tier, title: clip(redact(f.title), 300),
        detail: clip(redact(f.detail || ""), 600),
        evidence: Object.fromEntries(Object.entries(f.evidence || {}).slice(0, 10)
          .map(([k, v]) => [String(k).slice(0, 60), clip(redact(String(v)), 300)])),
      })),
    };
  }

  // ---------------------------------------------- what the extension covers
  // Shown in the popup, so nobody has to take a pitch slide's word for it.
  const EXTENSION_COVERAGE = [
    ["DP-01", "full", "Countdowns that restart on reload; stock claims that never change across days"],
    ["DP-02", "full", "Paid add-ons selected before you touched anything"],
    ["DP-03", "full", "Decline options worded to shame you (English, Hindi, Hinglish)"],
    ["DP-04", "full", "Consent boxes that bundle marketing; 'install the app to continue' walls"],
    ["DP-05", "partial", "Sign-up pages with no way to cancel mentioned; trial-to-paid terms in fine print. The full cancel flow needs the server audit."],
    ["DP-06", "full", "A 'No thanks' drawn too faint or too small next to a big 'Yes'"],
    ["DP-07", "full", "The cart charging more for the product than the price you picked"],
    ["DP-08", "full", "Fees added between the price you picked and the total"],
    ["DP-09", "full", "'Sponsored' labels too faint or too small to notice"],
    ["DP-10", "full", "The same pop-up coming back after you closed it"],
    ["DP-11", "full", "Double negatives and opt-outs that are ticked for you"],
    ["DP-12", "full", "A repeating charge selected for you"],
    ["DP-13", "partial", "Fake virus or 'device infected' messages with a fix-it button. Presentation only: files are never opened."],
  ];

  /** What the checks saw, for real-site testing: page type, the price and
   *  total they read, the fees, counts and timing. Labels and numbers only. */
  function buildDiagnostics(results) {
    const r = results || {};
    const lines = [
      "Ulterior diagnostics", `Site: ${r.site}`, `Version: ${r.version}  Scan: ${r.scanMs} ms`,
      `Page type: ${r.page}`,
      `Total read: ${r.total ? r.total.value + ' ("' + r.total.label + '")' : "none"}`,
      `Fees read: ${(r.fees || []).map((f) => f.label + " " + f.value).join("; ") || "none"}`,
      `Price picked: ${r.journey ? r.journey.start + " from " + r.journey.source + " (" + r.journey.kind + ")" : "none"}`,
      `Product price: ${r.history ? r.history.current : "none"}`,
      `Findings: ${(r.findings || []).map((f) => f.code + "/" + f.tier).join(", ") || "none"}`,
      `Notes: ${(r.notes || []).length}`,
    ];
    if (r.debug) Object.keys(r.debug).forEach((k) => lines.push(`${k}: ${r.debug[k]}`));
    return lines.join("\n");
  }

  // -------------------------------------------------------------- report
  const PATTERN_NAMES = {
    "DP-01": "False Urgency", "DP-02": "Basket Sneaking", "DP-03": "Confirm Shaming",
    "DP-04": "Forced Action", "DP-05": "Subscription Trap", "DP-06": "Interface Interference",
    "DP-07": "Bait and Switch", "DP-08": "Drip Pricing", "DP-09": "Disguised Advertisement",
    "DP-10": "Nagging", "DP-11": "Trick Wording", "DP-12": "SaaS Billing", "DP-13": "Rogue Malware",
  };

  const DISCLAIMER =
    "These are automated observations of page characteristics that resemble patterns " +
    "defined in India's Guidelines for Prevention and Regulation of Dark Patterns, 2023. " +
    "They are not a legal determination, and the page may have changed since.";

  const HELPLINE =
    "To raise it: National Consumer Helpline - call 1915 or 1800-11-4000, " +
    "WhatsApp 8800001915, or file at consumerhelpline.gov.in.";

  /** Plain-text evidence for one page. Site only -- never the URL, whose
   *  path and query routinely carry booking ids, tokens and email addresses. */
  function buildReport(site, findings, when) {
    const d = when instanceof Date ? when : new Date();
    const stamp = d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric",
                                              hour: "2-digit", minute: "2-digit" });
    const lines = [
      "Ulterior - evidence report",
      `Site: ${site}  (page address deliberately not included)`,
      `Captured: ${stamp}`,
      `Ulterior extension ${VERSION}`,
      "",
    ];
    (findings || []).forEach((f, i) => {
      lines.push(`${i + 1}. ${f.code} ${PATTERN_NAMES[f.code] || f.name || ""} - ${f.tier}`);
      lines.push(`   ${f.title}`);
      if (f.detail) lines.push(`   ${f.detail}`);
      if (f.evidence) {
        Object.keys(f.evidence).forEach((k) => {
          lines.push(`   - ${k.replace(/_/g, " ")}: ${f.evidence[k]}`);
        });
      }
      lines.push("");
    });
    if (!findings || !findings.length) lines.push("Nothing was flagged on this page.", "");
    lines.push(DISCLAIMER, HELPLINE);
    return lines.join("\n");
  }

  const api = {
    VERSION, SUPPORTED_SITES, parseAmounts, formatINR, FEE_LABEL_RE, TOTAL_LABELS, PAY_BUTTON_RE, BUY_VERB_RE,
    PAID_ADDON_RE, BENEFIT_RE, DECLINE_RE, URGENCY_PATTERNS, GUILT_PATTERNS,
    RECURRING_BILLING_PATTERNS, DOUBLE_NEGATIVE_PATTERNS, OPT_OUT_LABEL_PATTERNS,
    QTY_RE, ITEM_COUNT_RE, disclosesCharges, isShaming, redact, normalizeQuotes, clip, siteOf, productKey,
    parseColor, contrastRatio, over, parseCountdown, fmtClock, judgeJourney,
    PATTERN_NAMES, DISCLAIMER, HELPLINE, buildReport,
    CONSENT_TERMS_RE, CONSENT_MARKETING_RE, APP_WALL_RE, APP_WALL_BLOCK_RE, CONTINUE_ON_WEB_RE,
    SUBSCRIBE_CTA_RE, CANCEL_INFO_RE, TRIAL_CONVERTS_RE, SPONSORED_LABEL_RE, SCARE_RE,
    SCARE_CTA_RE, EXECUTABLE_RE, DIALOG_HINT_RE, titleTokens, fnv1a, fingerprint, similarity,
    sameProduct, COMPARE_SITES, AFFILIATE, compareLinks, REPORT_ORIGIN, REPORT_PATH,
    buildReportPayload, EXTENSION_COVERAGE, buildDiagnostics,
    COMMUNITY_PATHS, DAY, mergeSeries, summarize, dealVerdict, discountCheck, cleanProductUrl, suggestTarget,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.UlteriorCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
