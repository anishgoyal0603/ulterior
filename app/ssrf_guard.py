"""
SSRF (Server-Side Request Forgery) protection for self-serve audits.

WHY THIS EXISTS NOW, NOT LATER: making the product "simple to use" means
accepting an arbitrary URL from any visitor and having the SERVER's own
crawler fetch it. Without this module, a malicious input like
"http://169.254.169.254/latest/meta-data/" (the cloud metadata endpoint)
or "http://localhost:5432" (the app's own database port) would make the
crawler -- running inside your own infrastructure -- fetch internal
resources on the attacker's behalf. This is the exact mechanism flagged
as a documented gap in the earlier security audit ("if you ever let users
submit arbitrary URLs... validate against an allowlist and block internal
ranges"). Self-serve is the reason to finally close it, not a reason to
skip it.

Defence-in-depth, two layers:
  1. Scheme + syntax validation (http/https only, no credentials embedded).
  2. DNS resolution + IP-range check -- resolving the hostname and
     rejecting it if ANY resolved address is private/loopback/link-local.
     Checking the STRING alone is not enough: a hostname a user controls
     (e.g. "evil.example.com") can resolve to 127.0.0.1 or an internal IP
     (DNS rebinding) -- so this validates what the name actually resolves
     to, not just what it looks like.
"""

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    """Raised when a target URL fails SSRF safety checks."""


ALLOWED_SCHEMES = {"http", "https"}


def _loopback_allowed() -> bool:
    """True only when a developer has explicitly opted in, outside production.

    The bundled storefront is served on 127.0.0.1, so without this the first
    thing anyone tries -- pasting their own local demo shop into the dashboard
    -- is refused by the very guard that protects the deployed service. That
    is a confusing first impression, but it is NOT a reason to soften the
    guard, so this is narrowed three ways:

      * It is opt-in (ALLOW_LOCAL_TARGETS), so it is off unless someone asked.
      * It is refused outright in production -- config.validate() will not let
        the app start with it set, so it cannot be turned on by an env var on
        a deployed box.
      * It permits LOOPBACK ONLY. Private LAN ranges stay blocked, and so does
        link-local, because 169.254.169.254 is the cloud metadata endpoint and
        a developer's machine is quite often a cloud VM. Reaching your own
        laptop is the need; reaching the rest of the network is not.
    """
    from . import config
    return config.ALLOW_LOCAL_TARGETS and not config.IS_PRODUCTION


def _is_private_or_reserved(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable -- fail closed, not open
    if ip.is_loopback and _loopback_allowed():
        return False
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_target_url(url: str) -> str:
    """
    Raises UnsafeURLError with a clear, non-leaky reason if the URL is
    unsafe to crawl. Returns the URL unchanged if it passes.
    """
    if not url or len(url) > 2048:
        raise UnsafeURLError("URL is empty or unreasonably long.")

    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Only http/https URLs are allowed (got scheme: {parsed.scheme!r}).")

    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL has no hostname.")

    # Reject obvious loopback/internal hostnames outright before even
    # attempting DNS resolution.
    lowered = hostname.lower()
    if lowered in ("localhost", "0.0.0.0") or lowered.endswith(".local"):
        if not (lowered == "localhost" and _loopback_allowed()):
            raise UnsafeURLError("Local/internal hostnames are not allowed.")

    # Resolve and check EVERY returned address -- a hostname can have
    # multiple A/AAAA records, and DNS rebinding relies on checking the
    # string instead of what it actually resolves to.
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Could not resolve hostname: {hostname}") from e

    resolved_ips = {info[4][0] for info in addr_infos}
    if not resolved_ips:
        raise UnsafeURLError(f"Hostname resolved to no addresses: {hostname}")

    for ip_str in resolved_ips:
        if _is_private_or_reserved(ip_str):
            raise UnsafeURLError(
                f"Hostname resolves to a private/internal address ({ip_str}) -- refusing to crawl."
            )

    return url
