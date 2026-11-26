# Store listings — copy and paste

Everything the Chrome, Edge and Firefox store forms ask for. Make the upload
files first:

```
python scripts/package_extension.py
```

- Chrome Web Store and Edge Add-ons: `dist/ulterior-extension-<version>-chrome-edge.zip`
- Firefox Add-ons: `dist/ulterior-extension-<version>-firefox.zip`

Images are in `docs/store/`.

## Listing text (all three stores)

**Name:** Ulterior by The Odyssey

**Summary** (132 characters max — this is the manifest description):
Spots hidden fees, fake timers and other dark patterns while you shop, tracks prices and alerts you when they drop.

**Description:**

```
Ulterior is a second pair of eyes at checkout. It watches for the tricks that India's
Guidelines for Prevention and Regulation of Dark Patterns, 2023 describe — all 13 of
them — and tells you before you pay, with the page's own numbers as proof.

WHAT IT CATCHES
• Fees added after you picked a price. Ulterior remembers the fare, ticket or product
  you chose. When the total appears, it names every fee added on the way:
  "You picked this at ₹4,599; the total is now ₹5,148. ₹350 is a convenience fee."
• The cart charging more for the item than the price you picked.
• Pre-ticked insurance, donations and protection plans.
• Countdowns that start over when you reload, and "only 2 left" that never changes.
• A hidden "No thanks" next to a big "Yes", and guilt-trip wording.
• "Install the app to continue" walls and sign-up boxes that bundle marketing consent.
• Pop-ups that keep coming back after you close them.
• "Sponsored" labels too faint to notice.
• Sign-ups that hide how to cancel, and trial-to-paid terms in fine print.
• Double-negative checkboxes, surprise subscriptions, fake virus warnings.

Reads English, Hindi and Hinglish ("Jaldi karein! Sirf 2 bache hain", "सुविधा शुल्क").

PRICE TRACKER
• A price-history graph on every product page (15 days, 45 days, 90 days, 6 months, all).
• "Is this a good price?" — lowest in N days, below usual, about usual, or above usual.
• Fake-discount check: "70% off M.R.P." when the product never sold near that M.R.P.
• Watchlist with your target price, and a notification when it is reached.
• The same product you saw on another shop, and one-click searches on other stores.
• Optional community prices: share the prices you see (product id and price only) and
  get months of history from everyone else — off unless you switch it on.

HONEST BY DESIGN
Every finding says how strong its evidence is — provable, corroborated or indicative.
Claims nobody can check from a browser ("Only 2 left!") are shown as claims, not findings.
Findings are observations of resemblance to patterns in the Guidelines, not legal
conclusions. "Copy evidence" gives you a plain-text report for the National Consumer
Helpline (1915).

PRIVATE BY DESIGN
• What you browse stays in your browser. No account, no password, no tracking, no ads.
• Runs on its own only on popular Indian shopping, food and travel sites. Anywhere else
  it reads a page only when you press "Scan this page".
• Two optional features talk to the Ulterior server, both off unless you switch them on:
  community prices (product id and price only) and "Report this page" (you see exactly
  what is sent). Neither ever sends the page address or anything about you.

Built by team The Odyssey (IIPS, DAVV Indore). Open source:
https://github.com/anishgoyal0603/ulterior
```

**Category:** Shopping

**Language:** English

**Screenshots** (1280×800): `docs/store/1-cart-popup.png`, `docs/store/2-checkout-drip.png`,
`docs/store/3-flight-review-popup.png`, `docs/store/4-price-check.png`

**Small promo tile** (440×280): `docs/store/promo-440x280.png`

**Homepage URL:** https://ulterior-api-production.up.railway.app/

**Support URL:** https://github.com/anishgoyal0603/ulterior/issues

**Privacy policy URL:** https://ulterior-api-production.up.railway.app/privacy

## Chrome Web Store — Privacy practices tab

**Single purpose:**
Warn shoppers about dark patterns (hidden fees, pre-selected add-ons, fake countdowns and
similar tricks) on the shopping and booking pages they are viewing.

**Permission justifications:**

- **storage** — Keeps the results for open tabs and the price of the option the user picked
  (in session memory, cleared when the browser closes), the user's settings, and the
  optional on-device price history.
- **activeTab** — Lets the user run a one-time check of a site that is not on the
  extension's list by pressing "Scan this page".
- **scripting** — Injects the extension's own checking scripts into that one tab when the
  user presses "Scan this page".
- **alarms** — Checks the user's watchlist every six hours, only when community prices are on.
- **notifications** — Tells the user when a watched product reaches their target price.
- **Host permissions (content scripts on the listed shopping and travel sites)** — The checks
  read the prices, fees, totals, pre-selected options and pop-ups on the pages where people
  shop and book, and draw a small alert in the corner of the page when something is found.
- **Optional host permission (ulterior-api-production.up.railway.app)** — Requested only when
  the user switches on community prices or reporting: to share and fetch product prices
  (product id and price), and to send a report the user has reviewed.

**Remote code:** No, I am not using remote code.

**Data usage** — tick **Website content** (prices of products viewed, sent only with community
prices on; page findings, sent only in a report the user chooses to send) and **Web history**
(the product ids of pages viewed, with community prices on). Leave every other category
unticked.

Certify all three:
- I do not sell or transfer user data to third parties, outside of the approved use cases.
- I do not use or transfer user data for purposes that are unrelated to my item's single purpose.
- I do not use or transfer user data to determine creditworthiness or for lending purposes.

**Test instructions for the reviewer:**

```
No account is needed. The extension runs automatically on our public practice shop:
1. Open https://ulterior-api-production.up.railway.app/storefront/product.html
2. Click "Add to Cart" — the cart has a pre-ticked insurance add-on; the Ulterior alert
   appears in the bottom-right corner (Basket Sneaking, Confirm Shaming, Interface
   Interference).
3. Click "Proceed to Checkout" — the alert reports Drip Pricing: price picked ₹299,
   total ₹457, with the delivery and platform fees named.
4. Click the toolbar icon to see the findings, and "What Ulterior checks" for all 13.
Reporting is off by default; switching it on asks for permission to contact our server.
```

## Microsoft Edge Add-ons

Same zip as Chrome. Partner Center → Edge → Create new extension → upload
`...-chrome-edge.zip`. Paste the same description, privacy URL, screenshots and the same
reviewer instructions ("Notes for certification"). Edge registration is free.

## Firefox Add-ons (addons.mozilla.org)

Upload `...-firefox.zip` at https://addons.mozilla.org/developers/ → Submit a New Add-on →
"On this site". The package declares Mozilla's data-collection settings itself (nothing
required; "website content" optional, asked for when reporting is switched on). Paste the
same description and privacy URL. When asked for source code: the package is the source —
no build step, nothing minified. Firefox 140 or newer.
