"""
API key authentication.

WHY THIS IS THE ROOT FIX: an attacker-mindset pass on this app found that
every endpoint answered without any credential. That single gap is what made
audit enumeration, audit deletion, and mass data destruction possible -- they
were not separate bugs, they were one missing control. Ownership checks
(the classic IDOR fix) are meaningless here because there are no user
accounts to own anything; the correct control at this stage is "can the
caller prove it is authorised to use this service at all."

Design:
  - Keys are supplied via the API_KEYS env var (comma-separated) so they are
    rotatable without a code change and never live in the repo.
  - Comparison uses secrets.compare_digest to avoid a timing side channel
    that would let an attacker recover a key byte-by-byte.
  - PRODUCTION REFUSES TO START without at least one key (see config.validate),
    so it is impossible to deploy this open by accident.
  - In development, if no keys are configured, auth is disabled so the test
    suite and local work stay frictionless. That asymmetry is deliberate and
    is the same fail-safe pattern used elsewhere in config.py.
  - Read vs write separation: destructive routes additionally require the key
    to be listed in ADMIN_API_KEYS, so a key handed to a dashboard cannot
    wipe the database.
"""

import secrets
from typing import Optional

from fastapi import Header, HTTPException, Request

from . import config


def _key_matches(candidate: str, allowed: list) -> bool:
    """Constant-time comparison against every allowed key."""
    # compare_digest raises TypeError on a str containing any character above
    # U+007F. Header values reach us latin-1 decoded, so `X-API-Key: café`
    # produced a 500 with a full traceback in the log on every attempt -- an
    # unauthenticated way to flood the logs, and it hid real auth failures
    # behind server errors. A key that cannot be one of ours is simply wrong.
    try:
        candidate_bytes = candidate.encode("utf-8")
    except (UnicodeEncodeError, AttributeError):
        return False

    result = False
    for key in allowed:
        # Do NOT short-circuit: compare against all keys so total time does
        # not reveal how many comparisons ran before a match.
        if secrets.compare_digest(candidate_bytes, key.encode("utf-8")):
            result = True
    return result


def _extract_key(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> str:
    """FastAPI dependency for read/write endpoints."""
    if not config.API_KEYS:
        if config.IS_PRODUCTION:
            # Unreachable: config.validate() refuses to start. Defence in depth.
            raise HTTPException(status_code=503, detail="Service misconfigured.")
        return "auth-disabled-development"

    candidate = _extract_key(authorization, x_api_key)
    if not candidate or not _key_matches(candidate, config.API_KEYS):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return candidate


def require_admin_key(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> str:
    """
    Stricter dependency for DESTRUCTIVE endpoints (delete / purge).

    Privilege separation is enforced SERVER-SIDE here, not by hiding a button
    in the dashboard. A valid read key presented to a delete route is
    rejected with 403 -- it authenticates fine, it just isn't authorised.
    """
    if not config.API_KEYS:
        if config.IS_PRODUCTION:
            raise HTTPException(status_code=503, detail="Service misconfigured.")
        return "auth-disabled-development"

    candidate = _extract_key(authorization, x_api_key)
    if not candidate or not _key_matches(candidate, config.API_KEYS):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _key_matches(candidate, config.ADMIN_API_KEYS):
        raise HTTPException(
            status_code=403,
            detail="This key is not authorised for destructive operations.",
        )
    return candidate
