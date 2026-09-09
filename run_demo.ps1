# Windows launcher -- the PowerShell equivalent of run_demo.sh.
#
# Run it with:
#     powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
#
# -ExecutionPolicy Bypass is required, not superstition: Windows marks files
# that came out of a downloaded zip, and the default policy refuses them.
#
# ------------------------------------------------------------------------
# THIS FILE IS DELIBERATELY PURE ASCII. Do not add em-dashes, arrows or
# smart quotes.
#
# Windows PowerShell 5.1 -- still the default on most Windows installs and in
# VS Code -- reads .ps1 files as ANSI unless they carry a UTF-8 BOM. An
# earlier version of this script had em-dashes in its Write-Host strings.
# They arrived as mojibake, and worse, the corruption confused the parser
# mid-string: PowerShell printed the literal text `Write-Host "` and the
# comment lines that followed it, as output.
# ------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$Port = if ($env:PORT) { $env:PORT } else { "8000" }

# Supported Python minor versions, in the order we would rather have them.
# Below 3.10 the code uses syntax that will not parse; above 3.13 several
# dependencies have no prebuilt wheels yet, so pip falls back to compiling C
# extensions and fails without the Microsoft C++ Build Tools.
$PreferredMinors = @(12, 13, 11, 10)

function Test-SupportedMinor {
    param([int]$Minor)
    return ($Minor -ge 10 -and $Minor -le 13)
}

function Get-PyMinor {
    param([string]$Exe, [string[]]$ExeArgs)
    try {
        $out = & $Exe @ExeArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return $out.Trim()
    } catch {
        return $null
    }
}

# PowerShell's $ErrorActionPreference does NOT stop the script when an
# external program exits non-zero -- it only governs PowerShell's own cmdlets.
# Without this check, a failed `pip install` was followed by "installing
# Chromium" and "starting the API", printing a confident startup banner for a
# server that was never going to run.
function Invoke-Step {
    param([string]$Description, [scriptblock]$Command)
    Write-Host "==> $Description" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "FAILED: $Description (exit code $LASTEXITCODE)" -ForegroundColor Red
        Write-Host "Nothing further was run. The error above is the real one." -ForegroundColor Red
        exit 1
    }
}

# =========================================================================
# Pick the interpreter.
#
# ORDER MATTERS, and an earlier version of this script got it backwards: it
# checked the SYSTEM python first and exited on an unsupported version, even
# when a perfectly good .venv built with a supported one already existed.
# Its own error message told the user to run `py -3.12 -m venv .venv` and
# then refused to use the result.
#
# An existing, supported .venv is the answer whenever there is one. The
# system interpreter only matters when there is no .venv to use yet.
# =========================================================================

$VPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (Test-Path $VPy) {
    $VenvVer = Get-PyMinor -Exe $VPy -ExeArgs @()
    if (-not $VenvVer) {
        Write-Host ""
        Write-Host "The existing .venv is broken (its python.exe would not run)." -ForegroundColor Red
        Write-Host "Run:  Remove-Item -Recurse -Force .venv"
        Write-Host "then re-run this script."
        exit 1
    }
    $VenvMinor = [int]$VenvVer.Split('.')[1]
    if (-not (Test-SupportedMinor $VenvMinor)) {
        Write-Host ""
        Write-Host "The existing .venv was built with Python $VenvVer, which is not supported." -ForegroundColor Red
        Write-Host "Supported: Python 3.10 to 3.13. Recommended: 3.12"
        Write-Host ""
        Write-Host "Run:  Remove-Item -Recurse -Force .venv"
        Write-Host "then re-run this script (it will find a supported Python by itself)."
        exit 1
    }
    Write-Host "==> Using existing .venv (Python $VenvVer)" -ForegroundColor Cyan

} else {
    # No .venv yet, so we do need a usable system interpreter. Ask the py
    # launcher for each supported version by name before falling back to a
    # bare `python`, so that having 3.14 installed alongside 3.12 picks 3.12
    # rather than whatever happens to be newest.
    $PyExe = $null
    $PyArgs = @()
    $FoundVer = $null

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($minor in $PreferredMinors) {
            $ver = Get-PyMinor -Exe "py" -ExeArgs @("-3.$minor")
            if ($ver) { $PyExe = "py"; $PyArgs = @("-3.$minor"); $FoundVer = $ver; break }
        }
    }

    if (-not $PyExe) {
        foreach ($candidate in @("python", "python3")) {
            if (Get-Command $candidate -ErrorAction SilentlyContinue) {
                $ver = Get-PyMinor -Exe $candidate -ExeArgs @()
                if ($ver -and (Test-SupportedMinor ([int]$ver.Split('.')[1]))) {
                    $PyExe = $candidate; $FoundVer = $ver; break
                }
            }
        }
    }

    if (-not $PyExe) {
        Write-Host ""
        Write-Host "No supported Python was found." -ForegroundColor Red
        Write-Host "This project needs Python 3.10 to 3.13. Recommended: 3.12"
        Write-Host ""
        Write-Host "Python 3.14 is too new: some dependencies have no prebuilt wheels"
        Write-Host "for it yet, so pip tries to compile them from C source and fails"
        Write-Host "without the Microsoft C++ Build Tools."
        Write-Host ""
        Write-Host "Install Python 3.12.10 (the last 3.12 with a Windows installer):"
        Write-Host "  https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        Write-Host "Tick 'Add python.exe to PATH', reopen this terminal, and re-run."
        exit 1
    }

    Write-Host "==> Found Python $FoundVer ($PyExe $PyArgs)" -ForegroundColor Cyan
    Invoke-Step "Creating virtualenv (.venv)" { & $PyExe @PyArgs -m venv .venv }

    if (-not (Test-Path $VPy)) {
        Write-Host "The virtualenv was not created. Delete .venv and try again." -ForegroundColor Red
        exit 1
    }
}

# pip's default socket timeout is 15 seconds, which is not enough for the
# Playwright wheel (~40 MB) on a slow or shared connection -- it fails with a
# ReadTimeoutError partway through and looks like a broken setup rather than a
# slow one. These flags are the difference between "it does not work" and "it
# takes a few minutes", so they are the default rather than advice in a README
# that someone reads only after it has already failed.
$PipNet = @("--timeout", "120", "--retries", "10")

Invoke-Step "Upgrading pip" { & $VPy -m pip install --quiet @PipNet --upgrade pip }
Invoke-Step "Installing dependencies (a few minutes the first time)" { & $VPy -m pip install --quiet @PipNet -r requirements.txt }
# requirements-dev.txt too, even though this script only runs the demo.
#
# Without it, tests/test_report_language.py -- six tests that read the
# generated PDF back and assert on its wording -- SKIPS silently, because it
# needs pdfminer to read the PDF. CI installs it, so CI ran those six and a
# collaborator's laptop did not: the suite reported all green while quietly
# running less of itself. The report's wording is the most defamation-
# sensitive text this project produces, so those are the last six tests that
# should vanish without saying so. It is a small pure-Python package.
Invoke-Step "Installing the test-only dependencies" { & $VPy -m pip install --quiet @PipNet -r requirements-dev.txt }
Invoke-Step "Installing the Chromium build Playwright expects" { & $VPy -m playwright install chromium }

Write-Host ""
Write-Host "==> Starting the API on http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host ""
Write-Host "    Demo (start here) : http://127.0.0.1:$Port/demo/"
Write-Host "    Dashboard         : http://127.0.0.1:$Port/dashboard/"
Write-Host "    Coverage (JSON)   : http://127.0.0.1:$Port/coverage"
Write-Host "    Dark storefront   : http://127.0.0.1:$Port/storefront/cart.html"
Write-Host ""
Write-Host "    No API key is needed in development."
Write-Host "    This window will look frozen. That is the server running."
Write-Host "    Press Ctrl-C to stop it."
Write-Host ""

# PUBLIC_BASE_URL is what the hosted demo adapters walk, so it must match the
# port the server actually bound. Otherwise the crawler fetches a port with
# nothing on it and every demo audit comes back empty.
$env:PUBLIC_BASE_URL = "http://127.0.0.1:$Port"

# ALLOW_LOCAL_TARGETS lets the SSRF guard accept 127.0.0.1, so you can paste
# the bundled storefront into the dashboard's URL box and watch auto-discovery
# walk it. It is a LOCAL flag only -- app/config.py refuses to start in
# production with it set. This launcher is the local one, hence it is safe
# here and nowhere else.
$env:ALLOW_LOCAL_TARGETS = "1"

& $VPy -m uvicorn app.main:app --host 127.0.0.1 --port $Port
