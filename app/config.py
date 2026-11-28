"""
Centralised configuration with fail-fast validation.

Design rule: the app must REFUSE TO START in production if a critical
variable is missing, rather than silently falling back to a development
default. A silent fallback is how an app ends up running a public
deployment against a local SQLite file, or with CORS wide open, and nobody
notices until there's an incident.

Development stays frictionless: with APP_ENV unset (the default), every
setting has a working local default and nothing is required.
"""

import os
import re
import sys


class ConfigError(RuntimeError):
    """Raised at import time when production config is invalid."""


# "development" | "production". Anything not exactly "production" is treated
# as development, so a typo in APP_ENV fails SAFE (dev defaults, localhost
# CORS) rather than silently disabling protections.
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# DEBUG defaults to OFF. Only an explicit opt-in turns it on, and it is
# force-disabled in production regardless of what the variable says.
DEBUG = os.environ.get("DEBUG", "false").strip().lower() in ("1", "true", "yes")
if IS_PRODUCTION:
    DEBUG = False

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./darkpattern_auditor.db")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

_raw_origins = os.environ.get(
    "CORS_ALLOW_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
)
CORS_ALLOW_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# Where this instance is reachable from a browser. Used by the hosted demo
# adapters so the auditor walks the bundled storefront over REAL HTTP instead
# of file:// — the same code path a live site takes, which is the whole point
# of a demo. On Railway, set this to the generated domain; locally the default
# is correct.
#
# This is server-controlled configuration, not user input, which is why the
# hosted demo is a REGISTERED ADAPTER rather than a self-serve `target_urls`
# audit. Self-serve URLs go through app/ssrf_guard.py and a loopback address
# would be rejected outright — correctly, since that is exactly the request an
# attacker would use to reach the server's own internal network. An operator
# pointing the tool at a storefront the operator themselves deployed is a
# different thing entirely, and the registry is where that distinction lives.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

# Whether the unauthenticated public demo endpoints are exposed.
#
# The demo has to be clickable by someone with no API key -- that is the
# entire point of putting a link in front of a judge or a pilot user. But an
# unauthenticated endpoint that launches a headless browser is a resource
# amplifier, so the exposure is bounded on every axis that matters:
#   * only the two SERVER-DEFINED hosted demo adapters can be run; no
#     caller-supplied URL reaches this path at all
#   * the existing audit rate limit (5/min per IP) applies unchanged
#   * it can read back only jobs it created, and only demo jobs
#   * one environment variable turns it off entirely
# Default on, because a demo nobody can run is not a demo.
PUBLIC_DEMO_ENABLED = os.environ.get("PUBLIC_DEMO_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# The only adapters the public demo endpoint will run. Not configurable by a
# request, on purpose: the allowlist IS the security boundary.
PUBLIC_DEMO_ADAPTERS = ("hosted_dark_demo", "hosted_clean_demo")

# Local-development only: let the SSRF guard accept loopback targets, so the
# bundled storefront on 127.0.0.1 can be audited through the normal self-serve
# path instead of being refused by the guard that protects the deployed
# service. Default OFF, and validate() below REFUSES TO START if it is set in
# production. See app/ssrf_guard.py for why this permits loopback only.
ALLOW_LOCAL_TARGETS = os.environ.get("ALLOW_LOCAL_TARGETS", "").strip().lower() in ("1", "true", "yes")

# How often the retention sweep runs while the server is UP. purge_expired()
# used to be called only in the startup hook, so RETENTION_DAYS and
# MAX_STORED_AUDITS bounded nothing on a server that stays running -- which is
# the only kind of server a retention policy is for. Hourly: the policy is
# expressed in days, so anything finer is wasted work.
RETENTION_SWEEP_SECONDS = int(os.environ.get("RETENTION_SWEEP_SECONDS", "3600"))

# API keys. Comma-separated. Production refuses to start without at least one.
# ADMIN_API_KEYS is a subset authorised for destructive routes (delete/purge);
# if unset it defaults to API_KEYS, but a real deployment should separate them.
#
# ORGANISATIONS (2.4): an entry may be written "org:key", e.g.
#   API_KEYS=team:KEY1,parkhya:KEY2
# Each organisation sees only its own audits, reports and evidence. A plain
# "key" belongs to the default organisation, DEFAULT_ORG ("team"), which also
# owns every audit made before 2.4 and the public demo. Admin keys see all.
DEFAULT_ORG = "team"
_ORG_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def _parse_keys(raw: str):
    keys, orgs, bad = [], {}, []
    for entry in (e.strip() for e in raw.split(",")):
        if not entry:
            continue
        org, sep, key = entry.partition(":")
        if not sep:
            org, key = DEFAULT_ORG, entry
        org, key = org.strip().lower(), key.strip()
        if not key or not _ORG_NAME.match(org):
            bad.append(org or "(empty)")
            continue
        if key in orgs:
            # The same key listed for two organisations silently belonged to
            # whichever came last -- one customer could read another's audits.
            if orgs[key] != org:
                bad.append(f"{org} (that key is already {orgs[key]}'s)")
            continue
        keys.append(key)
        orgs[key] = org
    return keys, orgs, bad


API_KEYS, KEY_ORGS, _BAD_KEY_ENTRIES = _parse_keys(os.environ.get("API_KEYS", ""))
_raw_admin = os.environ.get("ADMIN_API_KEYS", "").strip()
# Unset: the default team's keys are admin (the behaviour before 2.4), and an
# organisation's own key never is -- otherwise every customer key could read
# every other customer's audits.
ADMIN_API_KEYS = (
    _parse_keys(_raw_admin)[0] if _raw_admin
    else [k for k in API_KEYS if KEY_ORGS.get(k) == DEFAULT_ORG]
)

# Route every crawling browser through the local egress proxy, which checks
# the address it actually connects to (defeats DNS rebinding). "off" only to
# diagnose a site that will not load.
EGRESS_PROXY = os.environ.get("EGRESS_PROXY", "on").strip().lower() not in ("0", "off", "false", "no")

# Durable audit jobs (app/tasks.py).
AUDIT_WORKERS = max(1, int(os.environ.get("AUDIT_WORKERS", "4")))
# "false" = this process only accepts audits; run `python -m app.worker` separately.
RUN_AUDIT_WORKERS = os.environ.get("RUN_AUDIT_WORKERS", "true").strip().lower() in ("1", "true", "yes")
JOB_HEARTBEAT_SECONDS = max(2, int(os.environ.get("JOB_HEARTBEAT_SECONDS", "15")))
JOB_STALE_SECONDS = max(10, int(os.environ.get("JOB_STALE_SECONDS", "180")))
JOB_MAX_ATTEMPTS = max(1, int(os.environ.get("JOB_MAX_ATTEMPTS", "2")))
JOB_SWEEP_SECONDS = max(2, int(os.environ.get("JOB_SWEEP_SECONDS", "30")))
# Queued + running audits one organisation may have at once.
MAX_ACTIVE_AUDITS_PER_ORG = max(1, int(os.environ.get("MAX_ACTIVE_AUDITS_PER_ORG", "3")))

# Evidence retention. Audits older than this are purged by the retention job
# (see app/retention.py). 0 disables automatic purging.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
MAX_STORED_AUDITS = int(os.environ.get("MAX_STORED_AUDITS", "500"))
# Public demo runs are kept separately (organisation "public-demo") with their
# own cap, so demo traffic can never evict a customer's audits.
DEMO_ORG = "public-demo"
MAX_STORED_DEMO_AUDITS = int(os.environ.get("MAX_STORED_DEMO_AUDITS", "50"))

# Rate limits (requests per window, per client IP)
RATE_LIMIT_AUDIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_AUDIT_PER_MINUTE", "5"))
RATE_LIMIT_DELETE_PER_HOUR = int(os.environ.get("RATE_LIMIT_DELETE_PER_HOUR", "3"))
RATE_LIMIT_DEFAULT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_DEFAULT_PER_MINUTE", "60"))
# Status polling and static page furniture are cheap, and the demo page does a
# lot of both: it polls every 900ms while an audit runs, and every stylesheet,
# script and storefront page inside the iframe is a request too. Sharing the
# 60/minute default with them made the demo rate-limit itself after two runs.
# These budgets are still bounded -- they cap memory and abuse -- but they are
# sized for what the page genuinely does rather than for launching browsers.
RATE_LIMIT_POLL_PER_MINUTE = int(os.environ.get("RATE_LIMIT_POLL_PER_MINUTE", "240"))
# Cancel / review an existing audit (cheap, and an operator does many in a row).
RATE_LIMIT_AUDIT_ACTIONS_PER_MINUTE = int(os.environ.get("RATE_LIMIT_AUDIT_ACTIONS_PER_MINUTE", "60"))
# PDF reports and evidence verification: real CPU and disk work per call.
RATE_LIMIT_HEAVY_PER_MINUTE = int(os.environ.get("RATE_LIMIT_HEAVY_PER_MINUTE", "30"))
RATE_LIMIT_STATIC_PER_MINUTE = int(os.environ.get("RATE_LIMIT_STATIC_PER_MINUTE", "600"))
# Reports sent from the browser extension: a person pressing Send, so a few a
# minute is generous, and the endpoint needs no key, so it must stay small.
RATE_LIMIT_REPORTS_PER_MINUTE = int(os.environ.get("RATE_LIMIT_REPORTS_PER_MINUTE", "10"))
MAX_STORED_EXTENSION_REPORTS = int(os.environ.get("MAX_STORED_EXTENSION_REPORTS", "5000"))
# Community prices: a price history is only useful over months, so these are
# kept longer than audits -- but bounded by age and by count all the same.
RATE_LIMIT_PRICES_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PRICES_PER_MINUTE", "120"))
COMMUNITY_RETENTION_DAYS = int(os.environ.get("COMMUNITY_RETENTION_DAYS", "400"))
MAX_PRICE_OBSERVATIONS = int(os.environ.get("MAX_PRICE_OBSERVATIONS", "1000000"))

# Trust X-Forwarded-For only when explicitly enabled. Behind a reverse proxy
# this must be on, or every client looks like the proxy and rate limiting
# collapses to a single shared bucket. Off by default because a client can
# spoof the header when the app is directly internet-facing.
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "false").strip().lower() in ("1", "true", "yes")
# How many proxies sit in front of the app and append to X-Forwarded-For.
# Railway, Render, Fly and a single nginx are each one hop.
TRUSTED_PROXY_HOPS = max(1, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))


def validate() -> None:
    """Validate configuration. Raises ConfigError in production if unsafe."""
    problems = []

    if IS_PRODUCTION:
        # 1. Database must be explicitly configured -- never the SQLite fallback.
        if not os.environ.get("DATABASE_URL"):
            problems.append(
                "DATABASE_URL is not set. Production must not run on the local "
                "SQLite fallback."
            )
        elif DATABASE_URL.startswith("sqlite"):
            problems.append(
                "DATABASE_URL points at SQLite. Use PostgreSQL in production."
            )
        else:
            # 2. Database connection must use TLS.
            lowered = DATABASE_URL.lower()
            if lowered.startswith("postgres") and "sslmode=" not in lowered:
                problems.append(
                    "DATABASE_URL has no sslmode parameter. Append "
                    "'?sslmode=require' (or 'verify-full') so the database "
                    "connection is encrypted in transit."
                )

        # 3. Authentication must be configured -- this is what stops an
        #    unauthenticated caller enumerating and deleting every audit.
        if not API_KEYS:
            problems.append(
                "API_KEYS is not set. Production must not run unauthenticated -- "
                "any caller could read and delete all audit data. Generate one "
                "with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        else:
            weak = [k for k in API_KEYS if len(k) < 24]
            if weak:
                problems.append(
                    f"{len(weak)} API key(s) are shorter than 24 characters and are "
                    "brute-forceable. Use secrets.token_urlsafe(32)."
                )
            if _BAD_KEY_ENTRIES:
                problems.append(
                    "API_KEYS has invalid entries "
                    f"({', '.join(_BAD_KEY_ENTRIES)}). Use org:key with a lowercase "
                    "name of letters, digits, '-' or '_', and give each organisation "
                    "its own key."
                )
            if not _raw_admin:
                problems.append(
                    "ADMIN_API_KEYS is not set. Set it explicitly to the key(s) allowed to "
                    "delete audits and see every organisation -- never the dashboard's key."
                )
            elif not ADMIN_API_KEYS or _parse_keys(_raw_admin)[2]:
                # Set, but nothing usable came out of it (a bad "org:" prefix,
                # stray commas): production must not boot with zero admin keys.
                problems.append(
                    "ADMIN_API_KEYS is set but contains no valid key"
                    + (f" (bad entries: {', '.join(_parse_keys(_raw_admin)[2])})"
                       if _parse_keys(_raw_admin)[2] else "") + "."
                )
            unknown_admin = [k for k in ADMIN_API_KEYS if k not in API_KEYS]
            if unknown_admin:
                problems.append(
                    "ADMIN_API_KEYS contains key(s) not present in API_KEYS."
                )

        # 3b. The public demo walks the storefront at PUBLIC_BASE_URL. Left at
        #     its default (127.0.0.1) on a deployed box, every demo audit
        #     fails, because production never allows loopback crawling.
        if PUBLIC_DEMO_ENABLED:
            from urllib.parse import urlparse as _up
            base = _up(PUBLIC_BASE_URL)
            if base.scheme != "https" or (base.hostname or "") in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
                problems.append(
                    "PUBLIC_BASE_URL must be this deployment's public https address "
                    "(e.g. https://your-app.up.railway.app) while PUBLIC_DEMO_ENABLED is on; "
                    f"it is {PUBLIC_BASE_URL!r}."
                )

        # 4. The loopback escape hatch is a local-development convenience and
        #    nothing else. On a deployed box it would let a caller point the
        #    server's own crawler at services bound to localhost -- the
        #    database, an admin port -- which is the precise attack the SSRF
        #    guard exists to prevent. Refusing to START is deliberate: silently
        #    ignoring it would leave someone believing it was in effect.
        if ALLOW_LOCAL_TARGETS:
            problems.append(
                "ALLOW_LOCAL_TARGETS is set. It is a local-development flag for "
                "auditing the bundled storefront on 127.0.0.1, and must never be "
                "set in production -- it would let the crawler reach services "
                "bound to localhost. Unset it."
            )

        # 4. CORS must not be a wildcard.
        if "*" in CORS_ALLOW_ORIGINS:
            problems.append(
                "CORS_ALLOW_ORIGINS contains '*'. Restrict it to your frontend "
                "origin(s) -- this API is not a public API."
            )
        if not CORS_ALLOW_ORIGINS:
            problems.append("CORS_ALLOW_ORIGINS is empty.")

        # 4. Origins must be HTTPS in production.
        insecure = [o for o in CORS_ALLOW_ORIGINS if o.startswith("http://")]
        if insecure:
            problems.append(
                f"CORS_ALLOW_ORIGINS contains non-HTTPS origin(s): {insecure}."
            )

    if problems:
        raise ConfigError(
            "Refusing to start -- invalid production configuration:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\n\nSee .env.example for the required variables."
        )


def startup_report() -> str:
    """Non-secret summary for the startup log. Never prints credential values."""
    db_kind = DATABASE_URL.split("://", 1)[0] if "://" in DATABASE_URL else "unknown"
    return (
        f"env={APP_ENV} debug={DEBUG} db={db_kind} "
        f"llm_layer3={'enabled' if ANTHROPIC_API_KEY else 'offline-stub'} "
        f"cors_origins={len(CORS_ALLOW_ORIGINS)} "
        f"api_keys={len(API_KEYS)} admin_keys={len(ADMIN_API_KEYS)} "
        f"retention_days={RETENTION_DAYS}"
    )
