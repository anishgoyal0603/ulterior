"""
Inspect any real, live URL and report what the generic extractor can
already detect out of the box (schema.org price, line items via common
attributes, checkboxes, accept/decline buttons) -- this tells you exactly
how much of a new SiteAdapter needs custom work versus how much is free.

Usage:
    python -m capture.inspect_site https://example.com/product/123

This is the practical answer to "make it work for a new e-commerce or
flight site": run this against the target's key pages first, see what's
already detected for free, then write a small FunnelStep list (see
capture/adapters/demo_ecommerce.py for the pattern) for the parts that
need it.
"""

import sys
from playwright.sync_api import sync_playwright
from .state_extractor import extract_page_state
from app.privacy import redact


def inspect(url: str, headless: bool = True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(url, timeout=20000)
        page.wait_for_timeout(500)
        state = extract_page_state(page, step_name="inspect")
        browser.close()

    print(f"\n=== Inspection report for {url} ===\n")
    print(f"Price detected: {state.price} (source: {state.price_source})")
    if state.price_source == "not_found":
        print("  -> No schema.org Product/Offer markup or [data-price] found.")
        print("     You'll need a price_selector_override in your SiteAdapter.")
    print(f"\nLine items detected: {len(state.line_items)}")
    for li in state.line_items:
        print(f"  - {redact(li['name'])}: {li['price']}")
    print(f"\nCheckboxes found: {len(state.checkboxes)}")
    for cb in state.checkboxes:
        flag = " <-- PRE-CHECKED" if cb.is_prechecked else ""
        print(f"  - id={cb.id!r} checked={cb.checked} label={redact(cb.label_text[:60])!r}{flag}")
    print(f"\nButtons found: {len(state.buttons)}")
    for b in state.buttons:
        print(f"  - role={b.role} text={redact(b.text[:40])!r} contrast={b.contrast_ratio}")
    print(f"\nUrgency language matched: {state.urgency_phrases_found or 'none'}")
    print("\n=== End of report ===\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m capture.inspect_site <url>")
        sys.exit(1)
    inspect(sys.argv[1])
