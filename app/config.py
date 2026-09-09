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
API_KEYS = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]
_raw_admin = os.environ.get("ADMIN_API_KEYS", "").strip()
ADMIN_API_KEYS = (
    [k.strip() for k in _raw_admin.split(",") if k.strip()] if _raw_admin else list(API_KEYS)
)

# Evidence retention. Audits older than this are purged by the retention job
# (see app/retention.py). 0 disables automatic purging.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
MAX_STORED_AUDITS = int(os.environ.get("MAX_STORED_AUDITS", "500"))

# Rate limits (requests per window, per client IP)
RATE_LIMIT_AUDIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_AUDIT_PER_MINUTE", "5"))
RATE_LIMIT_DELETE_PER_HOUR = int(os.environ.get("RATE_LIMIT_DELETE_PER_HOUR", "3"))
RATE_LIMIT_DEFAULT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_DEFAULT_PER_MINUTE", "60"))

# Trust X-Forwarded-For only when explicitly enabled. Behind a reverse proxy
# this must be on, or every client looks like the proxy and rate limiting
# collapses to a single shared bucket. Off by default because a client can
# spoof the header when the app is directly internet-facing.
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "false").strip().lower() in ("1", "true", "yes")


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
            unknown_admin = [k for k in ADMIN_API_KEYS if k not in API_KEYS]
            if unknown_admin:
                problems.append(
                    "ADMIN_API_KEYS contains key(s) not present in API_KEYS."
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
