"""
The false-positive benchmark: seven COMPLIANT shops that must come back clean.

WHY THIS IS THE MOST IMPORTANT TEST FILE IN THE PROJECT

Every other test asks "does the detector find the dark pattern?". This one
asks the question that actually decides whether this tool can be used on a
real company: "does it stay quiet about a shop doing nothing wrong?"

A finding here is not a failing assertion in the abstract. It is this tool
publishing that a named business's checkout is deceptive when it is not. That
is a defamation exposure and it is the fastest way for the project to lose
credibility in front of anyone who checks. A missed detection costs a finding;
a false positive costs the argument.

Three code-review passes had found bugs in the detectors. This corpus found
another one on its first run -- DP-08 reporting a shop at PROVABLE 1.0 for
charges that page had disclosed in the sentence under its price -- and it was
invisible to two hundred passing tests, because no test had ever shown the
detectors an honest page and checked they said nothing.

WHAT EACH SHOP IS FOR

Every shop is built around a pattern that is TEMPTING to flag and lawful to
do. They are the compliant twin of each dark fixture:

  Kesari Home     a real sale with a real deadline, and a timer that genuinely
                  counts down. Says "Hurry". DP-01 must not fire on urgency
                  language attached to a claim that is true.
  Anand Cycles    "In stock: 3 units" -- honestly low stock, stated as a fact.
  Bharat Books    a newsletter box, in the footer of nearly every shop alive.
                  DP-05 must not read "Subscribe" as a paid commitment.
  Sahaj Foods     a REQUIRED privacy-policy consent mentioning delivery
                  partners, plus an OPTIONAL unticked marketing box, plus a
                  delivery fee disclosed up front. DP-04 and DP-08 bait.
  Nakshatra       an optional add-on, unticked, with accept and decline given
                  the same visual weight. DP-02 and DP-06 bait.
  Mandi Direct    a paid placement labelled "Sponsored" in readable type.
  Patrika Plus    a genuine subscription whose recurring terms are disclosed
                  at the point of commitment, with a one-step cancel route.

HOW TO USE IT WHEN A DETECTOR CHANGES

If a change here makes a shop report something, the change is wrong until
proven otherwise -- not the corpus. Read what fired and ask whether a real
shop doing that deserves to be publicly called deceptive.

Add a shop whenever a new detector lands, or whenever someone finds real-world
copy this tool gets wrong. The corpus is only as good as the honest pages in
it, and it is the cheapest place in this project to buy confidence.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from capture.adapters.base import SiteAdapter, FunnelStep
from capture.funnel_walker import walk_funnel
from detectors.pipeline import audit

CORPUS = Path(__file__).parent.parent / "fixtures" / "compliant_corpus"


def _url(filename: str) -> str:
    return "file://" + str((CORPUS / filename).resolve())


# name -> the funnel a shopper walks, as (step name, file)
SHOPS = {
    "kesari_home_real_deadline_sale": [
        ("product", "sale_product.html"), ("checkout", "sale_checkout.html")],
    "anand_cycles_honestly_low_stock": [
        ("product", "stock_product.html"), ("checkout", "stock_checkout.html")],
    "bharat_books_newsletter_footer": [
        ("product", "newsletter_product.html"), ("checkout", "newsletter_checkout.html")],
    "sahaj_foods_lawful_consent_and_disclosed_fee": [
        ("product", "consent_product.html"), ("checkout", "consent_checkout.html")],
    "nakshatra_optional_addon_unticked": [
        ("cart", "addon_cart.html"), ("checkout", "addon_checkout.html")],
    "mandi_direct_labelled_sponsored": [
        ("listing", "sponsored_listing.html"), ("checkout", "sponsored_checkout.html")],
    "patrika_plus_disclosed_subscription": [
        ("offer", "subscription_offer.html"), ("cancel", "subscription_cancel.html")],
}


def _audit_shop(name, steps, tmp_path):
    adapter = SiteAdapter(
        site_name=name,
        funnel_steps=[FunnelStep(name=s, url=_url(f)) for s, f in steps],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path))
    return audit(trace)


def _describe(violations):
    return "\n".join(
        f"  {v.pattern_code} [{v.evidence.get('evidence_tier', '?')}] "
        f"conf={v.confidence} :: {v.explanation}"
        for v in violations
    )


@pytest.mark.parametrize("shop", sorted(SHOPS))
def test_a_compliant_shop_produces_no_findings(shop, tmp_path):
    """Nothing at all. Not a low-confidence hint, not an indicative nudge.

    A tier is a statement about how sure we are, not a licence to report a
    shop that has done nothing. If a compliant page trips a detector, the
    detector's rule is too broad, and the fix is the rule -- never a note in
    the report telling the reader to ignore it.
    """
    violations = _audit_shop(shop, SHOPS[shop], tmp_path)
    assert violations == [], (
        f"{shop} is a COMPLIANT shop and the auditor reported it:\n"
        + _describe(violations)
        + "\n\nThis is the tool calling an honest seller deceptive. Fix the "
          "detector, not this test."
    )


def test_the_whole_corpus_is_clean_and_reports_its_own_rate(tmp_path):
    """One number, so a regression is visible at a glance rather than as a
    single parametrised case failing among many."""
    findings = {}
    for shop, steps in SHOPS.items():
        violations = _audit_shop(shop, steps, tmp_path / shop)
        if violations:
            findings[shop] = violations

    rate = len(findings) / len(SHOPS)
    detail = "\n".join(f"{s}:\n{_describe(v)}" for s, v in findings.items())
    assert not findings, (
        f"false-positive rate {rate:.0%} ({len(findings)} of {len(SHOPS)} "
        f"compliant shops reported):\n{detail}"
    )


def test_the_corpus_actually_exercises_the_detectors(tmp_path):
    """A corpus of blank pages would also come back clean.

    This asserts the honest shops contain the material that TEMPTS each
    detector -- urgency wording, a consent checkbox, a subscription, an added
    fee -- so a pass means the detectors looked and declined, not that there
    was nothing to look at.
    """
    text = " ".join((CORPUS / f).read_text().lower()
                    for f in sorted(p.name for p in CORPUS.glob("*.html")))
    for bait in ("hurry", "in stock", "subscribe", "privacy policy",
                 "promotions", "sponsored", "delivery", "renews automatically",
                 "checkbox" if "checkbox" in text else "type=\"checkbox\""):
        assert bait in text, f"the corpus no longer contains {bait!r} to tempt a detector"


def test_the_dark_storefront_still_fires_so_the_fix_was_not_a_silencer(tmp_path):
    """The cheapest way to pass the corpus is to break the detectors.

    This is the other half of the measurement: the deliberately dark fixture
    must still produce its findings, including the drip-pricing one whose rule
    was narrowed to stop reporting disclosed fees.
    """
    dark = Path(__file__).parent.parent / "fixtures" / "ecommerce_dark"
    adapter = SiteAdapter(
        site_name="ShopMart (dark control)",
        funnel_steps=[
            FunnelStep(name="listing", url="file://" + str((dark / "listing.html").resolve())),
            FunnelStep(name="cart", url="file://" + str((dark / "cart.html").resolve())),
            FunnelStep(name="checkout", url="file://" + str((dark / "checkout.html").resolve())),
        ],
    )
    trace = walk_funnel(adapter, screenshot_dir=str(tmp_path))
    codes = {v.pattern_code for v in audit(trace)}
    for expected in ("DP-02", "DP-06", "DP-08"):
        assert expected in codes, (
            f"{expected} stopped firing on the dark storefront: {sorted(codes)}. "
            "A quiet detector passes the compliant corpus for the wrong reason."
        )
