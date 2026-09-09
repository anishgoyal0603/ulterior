"""
Security middleware: response headers, request correlation IDs, and
in-process rate limiting.

Rate limiting note: this uses an in-memory sliding window, which is correct
for a single-process deployment and for the demo. It does NOT share state
across multiple workers or machines -- with N uvicorn workers, the effective
limit is N x the configured value. For a multi-instance deployment, back
this with Redis (the REDIS_URL is already configured); the `check()`
interface is written so that swap touches only this file.
"""

import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from . import config


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# The longest window any route enforces is an hour; a bucket untouched for
# longer than that can never affect a decision, so it is safe to forget.
_BUCKET_TTL_SECONDS = 3700
_EVICT_EVERY_SECONDS = 60


class SlidingWindowLimiter:
    """A per-key sliding window.

    `clock` is injectable so the expiry behaviour can be tested by advancing
    time deliberately instead of by sleeping. That is not a convenience: the
    old test asserted that a hit falls out of a zero-width window, which is
    only observable if the clock ticks between two back-to-back calls. It does
    on Linux, and it does NOT on Windows, where `time.monotonic()` advances in
    steps of about 15.6 ms and returns the identical float for both calls. The
    test passed here and failed on the user's laptop -- a real defect in the
    test, dressed up as a bug in the code.
    """

    def __init__(self, clock=time.monotonic):
        self._hits = defaultdict(deque)
        self._lock = Lock()
        self._last_evict = 0.0
        self._clock = clock

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if the request is allowed, False if rate limited."""
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            self._evict_idle(now)
            bucket = self._hits[key]
            # `<=`, not `<`. A hit sitting exactly on the cutoff is outside the
            # window by definition -- and with a coarse clock, "exactly on the
            # cutoff" is where a zero-width window puts every hit, so the
            # boundary decides the behaviour rather than being a rounding
            # detail. At a real 60-second window the two differ only for a hit
            # aged 60.000000s, which no caller can distinguish.
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def _evict_idle(self, now: float) -> None:
        """Drop buckets whose newest hit is older than any window we enforce.

        Called from check() under the lock. Without it the dict only ever
        grows: entries are emptied of timestamps but never removed, so a
        long-running server accumulates one permanent entry per client IP
        until it is OOM-killed.
        """
        if now - self._last_evict < _EVICT_EVERY_SECONDS:
            return
        self._last_evict = now
        stale = [k for k, v in self._hits.items()
                 if not v or now - v[-1] > _BUCKET_TTL_SECONDS]
        for k in stale:
            del self._hits[k]

    def reset(self):
        with self._lock:
            self._hits.clear()
            self._last_evict = 0.0


limiter = SlidingWindowLimiter()


def _client_ip(request) -> str:
    """
    Resolve the client IP. X-Forwarded-For is honoured ONLY when
    TRUST_PROXY_HEADERS is enabled -- otherwise any client could spoof the
    header and trivially bypass rate limiting by rotating a fake value.
    """
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Per-route limits. There are no auth endpoints in this app (no login,
# signup, password reset, or OTP exists), so the limits are applied to the
# endpoints that actually carry abuse risk: the crawler trigger, which is
# resource-intensive, and the destructive delete routes.
_ROUTE_LIMITS = [
    # (method, path_prefix, limit, window_seconds, bucket)
    #
    # `bucket` is what the per-IP counter is keyed on. It is NOT the request
    # path: keying on the path meant DELETE /audits/1 and DELETE /audits/2
    # counted in different buckets, so "3 destructive operations per hour"
    # never fired for the route it was written for -- eleven deletes in a row
    # all returned 204. One bucket per ROUTE is what the limit means.
    ("POST", "/audits", lambda: config.RATE_LIMIT_AUDIT_PER_MINUTE, 60, "POST:/audits"),
    ("DELETE", "/audits", lambda: config.RATE_LIMIT_DELETE_PER_HOUR, 3600, "DELETE:/audits"),
    # The public demo starts a headless browser for anyone, with no key. It
    # was matched by neither prefix above, so it fell through to the default
    # 60/minute while config.py claimed the 5/minute audit limit applied to
    # it. Sixty unauthenticated browser launches a minute is a denial of
    # service against the machine running the demo.
    ("POST", "/demo-audit", lambda: config.RATE_LIMIT_AUDIT_PER_MINUTE, 60, "POST:/demo-audit"),
]


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        ip = _client_ip(request)
        path = request.url.path
        method = request.method

        limit = None
        window = 60
        bucket = None
        for m, prefix, limit_fn, win, name in _ROUTE_LIMITS:
            if method == m and path.startswith(prefix):
                limit, window, bucket = limit_fn(), win, name
                break
        if limit is None:
            # Unmatched paths share ONE default bucket per IP rather than one
            # per distinct path. Keying on the path let anyone mint unlimited
            # dictionary entries by requesting /x/<random> -- the limiter runs
            # before routing, so 404s counted too -- and nothing ever removed
            # them. That is an unbounded memory leak reachable by a crawler.
            limit, window, bucket = config.RATE_LIMIT_DEFAULT_PER_MINUTE, 60, "default"

        key = f"{ip}:{bucket}"
        if not limiter.check(key, limit, window):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please retry later.",
                    "correlation_id": getattr(request.state, "correlation_id", None),
                },
                headers={"Retry-After": str(window)},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Correlation ID
# ---------------------------------------------------------------------------

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Assigns every request a correlation id, echoed in the response header and
    in any error body. This is what makes sanitized error responses usable:
    the client reports the id, and an operator greps the server log for it.
    """
    async def dispatch(self, request, call_next):
        cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex[:16]
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers

        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        # The demo page frames the storefront it is auditing, so HTML must be
        # frameable BY THIS ORIGIN. SAMEORIGIN keeps the clickjacking
        # protection that matters -- no external site can frame this app --
        # while allowing the one frame the product itself needs. JSON stays at
        # DENY: an API response has no reason to be framed by anything, ever.
        content_type_early = (h.get("content-type") or "").lower()
        h["X-Frame-Options"] = "SAMEORIGIN" if content_type_early.startswith("text/html") else "DENY"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # HSTS only over HTTPS. Sending it over plain HTTP is meaningless and,
        # on a shared hostname, can lock a developer out of local http://.
        if config.IS_PRODUCTION:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # CSP is chosen by what the response actually IS, not by its path.
        #
        # The API returns JSON and needs nothing at all, so it gets the
        # strictest useful policy: deny everything. That was the only policy
        # here until the dashboard began being served from this same origin
        # (see app/main.py for why same-origin was necessary) -- at which
        # point `default-src 'none'` would have blocked the dashboard's own
        # stylesheet and scripts, and it would have rendered as a blank page
        # with errors only visible in a browser console.
        #
        # Keying on Content-Type rather than on a path prefix means the
        # correct policy follows the content automatically: /dashboard/summary
        # returns JSON and keeps the strict policy, while any HTML this app
        # ever serves gets the document policy without anyone remembering to
        # add another path to a list.
        content_type = (h.get("content-type") or "").lower()
        if content_type.startswith("text/html"):
            h["Content-Security-Policy"] = (
                "default-src 'none'; "
                # No external script origin. The dashboard's chart used to
                # come from cdnjs, which meant this policy had to allow a
                # third-party host to execute code on the same origin that
                # renders audit evidence. The chart is now inline SVG drawn by
                # the page itself, so that allowance is gone: nothing off this
                # origin can run here at all. 'unsafe-inline' still covers the
                # page's own <script>/<style> blocks — narrow it to a nonce if
                # the frontend ever grows past one file.
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                # connect-src stays 'self': the dashboard talks only to this
                # API, on this origin. If a compromised dependency ever tried
                # to exfiltrate audit data to another host, this line is what
                # stops the request.
                "connect-src 'self'; "
                # 'self', not 'none': the demo page frames the storefront it
                # audits. Still blocks every other origin from framing us.
                "frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
            )
        else:
            h["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )
        return response
