"""
Checks on the extension that need no browser: the manifest a store reviewer
reads, the promise that nothing leaves the browser, and the core unit tests.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
EXT = ROOT / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
SOURCES = sorted(EXT.rglob("*.js"))


def test_every_file_the_manifest_names_exists():
    """0.1 named three icons that were never committed, so Chrome refused to
    load it at all."""
    named = set(MANIFEST["icons"].values()) | set(MANIFEST["action"]["default_icon"].values())
    named |= {MANIFEST["background"]["service_worker"], MANIFEST["action"]["default_popup"]}
    for cs in MANIFEST["content_scripts"]:
        named |= set(cs["js"])
    missing = [p for p in sorted(named) if not (EXT / p).is_file()]
    assert missing == [], f"manifest names files that do not exist: {missing}"


def test_icons_are_the_sizes_they_claim():
    from PIL import Image
    for size, path in MANIFEST["icons"].items():
        with Image.open(EXT / path) as im:
            assert im.size == (int(size), int(size)), (path, im.size)


def _code(f):
    return "\n".join(l for l in f.read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith(("//", "*", "/*")))


NETWORK = re.compile(r"\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|sendBeacon|EventSource|importScripts\s*\(\s*['\"]https?:")


def test_the_checks_never_talk_to_a_network():
    """The promise on the landing page, the privacy page and the store
    listing: what you browse stays in your browser unless you switch a
    networked feature on. Exactly two network calls exist in the extension:
    the opt-in report (popup.js, sendReport) and opt-in community prices
    (background.js, communityFetch) -- both to the one Ulterior address, both
    without cookies, both behind a setting that is off by default."""
    allowed = {"popup.js", "background.js"}
    offenders = [f.name for f in SOURCES if f.name not in allowed and NETWORK.search(_code(f))]
    assert offenders == [], f"network calls in {offenders}"

    popup = _code(EXT / "popup.js")
    assert len(NETWORK.findall(popup)) == 1
    body = popup.split("async function sendReport(payload) {", 1)[1].split("\n  }\n", 1)[0]
    assert "fetch(C.REPORT_ORIGIN + C.REPORT_PATH" in body and 'credentials: "omit"' in body
    send_handler = popup.split('$("report-send").addEventListener', 1)[1]
    assert "settings.report" in send_handler.split("sendReport(", 1)[0]

    bg = _code(EXT / "background.js")
    assert len(NETWORK.findall(bg)) == 1
    body = bg.split("async function communityFetch(path, body) {", 1)[1].split("\n}\n", 1)[0]
    assert "fetch(C.REPORT_ORIGIN + path" in body and 'credentials: "omit"' in body
    # Every caller checks the switch and the permission first.
    for fn in ("async function communityHistory", "async function communityPut", "async function runWatchCheck"):
        part = bg.split(fn, 1)[1].split("communityFetch(", 1)[0]
        assert "communityOn()" in part, fn

    assert "host_permissions" not in MANIFEST, "the shipped extension needs no standing host permissions"
    core = (EXT / "src" / "core.js").read_text(encoding="utf-8")
    origin = re.search(r'const REPORT_ORIGIN = "([^"]+)"', core).group(1)
    assert origin.startswith("https://")
    assert MANIFEST["optional_host_permissions"] == [origin + "/*"]
    assert set(MANIFEST["permissions"]) == {"storage", "activeTab", "scripting", "alarms", "notifications"}


def test_the_server_and_the_extension_agree_on_the_supported_sites():
    from app.community import SUPPORTED_SITES
    core = (EXT / "src" / "core.js").read_text(encoding="utf-8")
    block = core.split("const SUPPORTED_SITES = [", 1)[1].split("];", 1)[0]
    assert re.findall(r'"([a-z0-9.-]+)"', block) == SUPPORTED_SITES


def test_the_auto_run_list_matches_the_one_the_popup_describes():
    core = (EXT / "src" / "core.js").read_text(encoding="utf-8")
    block = core.split("const SUPPORTED_SITES = [", 1)[1].split("];", 1)[0]
    sites = re.findall(r'"([a-z0-9.-]+)"', block)
    matches = MANIFEST["content_scripts"][0]["matches"]
    assert [f"https://*.{s}/*" for s in sites] == [m for m in matches if "railway.app" not in m]
    assert not any("127.0.0.1" in m or "localhost" in m or "<all_urls>" in m for m in matches)


def test_manifest_meets_store_limits_and_versions_agree():
    assert len(MANIFEST["description"]) <= 132
    assert MANIFEST["name"] == "Ulterior by The Odyssey"
    core = (EXT / "src" / "core.js").read_text(encoding="utf-8")
    assert f'const VERSION = "{MANIFEST["version"]}"' in core


def test_no_extension_page_builds_html_from_strings():
    for f in SOURCES:
        code = _code(f)
        assert "innerHTML" not in code and "insertAdjacentHTML" not in code, f.name
        assert "eval(" not in code and "new Function" not in code, f.name


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_core_unit_tests_pass():
    r = subprocess.run(["node", str(ROOT / "tests" / "extension_core.test.js")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_packager_builds_chrome_edge_and_firefox(tmp_path):
    """One source folder, two packages. Firefox needs a background script,
    an add-on id and Mozilla's data-collection declaration."""
    import zipfile
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "package_extension.py")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    v = MANIFEST["version"]
    chrome = zipfile.ZipFile(ROOT / "dist" / f"ulterior-extension-{v}-chrome-edge.zip")
    ff = zipfile.ZipFile(ROOT / "dist" / f"ulterior-extension-{v}-firefox.zip")
    assert json.loads(chrome.read("manifest.json"))["background"] == {"service_worker": "background.js"}
    fm = json.loads(ff.read("manifest.json"))
    assert fm["background"] == {"scripts": ["src/core.js", "src/watch.js", "background.js"]}
    gecko = fm["browser_specific_settings"]["gecko"]
    assert gecko["id"] and gecko["data_collection_permissions"]["required"] == ["none"]
    for z in (chrome, ff):
        names = z.namelist()
        assert "src/platform.js" in names and "icons/icon128.png" in names
        assert "watchlist.html" in names and "src/chart.js" in names
        assert not any(n.endswith(".md") for n in names)
