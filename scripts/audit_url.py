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


def _print_states(trace, show_html: bool = False):
    """What the crawler actually READ, page by page.

    This is the part that makes a result checkable. "No findings" and "the
    crawler saw an empty page" are the same sentence unless somebody prints
    the rendered text length, the price and where it came from, and the
    overlays -- so this prints all of them, next to every finding.
    """
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
        # Overlays, spelled out. A DP-04 finding is a claim about what an
        # overlay did NOT offer, and that claim cannot be checked from a
        # confidence score. Printing what the crawler saw -- how much it
        # covered, whether the contents were readable, which dismissal
        # controls it recognised -- is what let two false positives on this
        # detector be diagnosed instead of argued about.
        for modal in state.modals:
            if not modal.is_blocking or modal.viewport_coverage < 0.10:
                continue
            print(f"        overlay       : {modal.viewport_coverage:.0%} of viewport"
                  f"   readable: {not getattr(modal, 'contents_unreadable', False)}"
                  f"   dismiss: {modal.dismiss_controls or 'none recognised'}")
            snippet = " ".join((modal.text or "").split())[:120]
            print(f"            says: {snippet!r}")
        if show_html:
            print(f"        first 400 chars of text: {(state.full_text or '')[:400]!r}")


def audit_saved_pages(paths, show_html: bool = False):
    """Audit pages you saved from your own browser, in funnel order.

    WHY THIS MODE EXISTS, AND WHY IT IS THE ONLY HONEST WAY TO STUDY A BIG SITE

    Crawling a large commercial site does not work and should not be done:

      * It does not work. Amazon, Flipkart, MakeMyTrip and their peers run bot
        protection. A crawler gets a challenge page, and this tool correctly
        reports INCONCLUSIVE -- a result you cannot publish or even learn from.
        Their checkouts are also behind a login this tool will never type into.

      * It should not be done. Automated access is against those sites' terms,
        and a finding is a public statement that a NAMED company deceives its
        customers. You would be making that statement from a crawl you had no
        permission to run, using a tool that produced four false positives in
        its first two runs against real pages.

    But the underlying question -- do the sites everyone actually uses employ
    dark patterns? -- is a good one, and it has a clean answer. You browse the
    site yourself, as an ordinary customer, exactly as you are entitled to do.
    You save the pages you were shown. Then the tool measures what YOU were
    shown, rather than crawling anyone.

    That is the difference between an automated intrusion and a consumer
    documenting their own transaction, and it is the method behind the most
    useful evidence this project has received: a set of real checkout
    screenshots that exposed defects no amount of code review had found.

    HOW TO CAPTURE THE PAGES

      1. Browse the funnel yourself: product page -> cart -> checkout. Stop
         before paying.
      2. On each page: Ctrl+S, choose "Webpage, Complete", into one folder.
      3. Pass them here IN THE ORDER YOU SAW THEM.

    DP-08 needs at least two steps -- it compares the price you were quoted
    against the total you were finally asked for. DP-01's countdown check
    cannot run on a saved page at all, because a saved page cannot be reloaded
    with real time passing; the tool will simply not report it.
    """
    from capture.adapters.base import SiteAdapter, FunnelStep
    from capture.funnel_walker import walk_funnel
    from detectors.pipeline import audit

    steps = []
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            print(f"  NOT FOUND: {resolved}")
            return {"error": f"file not found: {resolved}"}
        steps.append(FunnelStep(name=resolved.stem[:40], url="file://" + str(resolved)))

    print("=" * 78)
    print("  Pages you saved from your own browser")
    for step in steps:
        print(f"    {step.name}")
    print("=" * 78)

    trace = walk_funnel(SiteAdapter(site_name="saved pages", funnel_steps=steps))
    _print_states(trace, show_html)

    violations = audit(trace)
    print(f"\n  FINDINGS: {len(violations)}")
    for v in violations:
        print(f"    {v.pattern_code} [{v.evidence.get('evidence_tier', '?')}] conf={v.confidence}")
        print(f"        {v.explanation}")
    if not violations:
        print("    Nothing found on the pages as saved.")
    print("\n  Check every finding against the page in front of you before you "
          "repeat it anywhere.")
    return {
        "mode": "saved_pages", "steps": [s.name for s in steps],
        "findings": [{"code": v.pattern_code, "tier": v.evidence.get("evidence_tier"),
                      "confidence": v.confidence, "explanation": v.explanation}
                     for v in violations],
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
        # A DNS failure is not a policy refusal, and printing the local-target
        # advice for one actively misleads: the LambdaTest playground failed to
        # resolve, and the tool answered by explaining how to audit a store on
        # your own machine. Different problem, different fix.
        if "resolve" in str(exc).lower():
            return (
                f"{exc}\n"
                "    That is a DNS failure, not a refusal by this tool -- the\n"
                "    hostname could not be looked up at all. Open the URL in a\n"
                "    browser: if it does not load there either, the site is down\n"
                "    or your network is blocking it, and there is nothing to fix\n"
                "    here."
            )
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

    _print_states(trace, show_html)

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
    ap.add_argument("--saved", nargs="+", metavar="FILE",
                    help="audit pages you saved from your own browser (Ctrl+S, "
                         '"Webpage, Complete"), listed in the order you saw '
                         "them. This is how to study a big commercial site: it "
                         "measures what you were shown, instead of crawling "
                         "anyone.")
    ap.add_argument("--show-html", action="store_true",
                    help="print the first 400 characters of each page's text")
    ap.add_argument("--headed", action="store_true",
                    help="show the browser window instead of running headless")
    ap.add_argument("--json", metavar="FILE",
                    help="also write the machine-readable summary here")
    args = ap.parse_args()

    if not args.url and not args.preset and not args.saved:
        ap.error("give a URL, --preset sandboxes, or --saved page1.html page2.html")

    if args.saved:
        results = [audit_saved_pages(args.saved, args.show_html)]
    else:
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
