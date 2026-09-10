# Auditing real websites, and checking whether a finding is real

Read this before you repeat any finding to anyone.

A finding from this tool is a statement that a **named company deceives its
customers**. That is a serious thing to say. In its first two runs against
pages nobody built for it, this tool produced **four false positives**, two of
them at its highest confidence tier. It is much better now — because those
four were found and fixed — but the lesson stands: the tool proposes, you
decide.

---

## 1. Can I just paste amazon.in into the dashboard?

You can type it. It will not give you a real audit, for three separate
reasons.

**It will not get in.** Amazon, Flipkart, Myntra, MakeMyTrip and their peers
run bot protection. An automated browser gets a challenge page or a stripped
page. The tool will report `INCONCLUSIVE`, which is the honest answer: it saw
nothing. You cannot publish that and you cannot learn from it.

**It cannot reach the interesting part.** Real dark patterns live in the
checkout — the pre-ticked insurance, the fee that appears at step four. That
is behind a login, and this tool never types credentials anywhere, by design.
It stops at the sign-in page.

**You do not have permission.** Automated access is against those sites'
terms of use. Crawling a company's checkout without asking, and then
publishing that the company is deceptive, is a bad position to be in — and
you would be doing it with a tool whose false-positive history you just read
above.

So: **do not crawl them.** The question underneath — *do the sites everyone
actually uses employ dark patterns?* — is a good question and worth answering.
There is a clean way to answer it.

---

## 2. The method that works: audit what you were shown

You are entitled to use Amazon as a customer. You are entitled to keep a
record of what it showed you. So:

1. **Browse the funnel yourself**, in your own browser, as an ordinary
   shopper. Product page → add to cart → cart → checkout. **Stop before
   paying.**
2. **Save each page**: `Ctrl+S`, choose **"Webpage, Complete"**, all into one
   folder. Name them so the order is obvious: `1-product.html`,
   `2-cart.html`, `3-checkout.html`.
3. **Screenshot each page too.** The screenshot is what you show a person;
   the saved HTML is what the tool measures.
4. Run:

```powershell
.\.venv\Scripts\python.exe scripts\audit_url.py --saved 1-product.html 2-cart.html 3-checkout.html
```

This is not a workaround. It is a better method:

- It is **a customer documenting their own transaction**, not an automated
  intrusion. No terms are broken and no server is hit by a robot.
- It gets **past the login**, because you logged in — so the tool finally sees
  the checkout, which is where the patterns are.
- The evidence is **reproducible by a human**. You have the screenshot. Anyone
  can look at it and agree or disagree.

You have already done the valuable half of this once: the checkout screenshots
you captured by hand exposed defects that no amount of code review had found.

### What this mode cannot check

**DP-01 (false urgency) will not fire on saved pages,** and that is correct.
Proving a countdown is fake requires loading the page *twice with real time
passing* and showing the number did not move. A saved file cannot do that. To
check a countdown yourself: note the number, wait 30 seconds, reload, and look
again. If it resets to exactly the same value, that is your evidence — take
two screenshots with the clock visible.

---

## 3. Read the verdict before you read the findings

The command-line harness ends every site with one of four verdicts. They are
not interchangeable.

| Verdict | What it means | Can you quote it? |
|---|---|---|
| **INCONCLUSIVE** | Almost nothing rendered — a challenge page, a login wall, or JavaScript that never finished | **No.** The tool saw nothing. |
| **PARTIAL** | Pages rendered but no price was found; the price detectors could not run | **No.** Half the tool was switched off. |
| **CLEAN** | Rendered, priced, nothing found | Yes — "we looked properly and found nothing." |
| **_n_ finding(s)** | Findings on pages genuinely read | Yes, **after** section 4. |

A "0 findings" result only means something when the verdict is CLEAN. This is
the single most important line in this document: **"no findings" and "the
crawler saw an empty page" look identical unless you check.**

---

## 4. Checking a finding, in order

Do these in order and stop at the first failure.

**Step 1 — Did the crawler actually see the page?**
Look at `rendered text` for the step named in the finding. Under ~200
characters and it is flagged `SUSPICIOUSLY EMPTY`. A finding about a page the
crawler could not read is worthless.

**Step 2 — Where did the price come from?**
`price_source: not_found` means every price-based detector was inert.
`rendered_text` means it was read off the page — check the number is the one
you would have read.

**Step 3 — Do the line items look like a shopping basket?**
This is where the worst false positive came from. If the item names are
*products on the shelf* — "Plimsolls", "Dash Force", "Winter Top" — the tool
is looking at a **catalogue**, not your order, and any total built from them
is meaningless. A real breakdown reads like *Item, Delivery, Tax, Total*.

**Step 4 — Open the screenshot.**
Every step is screenshotted. Look at the actual page. This catches more than
every automated check combined.

**Step 5 — Reproduce it by hand.**
Can *you* see the thing the tool says? Is the checkbox really pre-ticked when
the page first loads? Does the total really go up between the product page and
the checkout? If you cannot see it, it is a false positive — tell me, and it
becomes a test.

**Step 6 — Check the tier against the claim.**

| Tier | What it should mean | How to check it |
|---|---|---|
| **PROVABLE** (1.0) | Arithmetic or DOM state — the page either did this or it did not | Should take 30 seconds to confirm by eye. If you cannot, it is wrong. |
| **CORROBORATED** (0.9) | Two independent signals agreed | Check both, not one. |
| **INDICATIVE** (0.6) | Matches the pattern; a human must decide | **Never quote as a finding.** It is a prompt to go look. |

---

## 5. The four false positives, so you know the shapes

These are real, from real runs. If you see something like one of these,
suspect the tool first.

1. **A shipping offer read as a price.** "Free shipping on orders over $75"
   became the page's price, and then the baseline that a "total" was measured
   against. *Symptom: a price that is a round promotional number.*

2. **A shop's shelf summed as one order.** Nine products on a home page added
   to $324.98 and reported as the cart total — with an empty cart. *Symptom:
   line items that are product names.*

3. **An advert called a forced action.** A full-screen overlay reported as
   "no way out" when it had a close button the crawler could not see. *Symptom:
   a DP-04 finding on a page you can obviously click past.*

4. **A newsletter box called a subscription trap.** Every shop has one in its
   footer. *Symptom: DP-05 on a site that sells no subscription.*

---

## 6. What you can honestly claim

**You can say:** "Here is a tool that detects all thirteen CCPA dark-pattern
categories with stated evidence tiers. Here it is running on storefronts we
are permitted to audit. Here is its false-positive rate against a corpus of
seven compliant shops: zero."

**You can say:** "Auditing my own recorded checkout on <site>, the tool found
X, and here is the screenshot showing it."

**Do not say:** "Amazon uses dark patterns" on the strength of one automated
run. Even if it is true — and for many large sites, documented research says
some of it is — you have not proven it, and a judge who checks will find the
gap.

The strongest demonstration is not the biggest name. It is a finding you can
show on screen, explain in one sentence, and have the room verify with their
own eyes.

---

## 7. When you find a false positive

Send me the harness output and the screenshot. Every false positive becomes a
new shop in `tests/test_compliant_corpus.py`, which exists for exactly this
purpose: it is the file that stops this tool accusing honest sellers, and it
is only as good as the real-world mistakes fed into it.
