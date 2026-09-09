#!/usr/bin/env bash
# One command to run the whole thing on a laptop.
#
# Written because "clone it and figure out the six setup steps" is where a
# demo dies -- at a pitch, on a borrowed machine, five minutes before you are
# on stage. Everything here is idempotent: run it twice and it does the right
# thing both times.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# An EXISTING, supported .venv wins over whatever the system python happens
# to be. The Windows version of this script got that order backwards: it
# exited on an unsupported system python even when a good .venv was already
# sitting there, having just told the user to create one by hand.
VPY="./.venv/bin/python"

if [ -x "$VPY" ]; then
  VENVVER=$("$VPY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  VENVMINOR=${VENVVER#*.}
  if [ "${VENVVER%%.*}" != "3" ] || [ "$VENVMINOR" -lt 10 ] || [ "$VENVMINOR" -gt 13 ]; then
    echo "The existing .venv was built with Python $VENVVER, which is not supported."
    echo "Supported: 3.10-3.13 (3.12 recommended)."
    echo "Run:  rm -rf .venv   then re-run this script."
    exit 1
  fi
  echo "==> Using existing .venv (Python $VENVVER)"
else
  # No .venv yet, so find a SUPPORTED interpreter by name. Asking for each
  # version explicitly means having 3.14 installed alongside 3.12 picks 3.12,
  # rather than whatever "python3" happens to point at.
  PY=""
  for CAND in python3.12 python3.13 python3.11 python3.10 python3 python; do
    if command -v "$CAND" >/dev/null 2>&1; then
      V=$("$CAND" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || continue
      MINOR=${V#*.}
      if [ "${V%%.*}" = "3" ] && [ "$MINOR" -ge 10 ] && [ "$MINOR" -le 13 ]; then
        PY="$CAND"; PYVER="$V"; break
      fi
    fi
  done

  if [ -z "$PY" ]; then
    echo "No supported Python found. This project needs Python 3.10-3.13 (3.12 recommended)."
    echo "Newer versions are not supported: some dependencies have no prebuilt wheels"
    echo "for them yet, so pip tries to compile C extensions and fails."
    exit 1
  fi

  echo "==> Creating virtualenv (.venv)  [Python $PYVER via $PY]"
  "$PY" -m venv .venv
fi

echo "==> Installing dependencies"
# pip's default socket timeout is 15 seconds, which is not enough for the
# Playwright wheel (~40 MB) on a slow or shared connection -- it fails with a
# ReadTimeoutError partway through and looks like a broken setup rather than a
# slow one. These two flags are the difference between "it does not work" and
# "it takes a few minutes", so they are the default rather than advice in a
# README that someone reads only after it has already failed.
PIP_NET_FLAGS="--timeout 120 --retries 10"
./.venv/bin/pip install -q $PIP_NET_FLAGS --upgrade pip
./.venv/bin/pip install -q $PIP_NET_FLAGS -r requirements.txt
# requirements-dev.txt too. Without it tests/test_report_language.py skips
# silently -- six tests that assert what the generated PDF actually SAYS --
# so the suite reports green while running less of itself than CI does.
./.venv/bin/pip install -q $PIP_NET_FLAGS -r requirements-dev.txt

echo "==> Installing the Chromium build Playwright expects"
# Playwright pins an exact browser revision per version. Installing the
# browser separately from the library is the single most common cause of
# "works on my machine" for this stack, so it is not left to the reader.
./.venv/bin/python -m playwright install chromium || {
  echo "    (browser download failed -- if you already have a matching"
  echo "     Chromium, set PLAYWRIGHT_BROWSERS_PATH and re-run)"
}

echo "==> Starting the API on http://127.0.0.1:${PORT}"
echo
echo "    Demo (start here) : http://127.0.0.1:${PORT}/demo/"
echo "    Dashboard         : http://127.0.0.1:${PORT}/dashboard/"
echo "    Coverage (JSON)   : http://127.0.0.1:${PORT}/coverage"
echo "    Dark storefront   : http://127.0.0.1:${PORT}/storefront/cart.html"
echo
echo "    No API key is needed in development. Ctrl-C to stop."
echo

# PUBLIC_BASE_URL is what the hosted demo adapters walk, so it must match the
# port the server actually bound -- otherwise the crawler fetches a port with
# nothing on it and every demo audit comes back empty.
# ALLOW_LOCAL_TARGETS lets the SSRF guard accept 127.0.0.1, so you can paste
# the bundled storefront into the dashboard's URL box and watch auto-discovery
# walk it. It is a LOCAL flag only -- app/config.py refuses to start in
# production with it set. This launcher is the local one, hence it is safe
# here and nowhere else.
PUBLIC_BASE_URL="http://127.0.0.1:${PORT}" \
  ALLOW_LOCAL_TARGETS=1 \
  exec ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
