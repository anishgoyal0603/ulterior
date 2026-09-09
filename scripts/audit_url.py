#!/usr/bin/env python
"""
Audit any URL from the command line, and SAY WHAT HAPPENED.

    python scripts/audit_url.py https://www.saucedemo.com/
    python scripts/audit_url.py --preset sandboxes
    python scripts/audit_url.py https://your-store.myshopify.com/ --show-html

Why this exists rather than just using the dashboard: when a real site does
not work, the dashboard tells you there were no findings, and "no findings"
is indistinguishable from "the crawler saw an empty page". This prints the
diagnosis instead -- what rendered, what was read, what was clicked, what was
refused, and why a stage was missed.

That distinction is the whole point. A crawler that captures an empty React
shell and reports "no violations" has told you your site is clean when it
never saw your site. Everything below exists to make that failure visible.

NOTHING HERE IS DESTRUCTIVE. The crawler never clicks a control that could
spend money, subscribe, delete, or sign in -- see NEVER_CLICK in
capture/funnel_discovery.py. It reads pages and stops one button short of
paying.

Please only run this against sites you are allowed to audit: the automation
sandboxes below, an open-source demo instance, or a store you own.

Auditing a store on YOUR OWN machine (LocalWP, Docker, a dev server) needs
one extra switch, because the SSRF guard refuses private addresses by
default and would otherwise just say the page failed to load:

    PowerShell : $env:ALLOW_LOCAL_TARGETS = '1'
    bash       : export ALLOW_LOCAL_TARGETS=1
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Public sandboxes maintained BY the testing community FOR automated tools.
# They are the honest way to prove this works on a site nobody prepared for
# us, without touching a real company's checkout.
PRESETS = {
    "sandboxes": [
        ("Swag Labs (Sauce Labs)", "https://www.saucedemo.com/"),
        ("Automation Exercise", "https://automationexercise.com/products"),
        ("LambdaTest OpenCart playground",
         "https://ecommerce-playground.lambdatest.com/"),
        ("OpenCart official demo", "https://demo.opencart.com/"),
        ("Saleor storefront (React)", "https://demo.saleor.io/"),
    ],
}


def _refuse_early(url: str) -> str:
    """A readable reason this URL will not be crawled, or "".

    Without this, a store running on localhost -- a LocalWP WooCommerce site,
    a Saleor container, `npm run dev` -- fails inside the browser as
    "Could not load the starting URL", which reads like the site is down. It
    is not down: the SSRF guard is refusing a private address on purpose, and
    the person needs to be told which switch turns that off for their own
    machine rather than left debugging their web server.
    """
    from app import config
    from app.ssrf_guard import validate_target_url, UnsafeURLError

    try:
        validate_target_url(url)
        return ""
    except UnsafeURLError as exc:
        if config.ALLOW_LOCAL_TARGETS:
            return f"{exc}"
        return (
            f"{exc}\n"
            "    If this is YOUR OWN store on this machine (LocalWP, a Docker\n"
            "    container, a dev server), set the local-target flag first:\n"
            "        PowerShell : $env:ALLOW_LOCAL_TARGETS = '1'\n"
            "        bash       : export ALLOW_LOCAL_TARGETS=1\n"
            "    It is a development flag and it is off by default because a\n"
            "    server that will fetch private addresses on request is an SSRF\n"
            "    hole. Never set it on a deployed instance."
        )


def audit_one(url: str, name: str = "", show_html: bool = False, headless: bool = True):
    from capture.funnel_discovery import walk_discovered_funnel
    from capture.state_extractor import parse_money
    from detectors.pipeline import audit

    label = name or url
    print("=" * 78)
    print(f"  {label}")
    print(f"  {url}")
    print("=" * 78)

    refusal = _refuse_early(url)
    if refusal:
        print(f"  NOT CRAWLED: {refusal}")
        return {"url": url, "name": label, "error": refusal.splitlines()[0]}

    try:
        trace = walk_discovered_funnel(url, headless=headless)
    except Exception as exc:
        print(f"  CRAWL FAILED: {type(exc).__name__}: {exc}")
        print("  Nothing below is meaningful -- the crawler never reached the site.")
        return {"url": url, "name": label, "error": f"{type(exc).__name__}: {exc}"}

    discovery = trace.discovery or {}
    print(f"\n  Pages walked : {' -> '.join(discovery.get('stages_reached') or []) or 'none'}")
    print(f"  Not found    : {', '.join(discovery.get('stages_not_found') or []) or '-'}")

    print("\n  WHAT THE CRAWLER ACTUALLY READ")
    if not trace.states:
        print("      nothing -- the page did not load")
    for state in trace.states:
        text_len = len(state.full_text or "")
        print(f"    [{state.step_name}]")
        print(f"        rendered text : {text_len} chars"
              + ("   <-- SUSPICIOUSLY EMPTY" if text_len < 200 else ""))
        print(f"        price         : {state.price}  (source: {state.price_source})")
        print(f"        line items    : {len(state.line_items)}")
        for item in state.line_items[:8]:
            print(f"            {item['name'][:44]:46} {item['price']}")
        print(f"        checkboxes    : {len(state.checkboxes)}"
              f"   pre-ticked: {sum(1 for c in state.checkboxes if c.is_prechecked)}")
        print(f"        buttons       : {len(state.buttons)}")
        if show_html:
            print(f"        first 400 chars of text: {(state.full_text or '')[:400]!r}")

    notes = discovery.get("notes") or []
    if notes:
        print("\n  HOW IT WENT")
        for note in notes:
            print(f"    - {note}")

    blocked = getattr(trace, "blocked_requests", None)
    if blocked:
        print("\n  BLOCKED BY THE SSRF GUARD")
        for note in blocked:
            print(f"    - {note}")

    violations = audit(trace)
    print(f"\n  FINDINGS: {len(violations)}")
    for v in violations:
        tier = v.evidence.get("evidence_tier", "?")
        print(f"    {v.pattern_code} [{tier}] conf={v.confidence}")
        print(f"        {v.explanation}")

    # The honest verdict. "No findings" only means something if the crawler
    # actually saw a shop.
    read_anything = any((s.full_text or "") and len(s.full_text) >= 200 for s in trace.states)
    found_money = any(s.price is not None for s in trace.states)
    print("\n  VERDICT")
    if not read_anything:
        print("    INCONCLUSIVE -- the crawler read almost no text. Either the")
        print("    content is drawn by JavaScript that had not finished, or a")
        print("    challenge/login page was served. This is NOT a clean result.")
    elif not found_money:
        print("    PARTIAL -- pages rendered, but no price could be read. The")
        print("    price detectors (DP-08 especially) cannot run without one.")
        print("    Send this output and I will teach the extractor this layout.")
    elif not violations:
        print("    CLEAN -- pages rendered, prices were read, and no CCPA-13")
        print("    pattern was found. This is a real result.")
    else:
        print(f"    {len(violations)} finding(s) on a page the crawler genuinely read.")

    return {
        "url": url, "name": label,
        "stages": discovery.get("stages_reached"),
        "read_anything": read_anything, "found_money": found_money,
        "prices": [s.price for s in trace.states],
        "findings": [{"code": v.pattern_code,
                      "tier": v.evidence.get("evidence_tier"),
                      "confidence": v.confidence} for v in violations],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="the URL to audit")
    ap.add_argument("--preset", choices=sorted(PRESETS),
                    help="audit a built-in list of automation sandboxes")
    ap.add_argument("--show-html", action="store_true",
                    help="print the first 400 characters of each page's text")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window instead of running headless")
    ap.add_argument("--json", metavar="FILE",
                    help="also write the machine-readable summary here")
    args = ap.parse_args()

    if not args.url and not args.preset:
        ap.error("give a URL, or --preset sandboxes")

    targets = PRESETS[args.preset] if args.preset else [("", args.url)]
    results = [audit_one(url, name, args.show_html, headless=not args.headed)
               for name, url in targets]

    if len(results) > 1:
        print("\n" + "=" * 78)
        print("  SUMMARY")
        print("=" * 78)
        for r in results:
            if r.get("error"):
                verdict = f"CRAWL FAILED ({r['error'][:40]})"
            elif not r["read_anything"]:
                verdict = "INCONCLUSIVE (page did not render)"
            elif not r["found_money"]:
                verdict = "PARTIAL (no price read)"
            else:
                verdict = f"{len(r['findings'])} finding(s)"
            print(f"    {r['name'][:42]:44} {verdict}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
        print(f"\n  Written to {args.json}")


if __name__ == "__main__":
    main()
