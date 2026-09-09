"""
Generic page-state extractor.

Design principle: most of what we need to detect dark patterns (price,
cart line items, checkboxes, button prominence, visible text) can be read
WITHOUT any site-specific code, using two fallback strategies in order:

  1. schema.org structured data (JSON-LD / microdata) — an enormous share
     of real e-commerce sites emit Product/Offer markup for Google Shopping
     SEO purposes. Reading this is free, robust, and doesn't break when a
     site redesigns its CSS.
  2. Common attribute/class conventions (data-price, .price, .line-item,
     .subtotal, .total) — a reasonable fallback that a new SiteAdapter can
     override per-site if a real target uses different markup.

This is what makes the "any e-commerce or flight site" requirement honest:
new sites need a SMALL selector-override file (see adapters/base.py), not
a rewrite of the extraction logic.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class CheckboxState:
    id: str
    label_text: str
    checked: bool
    is_prechecked: bool  # checked on initial page load, before any user action
    # "checkbox" or "radio". Radios were added for DP-12 (SaaS Billing): a
    # pre-selected paid PLAN is almost always a radio, not a checkbox, so a
    # checkbox-only extractor was structurally blind to the most common form
    # of the pattern. Defaulted so every existing construction site keeps
    # working unchanged.
    input_type: str = "checkbox"
    # Whether the control carries the HTML `required` attribute. This is what
    # makes DP-04 (Forced Action) provable rather than a judgement call: the
    # page itself declares that the user cannot proceed without ticking it.
    required: bool = False


@dataclass
class ModalInfo:
    """An interruption: a dialog, popup, or overlay sitting on top of the page.

    Captured for DP-10 (Nagging) and DP-04 (Forced Action). `signature` is the
    stable identity used to recognise the SAME interruption reappearing at a
    later funnel step — a modal with no dismiss control that reappears after
    being dismissed is a different, much stronger finding than one shown once.
    """
    signature: str                  # stable identity across page loads
    text: str
    is_blocking: bool               # fixed/sticky overlay that covers content
    viewport_coverage: float        # 0.0-1.0 share of the viewport it covers
    dismiss_controls: List[str] = field(default_factory=list)  # texts of X/close/no-thanks controls
    bbox: Optional[dict] = None


@dataclass
class LinkInfo:
    """An anchor with its destination. Needed for DP-05 (finding — or failing to
    find — a cancellation route) and DP-13 (executable downloads)."""
    text: str
    href: str
    font_size_px: float = 14.0
    contrast_ratio: float = 21.0


@dataclass
class FormFieldInfo:
    """A form control the page declares as required. DP-04 compares what the
    page demands against what the stated task actually needs."""
    name: str
    label_text: str
    field_type: str
    required: bool


@dataclass
class DisclosureLabel:
    """A text element containing an ad/sponsorship disclosure word
    ('Ad', 'Sponsored', 'Promoted'). Captured with the same geometry the
    button/checkbox extraction already uses, so Layer 2 can apply the
    identical WCAG-contrast-and-size math it already uses for decline
    buttons -- to a disclosure label instead."""
    text: str
    font_size_px: float
    contrast_ratio: float
    bbox: Optional[dict]


@dataclass
class ButtonInfo:
    text: str
    role: str            # "accept" | "decline" | "neutral" (from CSS class hint)
    bbox: Optional[dict]  # {x, y, width, height}
    font_size_px: float
    bg_color: str
    text_color: str
    contrast_ratio: float


@dataclass
class PageState:
    url: str
    step_name: str
    price: Optional[float]
    price_source: str          # "schema_org" | "data_attribute" | "rendered_text" | "not_found"
    line_items: List[dict]     # [{"name": str, "price": float}]
    checkboxes: List[CheckboxState]
    buttons: List[ButtonInfo]
    full_text: str
    urgency_phrases_found: List[str]
    raw_html: str
    disclosure_labels: List["DisclosureLabel"] = field(default_factory=list)
    modals: List["ModalInfo"] = field(default_factory=list)
    links: List["LinkInfo"] = field(default_factory=list)
    form_fields: List["FormFieldInfo"] = field(default_factory=list)


# Validated against Mathur et al. (2019) CSCW "Dark Patterns at Scale" real
# checkout-crawl data (1,818 real scraped instances from 11K shopping sites,
# https://github.com/aruneshmathur/dark-patterns). Testing against the real
# Low-stock/High-demand/Limited-time Message strings in that dataset drove
# this list from an initial 71.7% match rate to 94.1% -- see
# tests/test_real_world_validation.py, which re-runs this check on every CI
# run so the number can't silently regress.
URGENCY_PATTERNS = [
    r"only\s+\d+\s+(left|remaining|seats?|units?|at this price)",
    r"hurry", r"offer ends?", r"limited time",
    r"\d+\s+people (are\s+)?(viewing|booked|bought|seeing)",
    r"selling fast", r"sell(ing)? out (fast|quickly)", r"\bwill sell out\b",
    r"high[\s-]?demand", r"strong demand", r"in high demand",
    r"\d+\s+claimed", r"claimed!",
    r"expect \d.*(weeks|days).*arrive",
    r"low(\s+in)? stock", r"limited stock",
    r"reserved for \d+", r"sale ends? (once|when)",
    r"few (left|remaining)",
    # NOTE: a bare "in stock" is deliberately NOT here. It is the plain,
    # honest way to state availability -- "In stock: 42 units" -- and listing
    # it made DP-01 report truthful sellers for fabricated scarcity. Genuine
    # scarcity framing is already covered by "low stock", "limited stock",
    # "only N left" and the number-near-depletion-word catch-all below.
    # generalized number-near-scarcity-word catch-all, added after real-data
    # gap analysis surfaced phrasings like "20 units left", "96 item(s) left"
    r"\d+[^.\n]{0,20}(left|in stock|remaining)",
]


# Every contrast measurement must resolve the background the user actually
# SEES, which means walking up to the first ancestor that paints one.
# getComputedStyle(el).backgroundColor returns "rgba(0, 0, 0, 0)" for any
# element that does not set its own background -- which is most of them -- and
# reading that literally scores a transparent element as pure BLACK. A plain
# `<button style="color:#333">` on a white page then measured 1.66:1 instead of
# its real 12.6:1, so DP-06 and DP-09 reported perfectly legible controls as
# unreadable. The link extractor always did this correctly; the button and
# disclosure-label extractors did not, and now they share this one definition.
_BG_WALK_JS = (
    "el => { let n = el; while (n) { const c = getComputedStyle(n).backgroundColor;"
    " if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; n = n.parentElement; }"
    " return 'rgb(255, 255, 255)'; }"
)


# ---------------------------------------------------------------------------
# Reading money off a page that was never built to be read by a machine
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. Until this was added, prices came only from schema.org
# metadata or a `data-price` attribute. The bundled fixtures have those, so
# every test passed -- and REAL SITES HAVE NEITHER. Stripping the data-*
# attributes from a page modelled on a live Indian travel site and re-running
# the crawler returned `price=None, line_items=0`: DP-08, the flagship
# provable detector, could not fire on any real website at all. The tool
# worked on its own demo and nowhere else, and nothing in 248 tests said so.
#
# So this reads what a person reads. Indian sites write money in a lot of
# ways -- "₹ 6,530", "₹6,530", "Rs. 5,065", "INR 1,465", "6,530/-" -- and the
# thousands grouping is sometimes Western (1,234,567) and sometimes Indian
# (12,34,567). Both parse here.

_CURRENCY = r"(?:₹|Rs\.?|INR)"
# Either grouped (6,530 / 12,34,567 / 1,234,567) or a plain run of digits
# (5065). The grouped alternative comes first so it wins where both could
# match, otherwise "6,530" would parse as 6.
_AMOUNT = r"(?:\d{1,3}(?:[,\u00a0\s]\d{2,3})+|\d+)(?:\.\d{1,2})?"
_MONEY_RE = re.compile(rf"{_CURRENCY}\s*({_AMOUNT})|({_AMOUNT})\s*/-", re.IGNORECASE)

# "67% off", "20 % discount", "18%". Removed from a row BEFORE the money
# pattern runs, because adjacent inline spans render with no separator:
# "<s>₹899</s><span>67% off</span>" is the single string "₹89967% off", and
# stripping money first eats "₹89967" and leaves the label "% off".
_PERCENT_BADGE_RE = re.compile(r"\d[\d,.]*\s*%\s*(?:off|discount)?", re.IGNORECASE)

# Words that mark the figure a shopper will actually be charged.
_TOTAL_WORDS = re.compile(
    r"\b(total|subtotal|sub-total|total amount|amount payable|grand total|"
    r"order total|you pay|payable now|net payable|amount to pay)\b", re.IGNORECASE)


def parse_money(text: str):
    """The first monetary amount in this text, or None.

    Handles ₹ / Rs. / INR prefixes, the trailing "/-" Indian sites use, and
    both Western and Indian digit grouping.
    """
    if not text:
        return None
    match = _MONEY_RE.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    cleaned = re.sub(r"[,\u00a0\s]", "", raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _money_from_lines(lines, prefer_total: bool):
    """Scan rendered lines for a price.

    prefer_total: on a checkout page the number that matters is the one next
    to the word "total", not the first rupee sign on the page -- which is
    usually the base fare or the first line item.
    """
    if prefer_total:
        for line in lines:
            if _TOTAL_WORDS.search(line):
                amount = parse_money(line)
                if amount:
                    return amount
    for line in lines:
        amount = parse_money(line)
        if amount:
            return amount
    return None


# A fee row on a real site is almost never one text node. It is a flex
# container with the label in one span and the amount in another:
#
#     <div class="fare-line"><span>Airline Taxes</span><span>₹ 1,465</span></div>
#
# inner_text on the BODY renders that as two separate lines, so a line-by-line
# scan sees a label with no amount and an amount with no label, and finds no
# fee breakdown at all on the pages that have the clearest one. This walks the
# DOM instead and returns the INNERMOST element that contains a money figure --
# which is that row, label and amount together.
_ROW_TEXT_JS = """
() => {
  const money = /(?:₹|Rs\\.?|INR)\\s*[\\d,]|\\d[\\d,\\s]*\\/-/i;
  // A discount badge is removed FIRST, before the money strip, and this
  // order is the whole point. Adjacent inline spans render with no
  // separator, so "<s>₹899</s><span>67% off</span>" reads as "₹89967% off",
  // and the money pattern then swallows "₹89967" -- leaving the label
  // "% off". That is not a fee; it is the leftovers of a price display, and
  // it was being reported as a line item on every product page with a sale
  // badge, which is most of them. Removing the badge first leaves "₹299₹",
  // which carries no word, so the element stops qualifying and the row is
  // read from its parent -- where the product's actual NAME lives.
  const stripPercent = (t) => t.replace(/\\d[\\d,.]*\\s*%\\s*(off|discount)?/gi, ' ');
  const stripMoney = (t) =>
    stripPercent(t)
     .replace(/(?:₹|Rs\\.?|INR)\\s*[\\d][\\d,\\s]*(?:\\.\\d{1,2})?/gi, ' ')
     .replace(/[\\d,]+\\s*\\/-/g, ' ')
     .replace(/\\s+/g, ' ').trim();

  // A FEE ROW carries both halves: a label AND an amount. A bare <span>₹ 129</span>
  // is only the amount, and its parent is the row -- so "innermost element that
  // contains money" picked the wrong node every time and found no breakdown on
  // exactly the pages that have the clearest one.
  const qualifies = (el) => {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 220) return false;
    if (!money.test(t)) return false;
    const label = stripMoney(t);
    // The label has to contain an actual WORD. "- ₹ 130" strips to "-", which
    // is not a label -- it is the amount half of a discount row, and treating
    // it as a row let it outrank its parent, which is where "Discounts" lives.
    return /[a-z]/i.test(label) && label.length <= 80;
  };

  const rows = [];
  const all = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of all) {
    if (!qualifies(el)) continue;
    // Innermost QUALIFYING element: if a descendant is also a label+amount
    // row, this element is a container listing several rows.
    let deeper = false;
    for (const d of el.querySelectorAll('*')) {
      if (qualifies(d)) { deeper = true; break; }
    }
    if (deeper) continue;
    rows.push((el.innerText || '').replace(/\\s+/g, ' ').trim());
  }
  return rows;
}
"""


def _dom_money_rows(page):
    try:
        rows = page.evaluate(_ROW_TEXT_JS)
    except Exception:
        return []
    seen, unique = set(), []
    for row in rows:
        if row not in seen:
            seen.add(row)
            unique.append(row)
    return unique


def _line_items_from_text(lines):
    """Label/amount pairs from rendered rows, for pages with no data-* markup.

    A row like "Airline Taxes and Surcharges    ₹ 1,465" carries both halves,
    which is exactly what a fee breakdown looks like to a reader. Rows whose
    label is the total itself are skipped -- the total is not one of its own
    components, and counting it would double the sum DP-08 compares against.
    """
    items = []
    for line in lines:
        amount = parse_money(line)
        if amount is None:
            continue
        # Same order as the browser-side rule, and for the same reason: a
        # "67% off" badge butted against a struck-through price leaves the
        # label "% off" if money is stripped first. See _ROW_TEXT_JS.
        label = _PERCENT_BADGE_RE.sub(" ", line)
        label = _MONEY_RE.sub("", label)
        label = re.sub(r"\(\s*\)", " ", label)          # "GST ( )" -> "GST"
        label = re.sub(r"[-–—:]+\s*$", "", label).strip(" \u00a0\t-–—:")
        label = re.sub(r"\s+", " ", label).strip()
        # A label with no letter in it is not a fee row -- it is punctuation
        # left over from a price display. Reporting it would put "%" or "-"
        # into a public finding as the name of an undisclosed charge.
        if not label or len(label) > 60 or not re.search(r"[A-Za-z]", label):
            continue
        if _TOTAL_WORDS.search(label):
            continue
        # A negative figure is a discount, and it belongs in the sum: a fee
        # breakdown that nets a discount against a charge must still add up.
        if re.search(r"^-|\(-|\bless\b|\bdiscount", line, re.IGNORECASE):
            amount = -abs(amount)
        items.append({"name": label, "price": amount})
    return items


def _relative_luminance(hex_or_rgb: str) -> float:
    """WCAG 2.1 relative luminance from an rgb()/hex color string."""
    value = (hex_or_rgb or "").strip()

    # A fully transparent colour carries no luminance of its own. Returning a
    # number for it is what let "rgba(0, 0, 0, 0)" be scored as black.
    alpha = re.match(r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([\d.]+)\s*\)", value)
    if alpha and float(alpha.group(1)) == 0:
        return 0.5

    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", value)
    if m:
        r, g, b = [int(x) / 255 for x in m.groups()]
    else:
        # The docstring promised hex support and the code never had it, so
        # contrast_ratio("#ffffff", "#000000") returned 1.0 -- the maximum
        # contrast pair reported as the minimum.
        h = value.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", h):
            r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        else:
            return 0.5  # genuinely unknown colour, neutral fallback

    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = channel(r), channel(g), channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def _extract_schema_org_price(html: str) -> Optional[float]:
    matches = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    for raw in matches:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        offers = data.get("offers") if isinstance(data, dict) else None
        if isinstance(offers, dict) and "price" in offers:
            try:
                return float(offers["price"])
            except (TypeError, ValueError):
                continue
    return None


def extract_page_state(page, step_name: str, price_selector_override: str = None) -> PageState:
    """
    page: a Playwright Page object, already navigated to the target URL.
    """
    html = page.content()
    full_text = page.inner_text("body")

    # --- price: schema.org, then data-price, then the RENDERED TEXT --------
    #
    # The text fallback is what makes this work on a site nobody prepared for
    # us. The first two sources are better when present -- they are the site
    # stating its own price unambiguously -- so they are still tried first,
    # and price_source records which one answered, so a reader of the evidence
    # can weigh a parsed figure differently from a declared one.
    price = _extract_schema_org_price(html)
    price_source = "schema_org" if price is not None else "not_found"
    if price is None:
        override = price_selector_override or "[data-price]"
        el = page.query_selector(override)
        if el:
            raw = el.get_attribute("data-price")
            if raw:
                try:
                    price = float(raw)
                    price_source = "data_attribute"
                except ValueError:
                    pass

    # Rows first (label and amount together), then whole lines as a backstop.
    money_rows = _dom_money_rows(page)
    text_lines = money_rows + [ln.strip() for ln in (full_text or "").splitlines() if ln.strip()]

    if price is None:
        # On a page that shows a total, that is the number a shopper pays --
        # not the first rupee sign, which is usually the base fare.
        looks_like_checkout = bool(_TOTAL_WORDS.search(full_text or ""))
        price = _money_from_lines(text_lines, prefer_total=looks_like_checkout)
        if price is not None:
            price_source = "rendered_text"

    # --- line items (cart/checkout breakdown) -----------------------------
    line_items = []
    for el in page.query_selector_all("[data-name][data-price]"):
        name = el.get_attribute("data-name")
        raw_price = el.get_attribute("data-price")
        try:
            line_items.append({"name": name, "price": float(raw_price)})
        except (TypeError, ValueError):
            continue

    if not line_items:
        line_items = _line_items_from_text(money_rows)

    # --- checkboxes AND radios: selected state on FIRST load = pre-ticked ---
    # Radios are included because a pre-selected paid PLAN (DP-12 SaaS Billing)
    # is nearly always a radio group, never a checkbox. Extracting only
    # checkboxes made the most common real form of that pattern invisible.
    checkboxes = []
    for el in page.query_selector_all("input[type=checkbox], input[type=radio]"):
        cb_id = el.get_attribute("id") or ""
        input_type = (el.get_attribute("type") or "checkbox").lower()
        checked = el.is_checked()
        required = el.get_attribute("required") is not None or \
            (el.get_attribute("aria-required") or "").lower() == "true"
        # best-effort label text: an explicit <label for=...> wins, because a
        # parent's innerText on a densely nested form can swallow the whole
        # fieldset and make every control look identical.
        label_text = ""
        if cb_id:
            try:
                lbl = page.query_selector(f'label[for="{cb_id}"]')
                if lbl:
                    label_text = lbl.inner_text() or ""
            except Exception:
                label_text = ""
        if not label_text:
            try:
                label_text = el.evaluate("el => el.parentElement.innerText") or ""
            except Exception:
                label_text = ""
        checkboxes.append(CheckboxState(
            id=cb_id, label_text=label_text.strip(),
            checked=checked, is_prechecked=checked,
            input_type=input_type, required=required,
        ))

    # --- buttons: geometry + contrast (Layer 2 raw material) ---
    buttons = []
    for el in page.query_selector_all("button, a.btn-accept, a.btn-decline"):
        text = (el.inner_text() or "").strip()
        if not text:
            continue
        cls = el.get_attribute("class") or ""
        role = "accept" if "accept" in cls else "decline" if "decline" in cls else "neutral"
        try:
            box = el.bounding_box()
        except Exception:
            box = None
        try:
            bg = el.evaluate(_BG_WALK_JS)
            fg = el.evaluate("el => getComputedStyle(el).color")
            font_size = el.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
        except Exception:
            bg, fg, font_size = "rgb(255,255,255)", "rgb(0,0,0)", 14.0
        buttons.append(ButtonInfo(
            text=text, role=role, bbox=box, font_size_px=font_size,
            bg_color=bg, text_color=fg, contrast_ratio=contrast_ratio(fg, bg),
        ))

    urgency_found = []
    # Smart/curly quotes (' vs ') are a real, common source of missed matches
    # -- surfaced by testing against real Mathur et al. data, where a
    # meaningful share of misses were purely a curly-apostrophe issue.
    normalized_for_matching = full_text.replace("\u2019", "'").replace("\u2018", "'")
    for pattern in URGENCY_PATTERNS:
        if re.search(pattern, normalized_for_matching, re.IGNORECASE):
            urgency_found.append(pattern)

    # --- disclosure labels: geometry + contrast (Layer 2, DP-09 Disguised Ad) ---
    # Whole-word match only ("ad", not "advance"/"leadership") -- a naive
    # substring search would false-positive constantly on ordinary content.
    disclosure_labels = []
    disclosure_pattern = re.compile(r"\b(ad|advertisement|sponsored|promoted)\b", re.IGNORECASE)
    for el in page.query_selector_all("span, small, div, p"):
        try:
            text = (el.inner_text() or "").strip()
        except Exception:
            continue
        # Keep this to short standalone labels ("Ad", "Sponsored") -- not
        # paragraphs that happen to contain the word "ad" in running prose.
        if not text or len(text) > 20 or not disclosure_pattern.search(text):
            continue
        try:
            box = el.bounding_box()
            bg = el.evaluate(_BG_WALK_JS)
            fg = el.evaluate("el => getComputedStyle(el).color")
            font_size = el.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
        except Exception:
            continue
        disclosure_labels.append(DisclosureLabel(
            text=text, font_size_px=font_size, contrast_ratio=contrast_ratio(fg, bg), bbox=box,
        ))

    modals = _extract_modals(page)
    links = _extract_links(page)
    form_fields = _extract_required_fields(page)

    return PageState(
        url=page.url, step_name=step_name, price=price, price_source=price_source,
        line_items=line_items, checkboxes=checkboxes, buttons=buttons,
        full_text=full_text, urgency_phrases_found=urgency_found, raw_html=html,
        disclosure_labels=disclosure_labels,
        modals=modals, links=links, form_fields=form_fields,
    )


# --- Interruption / modal extraction -------------------------------------
#
# Deliberately convention-based, in the same spirit as the price extractor:
# ARIA roles first (semantically correct and increasingly common), then the
# near-universal .modal/.popup/.overlay class conventions. A site using none
# of these needs a selector override in its adapter, exactly like a site
# using a non-standard price attribute.

MODAL_SELECTORS = (
    "[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open], "
    ".modal, .popup, .overlay, .interstitial, .lightbox, .newsletter-popup"
)

# Text on a control that offers a way OUT of an interruption. Order matters
# only for readability; matching is any-of.
DISMISS_PATTERNS = [
    r"^\s*[x✕✖×]\s*$", r"\bclose\b", r"\bdismiss\b", r"\bnot now\b",
    r"\bno thanks?\b", r"\bmaybe later\b", r"\blater\b", r"\bskip\b",
    r"\bcancel\b", r"\bcontinue without\b", r"\bdecline\b", r"\bno,? ",
]


def _looks_like_dismiss(text: str) -> bool:
    t = (text or "").strip().replace("’", "'")
    if not t or len(t) > 40:
        return False
    return any(re.search(p, t, re.IGNORECASE) for p in DISMISS_PATTERNS)


def modal_signature(text: str, element_id: str = "", element_class: str = "") -> str:
    """Stable identity for an interruption across page loads.

    Prefers the element's own id, then its class list, and falls back to a
    normalised prefix of its text. Text alone is a poor identity — a modal
    that shows a rotating discount ("Get 10% off" / "Get 15% off") would
    otherwise read as a *different* modal each time and DP-10 would never
    fire. Whitespace and digits are normalised out of the text fallback for
    the same reason.
    """
    if element_id:
        return f"id:{element_id}"
    if element_class:
        cls = " ".join(sorted(c for c in element_class.split() if c))
        if cls:
            return f"class:{cls}"
    normalised = re.sub(r"\d+", "#", re.sub(r"\s+", " ", (text or "").strip().lower()))
    return f"text:{normalised[:80]}"


def _extract_modals(page) -> List["ModalInfo"]:
    modals = []
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 900}
    except Exception:
        viewport = {"width": 1280, "height": 900}
    viewport_area = max(viewport["width"] * viewport["height"], 1)

    for el in page.query_selector_all(MODAL_SELECTORS):
        try:
            if not el.is_visible():
                continue
            text = (el.inner_text() or "").strip()
        except Exception:
            continue
        if not text:
            continue
        try:
            box = el.bounding_box()
            position = el.evaluate("el => getComputedStyle(el).position")
            z_index = el.evaluate("el => getComputedStyle(el).zIndex")
            el_id = el.get_attribute("id") or ""
            el_class = el.get_attribute("class") or ""
        except Exception:
            continue

        coverage = 0.0
        if box:
            coverage = min((box["width"] * box["height"]) / viewport_area, 1.0)

        # "Blocking" means it sits above the page rather than flowing with it.
        # A fixed/sticky position or a real stacking context is the mechanical
        # signal; coverage alone would misread a large in-flow banner.
        try:
            z = int(z_index)
        except (TypeError, ValueError):
            z = 0
        is_blocking = position in ("fixed", "sticky") or (position == "absolute" and z > 0)

        dismiss = []
        try:
            for ctl in el.query_selector_all("button, a, [role=button], .close, .dismiss"):
                ctl_text = (ctl.inner_text() or "").strip()
                if not ctl_text:
                    # An icon-only close button has no text. Its aria-label is
                    # the accessible name and counts as a dismissal control.
                    ctl_text = (ctl.get_attribute("aria-label") or "").strip()
                if _looks_like_dismiss(ctl_text):
                    dismiss.append(ctl_text)
        except Exception:
            pass

        modals.append(ModalInfo(
            signature=modal_signature(text, el_id, el_class),
            text=text[:500], is_blocking=is_blocking,
            viewport_coverage=round(coverage, 3),
            dismiss_controls=dismiss, bbox=box,
        ))
    return modals


def _extract_links(page) -> List["LinkInfo"]:
    links = []
    for el in page.query_selector_all("a[href]"):
        try:
            text = (el.inner_text() or "").strip()
            href = el.get_attribute("href") or ""
        except Exception:
            continue
        if not href:
            continue
        try:
            font_size = el.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
            fg = el.evaluate("el => getComputedStyle(el).color")
            bg = el.evaluate(_BG_WALK_JS)
        except Exception:
            font_size, fg, bg = 14.0, "rgb(0,0,0)", "rgb(255,255,255)"
        links.append(LinkInfo(
            text=text[:200], href=href,
            font_size_px=font_size or 14.0,
            contrast_ratio=contrast_ratio(fg, bg),
        ))
    return links


def _extract_required_fields(page) -> List["FormFieldInfo"]:
    fields = []
    for el in page.query_selector_all("input, select, textarea"):
        try:
            field_type = (el.get_attribute("type") or el.evaluate("el => el.tagName.toLowerCase()") or "").lower()
            if field_type in ("hidden", "submit", "button", "image", "reset"):
                continue
            # Checkboxes and radios are already captured in `checkboxes`, with
            # richer information (selected-on-load state, which form_fields has
            # no notion of). Including them here too made every required
            # consent checkbox appear in BOTH collections, so DP-04 reported
            # the same control twice and doubled its own finding count -- the
            # identical mistake the DP-02/DP-12 split exists to prevent.
            if field_type in ("checkbox", "radio"):
                continue
            required = el.get_attribute("required") is not None or \
                (el.get_attribute("aria-required") or "").lower() == "true"
            if not required:
                continue
            name = el.get_attribute("name") or el.get_attribute("id") or ""
            # label[for=...] references the element's ID, never its NAME. This
            # looked up the name first, so on the very common
            # <input id="email-input" name="email"> the lookup missed and fell
            # through to the parent's innerText -- which swallows sibling
            # labels. A required email field next to a marketing checkbox then
            # produced label_text "Email address\nSend me marketing offers",
            # and DP-04 accused the email field of bundling marketing consent.
            field_id = el.get_attribute("id") or ""
            label_text = ""
            if field_id:
                try:
                    lbl = page.query_selector(f'label[for="{field_id}"]')
                    if lbl:
                        label_text = (lbl.inner_text() or "").strip()
                except Exception:
                    pass
            if not label_text:
                try:
                    label_text = (el.evaluate("el => el.parentElement.innerText") or "").strip()
                except Exception:
                    label_text = ""
        except Exception:
            continue
        fields.append(FormFieldInfo(
            name=name, label_text=label_text[:200],
            field_type=field_type, required=True,
        ))
    return fields
