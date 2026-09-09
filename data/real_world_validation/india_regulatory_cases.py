"""
Real-world regulatory case index for India's travel/e-commerce sector.

Every entry here is built from PUBLICLY REPORTED regulatory action or named
industry research -- NOT from a live crawl of any named company's site.
This project has no live observation of MakeMyTrip, Goibibo, Vistara, or
any other named platform's current site content. What follows is a
paraphrased, cited summary of official/reported findings, used the same
way case_resonance-style grounding is used elsewhere in this project: to
give a live audit's output a real reference point, not to accuse a named
company of anything this project personally observed.

Sources (see each entry's `source_note`):
- CCPA enforcement actions reported to the Rajya Sabha (Parliament),
  covered by Outlook Business, Sept 2026.
- CCI (Competition Commission of India) inquiry into MakeMyTrip/Goibibo,
  as summarized in Legal Vidhiya's review of Indian dark-pattern case law.
- Datum Intelligence, "Dark Patterns in India's Online Marketplaces"
  (B-Index scoring), as reported by MediaNama, June 2026.
- Ministry of Consumer Affairs statements on the 26-platform self-audit
  declarations, as reported by The Tribune.

IMPORTANT — what this is and isn't:
- This IS a legitimate way to ground a live audit's findings in documented
  regulatory precedent ("this pattern maps to a category the CCI/CCPA has
  already acted on for this exact sector").
- This is NOT a claim that any named company currently exhibits any
  specific pattern on their live site today. No live crawl was performed
  against any of these companies from this project. See README's
  "Honest scope statement" and the Railway/ToS note for why.
"""

REGULATORY_CASES = [
    {
        "id": "IN-CCI-01",
        "title": "CCI inquiry into MakeMyTrip / Goibibo pricing practices",
        "summary": (
            "The Competition Commission of India opened an inquiry into "
            "MakeMyTrip and Goibibo over alleged anti-competitive practices "
            "and price manipulation. The complaint alleged false discounting "
            "and misleading price representations -- officially characterized "
            "in subsequent legal analysis as consistent with bait-and-switch "
            "and drip-pricing patterns, though the CCI's own inquiry was "
            "framed through a competition-law lens rather than the CCPA's "
            "dark-patterns framework specifically."
        ),
        "ccpa_pattern_codes": ["DP-07", "DP-08"],
        "source_note": (
            "CCI inquiry as summarized in Legal Vidhiya, 'Dark Patterns in "
            "Online Shopping and Consumer Deception' (2025). This is a "
            "reported regulatory action, not a live observation by this project."
        ),
    },
    {
        "id": "IN-CCPA-02",
        "title": "CCPA notices for basket sneaking, false urgency, misleading ads",
        "summary": (
            "The CCPA issued notices to platforms including BookMyShow, "
            "MakeMyTrip, and Flipkart for practices including basket "
            "sneaking, false urgency, and misleading advertisements, as "
            "part of its ongoing enforcement of the 2023 Dark Patterns "
            "Guidelines."
        ),
        "ccpa_pattern_codes": ["DP-02", "DP-01"],
        "source_note": (
            "As reported in Neetiniyaman's overview of India's dark-pattern "
            "regulatory framework (2025), citing CCPA enforcement actions."
        ),
    },
    {
        "id": "IN-CCPA-03",
        "title": "CCPA penalties against 9 platforms (Rajya Sabha disclosure)",
        "summary": (
            "The Government disclosed to the Rajya Sabha that the CCPA has "
            "penalised nine digital platforms -- including Zepto, "
            "BookMyShow, IndiGo, Physics Wallah, FirstCry, and SpiceJet -- "
            "for practices including hidden charges, pre-selected options, "
            "misleading urgency messages, and subscription traps."
        ),
        "ccpa_pattern_codes": ["DP-08", "DP-06", "DP-01", "DP-05"],
        "source_note": (
            "Reported by Outlook Business, 'Inside India's Dark Pattern "
            "Crackdown' (Sept 2026), citing a written Parliamentary reply."
        ),
    },
    {
        "id": "IN-INDUSTRY-04",
        "title": "Datum Intelligence B-Index: travel sector findings",
        "summary": (
            "An industry report scoring platforms on a 'B-Index' of "
            "dark-pattern harm found Cleartrip (Flipkart-owned) the most "
            "harmful travel booking platform (B-Index 85.2), while "
            "MakeMyTrip ranked among the safest (B-Index 9.4). The most "
            "common patterns found across e-commerce platforms generally "
            "were false urgency, drip pricing, nagging, basket sneaking, "
            "and disguised or fake reviews; 63% of platforms studied showed "
            "hidden charges or drip pricing, up from 52% the prior year."
        ),
        "ccpa_pattern_codes": ["DP-01", "DP-08", "DP-10", "DP-02"],
        "source_note": (
            "Datum Intelligence, 'Dark Patterns in India's Online "
            "Marketplaces,' as reported by MediaNama (June 2026). Notable: "
            "this finding runs COUNTER to an assumption that MakeMyTrip is "
            "a bad actor -- it's reported as comparatively safe in this "
            "specific study. Stated here to avoid this project asserting "
            "the opposite without basis."
        ),
    },
]


def find_cases_for_pattern(ccpa_code: str) -> list:
    """Return documented regulatory cases relevant to a given CCPA-13 code."""
    return [c for c in REGULATORY_CASES if ccpa_code in c["ccpa_pattern_codes"]]
