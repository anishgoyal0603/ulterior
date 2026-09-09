# Real-world validation data

Source: Arunesh Mathur et al., "Dark Patterns at Scale: Findings from a
Crawl of 11K Shopping Websites," CSCW 2019.
Paper: https://arxiv.org/pdf/1907.07032.pdf
Code: https://github.com/aruneshmathur/dark-patterns (GPL-3.0)
Full raw crawl archives (hundreds of GB): https://darkpatterns.cs.princeton.edu/data/

`mathur2019_dark_patterns.csv` — 1,818 real dark-pattern instances scraped
from real e-commerce checkout/product pages, with verbatim text, category,
type, and a ground-truth "Deceptive?" label (Yes/No/Depends). This is the
processed output referenced by the paper, not a raw crawl archive.

`mathur2019_ranked_sites.csv` — 11,266 ranked shopping sites with dp/deceptive
boolean flags, category, and traffic metrics.

## Why this matters here

This is the ONLY real-world validation this project's detectors have been
tested against — every other fixture in `fixtures/` is synthetic, built by
hand to embed patterns the author already knew about. Testing against this
data surfaced real regex gaps (curly-apostrophe handling, missing phrasings
like "in high demand", "sell out fast") that synthetic fixtures never would
have. See `tests/test_real_world_validation.py` for the regression tests
that keep these detection rates from silently drifting.

Current validated detection rates (Layer 1 urgency-language + Layer 3
confirm-shaming offline stub, regex-only, no LLM):
- Urgency language (Low-stock/High-demand/Limited-time): 94.1% (721/766)
- Confirm-shaming: 92.9% (157/169)

These are NOT end-to-end pipeline accuracy numbers (this dataset has no
funnel/screenshot data, so Layer 1's drip-pricing/basket-sneaking/fake-
urgency-via-reload logic and Layer 2's visual geometry can't be tested
against it) -- they measure specifically whether the TEXT-MATCHING layer
recognizes real-world phrasing. That's a meaningfully different and more
honest claim than "the tool is 94% accurate."

## License note

Mathur et al.'s code is GPL-3.0. This CSV is data, not their code, and is
used here for research validation. If you redistribute this project,
keep this attribution and the paper citation intact.
