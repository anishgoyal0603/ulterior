"""
Build the store uploads for every browser:

    python scripts/package_extension.py

writes
    dist/ulterior-extension-<version>-chrome-edge.zip   Chrome Web Store and Microsoft Edge Add-ons
    dist/ulterior-extension-<version>-firefox.zip       Firefox Add-ons (addons.mozilla.org)

Chrome and Edge take the same package. Firefox needs three manifest changes,
made here so there is one source folder: a background *script* instead of a
service worker, an add-on id, and Mozilla's data-collection declaration
(required for new add-ons since November 2025).

Checks before it zips, so a broken package never reaches a review queue (a
rejection costs days):
  * manifest.json parses and every file it names exists
  * the version in manifest.json matches extension/src/core.js
  * no development-only host (localhost, 127.0.0.1) or <all_urls> in it
  * no network call except the opt-in report in popup.js
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
DIST = ROOT / "dist"
SKIP_SUFFIXES = {".md", ".pyc"}
FIREFOX_ID = "ulterior@theodyssey.iipsdavv"


def fail(msg: str) -> None:
    print(f"NOT PACKAGED: {msg}")
    sys.exit(1)


def check(manifest: dict) -> None:
    version = manifest["version"]
    core = (EXT / "src" / "core.js").read_text(encoding="utf-8")
    if f'const VERSION = "{version}"' not in core:
        fail(f"manifest says {version} but src/core.js has a different VERSION")

    named = set(manifest["icons"].values()) | set(manifest["action"]["default_icon"].values())
    named |= {manifest["background"]["service_worker"], manifest["action"]["default_popup"]}
    for cs in manifest.get("content_scripts", []):
        named |= set(cs["js"])
    missing = sorted(p for p in named if not (EXT / p).is_file())
    if missing:
        fail(f"manifest names files that do not exist: {missing}")

    text = json.dumps(manifest)
    for bad in ("localhost", "127.0.0.1", "<all_urls>"):
        if bad in text:
            fail(f"manifest contains {bad!r} -- that is a test setting, not for a store")

    # The only network calls allowed: the opt-in report in popup.js and the
    # opt-in community prices in background.js -- one fetch each.
    network = re.compile(r"\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|sendBeacon")
    for f in EXT.rglob("*.js"):
        code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines()
                         if not l.strip().startswith(("//", "*", "/*")))
        calls = network.findall(code)
        if f.name == "popup.js" and len(calls) == 1 and "fetch(C.REPORT_ORIGIN + C.REPORT_PATH" in code:
            continue
        if f.name == "background.js" and len(calls) == 1 and "fetch(C.REPORT_ORIGIN + path" in code:
            continue
        if calls:
            fail(f"{f.relative_to(EXT)} makes a network call; the privacy policy says it never does")


def firefox_manifest(manifest: dict) -> dict:
    m = json.loads(json.dumps(manifest))
    m["background"] = {"scripts": ["src/core.js", "src/watch.js", "background.js"]}
    m["browser_specific_settings"] = {"gecko": {
        "id": FIREFOX_ID,
        "strict_min_version": "140.0",
        # Nothing is collected unless the person turns reporting on (asks for
        # "website content") or community prices on (asks for "browsing
        # activity") -- each asked at that moment, in popup.js.
        "data_collection_permissions": {"required": ["none"], "optional": ["websiteContent", "browsingActivity"]},
    }}
    return m


def build(out: Path, manifest: dict) -> None:
    files = sorted(p for p in EXT.rglob("*")
                   if p.is_file() and p.suffix not in SKIP_SUFFIXES and not p.name.startswith(".")
                   and p.name != "manifest.json")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        for p in files:
            z.write(p, p.relative_to(EXT).as_posix())
    print(f"  {out.relative_to(ROOT)}  ({len(files) + 1} files, {out.stat().st_size // 1024} KB)")


def main() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    check(manifest)
    DIST.mkdir(exist_ok=True)
    v = manifest["version"]
    print("Packaged:")
    build(DIST / f"ulterior-extension-{v}-chrome-edge.zip", manifest)
    build(DIST / f"ulterior-extension-{v}-firefox.zip", firefox_manifest(manifest))
    print("Upload: Chrome  https://chrome.google.com/webstore/devconsole")
    print("        Edge    https://partner.microsoft.com/dashboard/microsoftedge/overview")
    print("        Firefox https://addons.mozilla.org/developers/")


if __name__ == "__main__":
    main()
