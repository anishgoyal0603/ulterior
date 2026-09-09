"""
The product's own pages must render without inline <style> or <script>.

WHY THIS FILE EXISTS: the demo and dashboard originally carried their CSS and
JS inline. Served by our own app that is fine -- our CSP allows 'unsafe-inline'
because the audited storefront fixtures need it. But a page is not only ever
viewed through our own headers. Some embedded browser views (an IDE preview
pane, an in-app webview, a corporate proxy that rewrites CSP) impose a stricter
policy of their own, and under `style-src 'self'` an inline <style> block is
silently dropped.

The result was not a subtle degradation. The whole UI collapsed to unstyled
Times New Roman with a dead iframe, and there is no error on the page -- only a
console message the viewer never opens. It looked like the product was broken.

Keeping CSS and JS in their own same-origin files costs nothing and makes the
page render under any policy that allows 'self', which is nearly all of them.
These tests keep it that way.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PAGES = [
    ("demo/index.html", ["demo.css", "demo.js"]),
    ("dashboard/index.html", ["dashboard.css", "dashboard.js"]),
]


@pytest.mark.parametrize("page,assets", PAGES)
def test_page_has_no_inline_style_or_script_block(page, assets):
    html = (ROOT / page).read_text()
    assert "<style>" not in html, (
        f"{page} has an inline <style> block. A viewer with style-src 'self' "
        f"will drop it and the page will render as unstyled Times New Roman."
    )
    assert not re.search(r"<script(?![^>]*\ssrc=)[^>]*>", html), (
        f"{page} has an inline <script> block. A viewer with script-src 'self' "
        f"will refuse to run it and the page will do nothing."
    )


@pytest.mark.parametrize("page,assets", PAGES)
def test_page_has_no_inline_style_attributes(page, assets):
    """style="..." is blocked by the same directive as a <style> block, and it
    cannot be rescued by a hash. Utility classes in the stylesheet instead."""
    html = (ROOT / page).read_text()
    offenders = re.findall(r'style="[^"]*"', html)
    assert offenders == [], (
        f"{page} carries {len(offenders)} inline style attribute(s): {offenders[:3]}"
    )


@pytest.mark.parametrize("page,assets", PAGES)
def test_page_has_no_inline_event_handlers(page, assets):
    """onclick="..." is inline script by another name, blocked the same way."""
    html = (ROOT / page).read_text()
    handlers = re.findall(r'\son[a-z]+="[^"]*"', html)
    assert handlers == [], f"{page} has inline handlers: {handlers[:3]}"


@pytest.mark.parametrize("page,assets", PAGES)
def test_referenced_assets_exist_and_are_not_empty(page, assets):
    """Extracting the blocks is only a fix if the files actually got written --
    an empty or missing stylesheet renders exactly like a blocked inline one."""
    html = (ROOT / page).read_text()
    folder = (ROOT / page).parent
    for asset in assets:
        assert asset in html, f"{page} does not reference {asset}"
        path = folder / asset
        assert path.exists(), f"{page} references {asset}, which does not exist"
        assert len(path.read_text().strip()) > 200, f"{asset} is suspiciously empty"


def test_dashboard_utility_classes_are_all_defined():
    """The inline style attributes became .u1../.uN classes. A class used in the
    markup but missing from the stylesheet would silently lose that styling."""
    html = (ROOT / "dashboard/index.html").read_text()
    css = (ROOT / "dashboard/dashboard.css").read_text()
    used = set(re.findall(r"\bu\d+\b", html))
    assert used, "no utility classes found -- has the extraction been undone?"
    for cls in sorted(used):
        assert f".{cls} " in css or f".{cls}{{" in css, f".{cls} is used but never defined"


def test_external_scripts_are_deferred_not_racing_the_dom():
    """The scripts used to sit at the end of <body>, so the DOM was parsed by
    the time they ran. As external files they need `defer` to keep that
    guarantee -- without it they can run before the elements they query exist."""
    for page, _ in PAGES:
        html = (ROOT / page).read_text()
        for tag in re.findall(r"<script[^>]*src=[^>]*>", html):
            assert "defer" in tag or "async" in tag or "module" in tag, (
                f"{page}: {tag} may run before the DOM it queries exists"
            )


@pytest.mark.parametrize("script", ["demo/demo.js", "dashboard/dashboard.js"])
def test_the_scripts_do_not_generate_inline_styles_either(script):
    """Removing style="..." from the HTML is only half the job. The dashboard
    built table cells with innerHTML containing style="color:#666", so the
    last inline styles in the product were generated at runtime and survived
    the sweep of the markup. They fail the same two ways: a viewer whose CSP
    forbids inline styles drops them, and a hardcoded light-mode grey is
    nearly invisible on the dark surface."""
    text = (ROOT / script).read_text()
    code = "\n".join(line for line in text.splitlines()
                     if not line.strip().startswith(("//", "*", "/*")))
    assert 'style="' not in code, f"{script} generates an inline style attribute"
    assert ".style.cssText" not in code, f"{script} sets style via cssText"
