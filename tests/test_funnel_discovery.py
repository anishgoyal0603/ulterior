"""
Tests for automatic funnel discovery.

The most important test in this file is `test_never_clicks_a_control_that_
spends_money`. Everything else here is about coverage; that one is about not
placing an order on a stranger's payment card. Discovery exists to walk REAL
shops, and a real shop's checkout page has a button that commits a purchase.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.funnel_discovery import (
    walk_discovered_funnel, _is_forbidden, _find_control, NEVER_CLICK, STAGES,
    DiscoveryLog,
)
from detectors.pipeline import audit

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _file_url(folder: str, page: str) -> str:
    return "file://" + str((FIXTURES / folder / page).resolve())


@pytest.fixture(scope="module")
def discovered(tmp_path_factory):
    trace = walk_discovered_funnel(
        _file_url("ecommerce_dark", "listing.html"),
        screenshot_dir=str(tmp_path_factory.mktemp("disc")),
    )
    return trace, audit(trace)


# --- The safety rail -----------------------------------------------------

@pytest.mark.parametrize("label", [
    "Pay Now", "PAY NOW", "Place Order", "Place the order", "Confirm Order",
    "Complete Purchase", "Buy Now", "Subscribe", "Start my free trial",
    "Upgrade", "Donate", "Delete", "Remove", "Sign in", "Log In", "Register",
])
def test_never_clicks_a_control_that_spends_money_or_signs_in(label):
    """Discovery walks real shops. If it ever pressed one of these, it would
    place a real order, start a real subscription, or delete someone's data.
    This is checked before every click, and beats any intent match."""
    assert _is_forbidden(label) is not None, f"{label!r} must be on the never-click list"


@pytest.mark.parametrize("label", [
    "Add to Cart", "Proceed to Checkout", "View Product", "Continue",
    "View Bag", "Add to basket",
])
def test_navigation_controls_are_not_blocked(label):
    """The rail must not be so broad that discovery cannot move at all."""
    assert _is_forbidden(label) is None, f"{label!r} should be clickable"


def test_buy_now_is_forbidden_even_though_it_looks_like_navigation():
    """'Buy Now' reads like a product-page link and on many Indian storefronts
    it skips the cart and goes straight to payment. It is on the never-click
    list for exactly that reason, and it must stay there."""
    assert _is_forbidden("Buy Now") is not None
    assert any("buy now" in pattern for pattern in NEVER_CLICK)


def test_a_forbidden_control_is_refused_and_recorded_not_silently_skipped(tmp_path):
    """Refusing to click is correct, but silence would leave an unexplained
    gap in the funnel. The refusal has to appear in the discovery log."""
    page = tmp_path / "checkout.html"
    page.write_text(
        "<html><body><h1>Checkout</h1>"
        "<button>Place Order</button>"
        "</body></html>"
    )
    trace = walk_discovered_funnel("file://" + str(page), screenshot_dir=str(tmp_path / "shots"))
    notes = " ".join(trace.discovery["notes"]).lower()
    assert "refused to click" in notes
    assert "place order" in notes


# --- Does it actually work -----------------------------------------------

def test_discovers_the_whole_funnel_from_a_single_url(discovered):
    trace, _ = discovered
    steps = [s.step_name for s in trace.states]
    assert steps == ["entry", "product", "cart", "checkout"], steps


def test_finds_the_same_violations_as_a_handwritten_adapter(discovered):
    """The point of discovery is that nobody has to write an adapter. If it
    found less than the adapter does, it would not be a substitute for one."""
    _, violations = discovered
    codes = {v.pattern_code for v in violations}
    for expected in ("DP-01", "DP-02", "DP-03", "DP-06", "DP-08"):
        assert expected in codes, f"{expected} missing from a discovered walk: {codes}"


def test_drip_pricing_works_across_discovered_steps(discovered):
    """DP-08 compares the first disclosed price against the final total. That
    only works if discovery reached both ends of the funnel."""
    _, violations = discovered
    drip = [v for v in violations if v.pattern_code == "DP-08"]
    assert drip, "no drip pricing finding from a discovered funnel"
    assert drip[0].evidence["hidden_amount"] > 0


# --- Honesty about what it could not do ----------------------------------

def test_reports_the_stages_it_could_not_find(discovered):
    trace, _ = discovered
    assert "order_summary" in trace.discovery["stages_not_found"]
    assert trace.discovery["stages_reached"][0] == "entry"


def test_says_so_plainly_when_only_one_page_could_be_examined(tmp_path):
    """A dead end on a real site is usually bot protection. Reporting 'no
    findings' without saying only one page was seen would read as a clean
    bill of health for a site that was never actually examined."""
    page = tmp_path / "wall.html"
    page.write_text("<html><body><h1>Access denied</h1></body></html>")
    trace = walk_discovered_funnel("file://" + str(page), screenshot_dir=str(tmp_path / "s"))
    assert len(trace.states) == 1
    notes = " ".join(trace.discovery["notes"]).lower()
    assert "only the starting page" in notes
    assert "bot protection" in notes


def test_an_unreachable_url_is_reported_not_raised(tmp_path):
    """A bad URL must come back as an explained empty result, not a 500."""
    trace = walk_discovered_funnel(
        "file:///definitely/not/a/real/path/nope.html",
        screenshot_dir=str(tmp_path / "s"),
    )
    assert trace.discovery["notes"], "an unreachable URL produced no explanation"


def test_intent_order_prefers_add_to_cart_over_a_generic_continue():
    """A product page often has both. Clicking 'Continue' there would skip the
    cart, and the cart is where basket sneaking lives."""
    cart_intents = dict(STAGES)["cart"]
    checkout_intents = dict(STAGES)["checkout"]
    assert any("add to" in p for p in cart_intents)
    assert any(r"\bcontinue\b" in p for p in checkout_intents)
    assert STAGES.index(("cart", cart_intents)) < STAGES.index(("checkout", checkout_intents))
