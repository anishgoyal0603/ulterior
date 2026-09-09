"""
Can this tool read a page that was never built for it?

THE GAP THIS FILE CLOSES

Until these tests existed, every price came from schema.org metadata or a
`data-price` attribute. The bundled fixtures carry both, so 248 tests passed --
and no real website has either. Stripping the data-* attributes from a page
modelled on a live Indian travel site and re-running the crawler returned:

    price=None (not_found)   line_items=0

DP-08 -- drip pricing, the flagship PROVABLE detector and the strongest claim
this project makes -- could not fire on any real site at all. The tool worked
on its own demo and nowhere else, and nothing in the suite said so, because
nothing in the suite had ever shown it a page it did not help build.

The fixtures here are modelled on screenshots of a real Indian OTA's flight
search and fare summary, and they carry NO machine-readable price data. What
the detectors get is what a person gets: rendered text.

WHAT REAL PAGES DO THAT DEMO PAGES DO NOT

  * money as text in many shapes -- "₹ 6,530", "₹6,530", "Rs. 5,065",
    "INR 1,465", "6,530/-" -- with Western (1,234,567) or Indian (12,34,567)
    grouping
  * a fee row split across flex children: <span>Taxes</span><span>₹ 1,465</span>,
    which body-level inner_text renders as two separate lines, so a
    line-by-line scan sees a label with no amount and an amount with no label
  * the first rupee figure on a checkout page being the BASE FARE, not the
    total the shopper actually pays
  * discounts written as "- ₹ 130", which must count as negative or a fee
    breakdown will not add up to its own total
  * clocks everywhere that are not countdowns -- departure times, durations
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.adapters.base import SiteAdapter, FunnelStep
from capture.funnel_walker import walk_funnel
from capture.state_extractor import parse_money, URGENCY_PATTERNS
from detectors.layer1_rules import _extract_urgent_value, _urgency_value_kind
from detectors.pipeline import audit

OTA = Path(__file__).parent.parent / "fixtures" / "real_world_ota"


# --- Money, written the way Indian sites write it -------------------------

@pytest.mark.parametrize("text,expected", [
    ("₹ 6,530", 6530.0),          # a space after the symbol, as MakeMyTrip writes it
    ("₹6,530", 6530.0),
    ("Rs. 5,065", 5065.0),
    ("Rs 5065", 5065.0),
    ("INR 1,465", 1465.0),
    ("6,530/-", 6530.0),          # the trailing "/-" convention
    ("₹ 12,34,567", 1234567.0),   # Indian lakh grouping
    ("₹ 1,234,567", 1234567.0),   # Western grouping
    ("₹ 1,299.50", 1299.5),
    ("Total Amount ₹ 6,529", 6529.0),
])
def test_money_is_read_the_way_a_person_reads_it(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", [
    "Non stop", "02h 25m", "93% on time", "Terminal T2", "no money here", "",
])
def test_things_that_are_not_money_are_not_read_as_money(text):
    """A duration and an on-time percentage are numbers on every flight page.
    Reading either as a price would poison the arithmetic DP-08 depends on."""
    assert parse_money(text) is None


# --- A real page, with nothing machine-readable in it ---------------------

@pytest.fixture(scope="module")
def ota_trace(tmp_path_factory):
    adapter = SiteAdapter(
        site_name="OTA modelled on real screenshots",
        funnel_steps=[
            FunnelStep(name="results", url="file://" + str((OTA / "ota_results.html").resolve())),
            FunnelStep(name="fare_summary", url="file://" + str((OTA / "ota_fare_summary.html").resolve())),
        ],
    )
    return walk_funnel(adapter, screenshot_dir=str(tmp_path_factory.mktemp("ota")))


def test_the_price_is_found_without_any_data_attribute(ota_trace):
    """This is the whole point. No schema.org, no data-price -- just text."""
    for state in ota_trace.states:
        assert state.price is not None, (
            f"no price found on '{state.step_name}' -- the tool cannot audit a "
            f"real site if it cannot read what the site charges"
        )
        assert state.price_source == "rendered_text"


def test_the_total_beats_the_base_fare_on_a_checkout_page(ota_trace):
    """A fare summary lists the base fare FIRST and the total LAST. Taking the
    first rupee figure on the page reports the base fare as the price, which
    understates what the shopper pays and breaks the drip-pricing comparison
    in the direction that hides a real violation."""
    summary = [s for s in ota_trace.states if s.step_name == "fare_summary"][0]
    assert summary.price == 6529.0, f"read {summary.price}, expected the total"


def test_the_whole_fee_breakdown_is_recovered_from_a_flex_row(ota_trace):
    """Each row is <span>label</span><span>amount</span>. Body-level text
    renders those as separate lines, which is why this is read from the DOM."""
    summary = [s for s in ota_trace.states if s.step_name == "fare_summary"][0]
    names = {item["name"].lower() for item in summary.line_items}
    for expected in ("taxes", "other services", "discounts"):
        assert any(expected in n for n in names), f"{expected!r} missing from {names}"


def test_a_discount_counts_as_negative_so_the_breakdown_adds_up(ota_trace):
    """"- ₹ 130" is money coming off. Counted as positive, a fee breakdown
    overshoots its own total and DP-08 reports a hidden charge that is really
    a discount -- accusing a shop for giving money back."""
    summary = [s for s in ota_trace.states if s.step_name == "fare_summary"][0]
    discounts = [i for i in summary.line_items if "discount" in i["name"].lower()]
    assert discounts, "no discount row found"
    assert discounts[0]["price"] < 0
    assert abs(sum(i["price"] for i in summary.line_items) - summary.price) < 1.0, (
        "the recovered breakdown does not add up to the total on the page"
    )


def test_this_real_world_page_is_reported_clean(ota_trace):
    """Nothing on the modelled pages is a CCPA-13 dark pattern: the fare is
    itemised, the total matches the listed price, the add-ons are unticked and
    the sponsored placement is labelled. The tool must say so."""
    violations = audit(ota_trace)
    assert violations == [], "\n".join(
        f"{v.pattern_code} [{v.evidence.get('evidence_tier')}] {v.explanation}"
        for v in violations
    )


# --- Clocks that are not countdowns ---------------------------------------

def test_a_departure_time_next_to_a_seats_left_message_is_not_a_countdown():
    """Real flight page: "Hurry! Only 3 seats left  07:15 DEL  09:30 BOM".

    Looking ahead for a clock after ANY urgency phrase turned 07:15 -- the
    departure time -- into a countdown. A departure time is identical on every
    reload, so every airline seat page carrying a seats-left message would be
    reported at CORROBORATED 0.9 for "a countdown that does not count down".
    A clock is only a countdown when the words beside it claim a deadline.
    """
    text = "Hurry! Only 3 seats left  07:15 DEL 02h 15m 09:30 BOM  Rs 6,530"
    patterns = [p for p in URGENCY_PATTERNS if re.search(p, text, re.I)]
    assert _urgency_value_kind(text, patterns) == "count"
    values = {_extract_urgent_value(text, p) for p in patterns}
    assert "07:15" not in values


def test_a_real_countdown_is_still_recognised():
    text = "Offer ends in: 00:04:59"
    patterns = [p for p in URGENCY_PATTERNS if re.search(p, text, re.I)]
    assert _urgency_value_kind(text, patterns) == "countdown"


def test_price_digits_do_not_leak_into_a_scarcity_count():
    """"Rs 7,475 Only 2 seats left" was yielding the value "2 7" -- the 7 being
    the first digit of the fare. That is meaningless as evidence, and it can
    differ between loads purely because the price moved."""
    text = "Rs 7,475  Only 2 seats left at this price"
    patterns = [p for p in URGENCY_PATTERNS if re.search(p, text, re.I)]
    values = {v for v in (_extract_urgent_value(text, p) for p in patterns) if v}
    assert values == {"2"}, values


# --- The sale badge, which is on nearly every product page alive ----------

SALE_BADGE_PAGE = """<!doctype html><meta charset="utf-8"><title>Sale</title>
<body>
  <div class="row"><span class="name">Cotton Kurta</span>
    <span class="price">&#8377;1,999<s>&#8377;2,999</s><span class="save">33% off</span></span>
  </div>
  <div class="row"><span>Delivery</span><span>&#8377;49</span></div>
  <div class="row"><span>Total</span><span>&#8377;2,048</span></div>
</body>"""


def _states_for(html: str, tmp_path):
    page = tmp_path / "sale.html"
    page.write_text(html, encoding="utf-8")
    adapter = SiteAdapter(
        site_name="sale badge",
        funnel_steps=[FunnelStep(name="checkout", url="file://" + str(page.resolve()))],
    )
    return walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots")).states


def test_a_discount_badge_does_not_become_a_line_item_called_percent_off(tmp_path):
    """Adjacent inline spans render with NO separator between them, so

        <s>Rs 899</s><span>67% off</span>

    is the single string "Rs 89967% off". Stripping money first consumed
    "Rs 89967" and left the label "% off", which was then reported as a
    line item -- and, on a checkout page, as an UNDISCLOSED CHARGE named
    "% off" in a PROVABLE-tier finding. A sale badge is on most product
    pages in existence, so this was not an edge case.
    """
    state = _states_for(SALE_BADGE_PAGE, tmp_path)[0]
    names = [item["name"] for item in state.line_items]
    for name in names:
        assert re.search(r"[A-Za-z]", name), (
            f"line item {name!r} has no word in it -- it is leftover punctuation "
            f"from a price display, and it would be published as the name of a "
            f"charge. All items: {names}"
        )
        assert "%" not in name, f"discount badge leaked into a line item name: {names}"


def test_the_sale_badge_page_still_reads_its_real_numbers(tmp_path):
    """The fix must not work by making the extractor blind. The delivery fee
    and the total are still there and must still be read."""
    state = _states_for(SALE_BADGE_PAGE, tmp_path)[0]
    assert state.price == 2048.0, f"read {state.price}, expected the total"
    names = " ".join(i["name"].lower() for i in state.line_items)
    assert "delivery" in names, f"the delivery fee row was lost: {state.line_items}"


def test_a_percentage_tax_row_keeps_its_label(tmp_path):
    """"GST (18%)" is a legitimate fee row whose label contains a percentage.
    Stripping percentages must not strip the row."""
    html = ("""<!doctype html><meta charset="utf-8"><body>"""
            """<div><span>GST (18%)</span><span>&#8377;129</span></div>"""
            """<div><span>Total</span><span>&#8377;129</span></div></body>""")
    state = _states_for(html, tmp_path)[0]
    names = " ".join(i["name"].lower() for i in state.line_items)
    assert "gst" in names, f"a percentage in the label lost the row: {state.line_items}"


# --- What the public automation sandboxes taught it -----------------------
#
# Every test below comes from one run against saucedemo, automationexercise,
# the OpenCart demos and the Saleor storefront -- the first pages this tool
# had ever seen that nobody prepared for it. Three of the four problems it
# exposed were invisible to 252 passing tests.

@pytest.mark.parametrize("text,expected", [
    ("$19.99", 19.99), ("US$ 1,299", 1299.0), ("€40", 40.0), ("£12.50", 12.5),
    ("¥1,200", 1200.0), ("19.99 USD", 19.99), ("500 INR", 500.0),
])
def test_money_outside_india_is_read_too(text, expected):
    """The currency list was rupees only. Pointed at storefronts priced in
    dollars, the extractor read NO price at all -- demo.opencart.com and
    demo.saleor.io both came back "no price could be read", and DP-08, the
    strongest claim this project makes, cannot run without one. A tool that
    silently declines to measure anything outside one currency does not
    "work when you paste a link"."""
    assert parse_money(text) == expected


USD_CHECKOUT = """<!doctype html><meta charset="utf-8"><body>
  <div><span>Cotton Tee</span><span>$19.99<s>$29.99</s><span>33% off</span></span></div>
  <div><span>Shipping</span><span>$4.50</span></div>
  <div><span>Sales Tax</span><span>$1.75</span></div>
  <div><span>Total</span><span>$26.24</span></div></body>"""


def test_a_dollar_checkout_is_read_end_to_end(tmp_path):
    state = _states_for(USD_CHECKOUT, tmp_path)[0]
    assert state.price == 26.24, f"read {state.price}"
    names = {i["name"] for i in state.line_items}
    assert not any("$" in n for n in names), f"a stray currency mark survived: {names}"
    assert abs(sum(i["price"] for i in state.line_items) - state.price) < 0.01


PRODUCT_CARDS = """<!doctype html><meta charset="utf-8"><body>
  <div class="card"><h3>Blue Top</h3><p>$5.00</p><a href="#">Add to cart</a></div>
  <div class="card"><h3>Men Tshirt</h3><p>$4.00</p><a href="#">Add to cart</a></div>
  <div class="row"><span>Quantity:</span><span>$5.00</span><a href="#">Add to cart</a></div>
  </body>"""


def test_a_buy_button_does_not_become_part_of_a_charge_name(tmp_path):
    """A product card is the innermost element holding both a name and a
    price, so the row scanner picks it -- correctly -- and the button's words
    ride along. A real listing page produced line items called "Blue Top Add
    to cart" and "Quantity: Add to cart". Printing either in a finding as the
    name of an undisclosed fee is indefensible."""
    state = _states_for(PRODUCT_CARDS, tmp_path)[0]
    names = [i["name"] for i in state.line_items]
    for name in names:
        assert "add to cart" not in name.lower(), f"button text in a charge name: {names}"
    assert not any(n.rstrip(":").strip().lower() == "quantity" for n in names), (
        f"'Quantity' is a form caption, not a charge: {names}"
    )


def test_a_catalogue_is_not_summed_as_one_order(tmp_path):
    """The landmine this run walked past by luck.

    The row scanner returned 36 line items on a real listing page, one per
    product on the shelf. Had that page been the last step of a funnel, DP-08
    would have compared the first price against the value of the ENTIRE
    CATALOGUE and reported the shop, at PROVABLE 1.0, for concealing charges
    that were simply other products for sale.
    """
    listing = tmp_path / "listing.html"
    rows = "".join(
        f'<div><span>Item {i}</span><span>$50.00</span></div>' for i in range(30)
    )
    listing.write_text(
        f'<!doctype html><meta charset="utf-8"><body><p>Price: $50.00</p>{rows}</body>',
        encoding="utf-8")
    entry = tmp_path / "entry.html"
    entry.write_text('<!doctype html><meta charset="utf-8"><body><p>$50.00</p></body>',
                     encoding="utf-8")

    adapter = SiteAdapter(
        site_name="a shop with a catalogue page",
        funnel_steps=[
            FunnelStep(name="entry", url="file://" + str(entry.resolve())),
            FunnelStep(name="listing", url="file://" + str(listing.resolve())),
        ],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-08" not in codes, (
        "the catalogue was summed as if it were one order's fee breakdown; "
        f"findings: {codes}"
    )


def test_an_overlay_we_cannot_see_into_is_not_called_inescapable(tmp_path):
    """DP-04's entire claim is "there was no way out". A crawler cannot read
    into another document, so for an ad or consent iframe it has not observed
    an absence -- it has observed its own blind spot.

    This fired at CORROBORATED 0.75 against an automation sandbox whose
    overlay did have a close control; one step later the crawler clicked its
    way past the very same overlay.
    """
    page = tmp_path / "ad.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><body><p>$10.00 Shop now</p>'
        '<div role="dialog" style="position:fixed;top:0;left:0;width:1280px;'
        'height:900px;z-index:99"><iframe srcdoc="&lt;p&gt;advert&lt;/p&gt;" '
        'width="1200" height="800"></iframe></div></body>',
        encoding="utf-8")
    adapter = SiteAdapter(
        site_name="shop running a full-page ad frame",
        funnel_steps=[FunnelStep(name="entry", url="file://" + str(page.resolve()))],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-04" not in codes, f"accused a site over an unreadable overlay: {codes}"


def test_a_newsletter_footer_is_not_a_subscription_with_no_way_out(tmp_path):
    """The second false positive from the same run. A shop with a newsletter
    box and the word "monthly" somewhere on the page was reported for offering
    a recurring commitment with no cancellation route. It sells no
    subscription at all -- and nearly every shop on the internet has that
    footer."""
    page = tmp_path / "shop.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><body>'
        '<p>Cotton Tee $19.99</p><p>Total $19.99</p>'
        '<p>New arrivals added monthly.</p>'
        '<footer><h4>Subscribe</h4><p>Get our newsletter for updates.</p>'
        '<button>Subscribe</button></footer></body>',
        encoding="utf-8")
    adapter = SiteAdapter(
        site_name="an ordinary shop with a newsletter box",
        funnel_steps=[FunnelStep(name="entry", url="file://" + str(page.resolve()))],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-05" not in codes, f"a newsletter was read as a subscription trap: {codes}"


# --- The worst finding this project has produced --------------------------
#
# Second sandbox run, a React storefront:
#
#   DP-08 [provable] conf=1.0
#   Price disclosed at 'entry' was $75.0, but the final total at 'cart' is
#   $324.98 -- $249.98 in charges were not shown upfront.
#
# Every number was furniture. The $75 was a "Free shipping on orders over $75"
# banner. The $324.98 was nine products on the HOME PAGE shelf, summed. The
# "cart" was the same grid again -- the crawler had clicked a control reading
# "0 items in cart". An empty basket, at the project's highest confidence
# tier, published against a named company.

SHELF = """<!doctype html><meta charset="utf-8"><body>
  <p>Free shipping on orders over $75</p>
  <div><span>Plimsolls</span><span>$55.00</span></div>
  <div><span>Dash Force</span><span>$90.00</span></div>
  <div><span>Dark Polygon Tee</span><span>$45.00</span></div>
  <div><span>Mighty Mug</span><span>$11.99</span></div>
  </body>"""


def _two_step_trace(html_first, html_last, tmp_path):
    first = tmp_path / "first.html"
    last = tmp_path / "last.html"
    first.write_text(html_first, encoding="utf-8")
    last.write_text(html_last, encoding="utf-8")
    adapter = SiteAdapter(
        site_name="storefront",
        funnel_steps=[
            FunnelStep(name="entry", url="file://" + str(first.resolve())),
            FunnelStep(name="cart", url="file://" + str(last.resolve())),
        ],
    )
    return walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))


def test_a_shipping_offer_is_not_the_price_of_anything(tmp_path):
    """"Free shipping on orders over $75" is a shop giving something away.
    Read as the page's price, it became the baseline that a later "total" was
    measured against."""
    state = _states_for(SHELF, tmp_path)[0]
    assert state.price != 75.0, (
        "the free-shipping threshold was read as the product price"
    )
    names = " ".join(i["name"].lower() for i in state.line_items)
    assert "shipping" not in names, f"the offer became a line item: {state.line_items}"


def test_the_same_shelf_seen_twice_is_not_a_basket(tmp_path):
    """The crawler clicked "0 items in cart, view bag" and got the same grid.
    Identical itemisation at two steps means nothing was added to anything."""
    trace = _two_step_trace(SHELF, SHELF, tmp_path)
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-08" not in codes, (
        f"an empty cart was reported for concealing charges: {codes}"
    )


def test_a_page_that_never_states_a_total_cannot_have_one_summed_for_it(tmp_path):
    """A checkout says "Total". A product grid does not. Summing rows on a
    page that states no total is inventing a figure and then attributing it
    to the shop."""
    trace = _two_step_trace(
        '<!doctype html><meta charset="utf-8"><body><p>Tee $30.00</p></body>',
        SHELF, tmp_path)
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-08" not in codes, f"a total was invented for a shelf: {codes}"


def test_an_overlay_that_asks_for_nothing_is_not_a_forced_action(tmp_path):
    """DP-04 is named for the action a user is forced INTO. The detector was
    never checking that one existed, so a full-viewport overlay demanding
    nothing was reported as leaving 'compliance as the only way forward' --
    twice, against the same real site."""
    page = tmp_path / "overlay.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><body><p>Tee $30.00</p>'
        '<div role="dialog" style="position:fixed;top:0;left:0;width:1280px;'
        'height:900px;z-index:99"><p>Our biggest sale of the year</p></div></body>',
        encoding="utf-8")
    adapter = SiteAdapter(
        site_name="shop with a promo overlay",
        funnel_steps=[FunnelStep(name="entry", url="file://" + str(page.resolve()))],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-04" not in codes, f"an advert was called a forced action: {codes}"


def test_an_overlay_that_does_demand_something_still_fires(tmp_path):
    """The other half of the measurement. Narrowing DP-04 must not mute it:
    an inescapable overlay demanding an email address is the real pattern."""
    page = tmp_path / "gate.html"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><body><p>Tee $30.00</p>'
        '<div role="dialog" style="position:fixed;top:0;left:0;width:1280px;'
        'height:900px;z-index:99"><p>Enter your email address to continue</p>'
        '<input type="email"></div></body>',
        encoding="utf-8")
    adapter = SiteAdapter(
        site_name="shop with an email wall",
        funnel_steps=[FunnelStep(name="entry", url="file://" + str(page.resolve()))],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))
    codes = [v.pattern_code for v in audit(trace)]
    assert "DP-04" in codes, (
        f"DP-04 was narrowed into silence -- a real email wall no longer fires: {codes}"
    )


# --- The capability, proven on a deceptive page ---------------------------

def test_drip_pricing_is_caught_with_no_machine_readable_data(tmp_path):
    """The other half of the measurement. Reading text is only worth having if
    it still catches the violation -- an extractor that finds nothing also
    produces no false positives."""
    dark = Path(__file__).parent.parent / "fixtures" / "ecommerce_dark"
    stripped = tmp_path / "stripped"
    stripped.mkdir()
    (stripped / "shop.css").write_text((dark / "shop.css").read_text())
    for name in ("listing.html", "cart.html", "checkout.html"):
        text = (dark / name).read_text()
        text = re.sub(r'\s+data-(price|name|total|subtotal)="[^"]*"', "", text)
        text = re.sub(r'<script type="application/ld\+json">.*?</script>', "", text, flags=re.S)
        (stripped / name).write_text(text)

    adapter = SiteAdapter(
        site_name="ShopMart, stripped of machine-readable data",
        funnel_steps=[FunnelStep(name=n.replace(".html", ""),
                                 url="file://" + str((stripped / n).resolve()))
                      for n in ("listing.html", "cart.html", "checkout.html")],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path / "shots"))
    codes = {v.pattern_code for v in audit(trace)}
    assert "DP-08" in codes, (
        f"drip pricing went undetected with no data attributes: {sorted(codes)}. "
        "That is the state the tool was in for every real website."
    )
    assert "DP-02" in codes, f"basket sneaking went undetected: {sorted(codes)}"


def test_a_currency_code_inside_an_ordinary_word_is_not_money():
    """"Rs" is a rupee sign and it is also the middle of English words. The
    product "SNEAKERS Plimsolls" came back as the line item "SNEAKE
    Plimsolls" -- the RS stripped as currency. The same flaw would have made
    "SNEAKERS 55" parse as fifty-five rupees, which is a wrong NUMBER rather
    than a cosmetic blemish."""
    from capture.state_extractor import _line_items_from_text
    items = _line_items_from_text(["SNEAKERS Plimsolls $55.00"])
    assert items and items[0]["name"] == "SNEAKERS Plimsolls", items
    assert parse_money("SNEAKERS 55") is None
