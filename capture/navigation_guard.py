"""
Enforces the SSRF policy inside the browser, on every request it makes.

WHY THIS EXISTS
---------------
`app/ssrf_guard.validate_target_url` checks a URL once, in the API thread,
before the job is queued. That check is necessary and it is not sufficient,
because it validates a URL the browser has not fetched yet. Between the check
and the fetch, two things can change what is actually retrieved:

  1. **A redirect.** The validated host resolves to a public address, so the
     job is accepted. Chromium then does its own DNS lookup and follows
     whatever `Location:` header comes back -- including
     `http://169.254.169.254/latest/meta-data/`, the cloud metadata endpoint.
     The response body lands in PageState.full_text, gets screenshotted to
     disk, and is quoted back to the caller in the finding evidence. That is a
     classic time-of-check/time-of-use hole, and it hands an attacker the
     instance's IAM credentials.

  2. **A link discovery clicks.** capture/funnel_discovery.py finds its own
     way through a funnel by clicking controls whose text matches an intent.
     A page under the attacker's control can offer
     `<a href="http://10.0.0.5:5432/">Proceed to checkout</a>`, which matches
     the checkout intent, is not on the never-click list, and gets clicked.

Neither is reachable through the pre-flight check, because neither URL exists
when it runs. The only place that can enforce the policy is the browser, at
the moment of the request -- which is what this module does.

WHAT IT DOES
------------
Installs a Playwright route handler on every request the page makes. Each one
is validated against the same `validate_target_url` the API uses, so there is
exactly one definition of "safe to fetch" in the codebase. A request that
fails is aborted, and the reason is recorded so an operator can see WHY a walk
came back thin rather than being told nothing.

Deliberately NOT clever: it validates by the same rules for the top-level
document, sub-resources and redirects alike. A stylesheet from an internal IP
is as much an SSRF as a page from one.
"""

from typing import Callable, List, Optional
from urllib.parse import urlparse

from app.ssrf_guard import validate_target_url, UnsafeURLError

# Schemes the browser uses internally that never reach the network and cannot
# address a host. Blocking them would break ordinary pages for no benefit.
_INERT_SCHEMES = {"data", "blob", "about", "javascript"}


class NavigationGuard:
    """Blocks any browser request the SSRF policy would refuse.

    `allow_file` exists for the local fixture suite, which loads pages over
    file:// -- it is False by default and the tests that need it pass it
    explicitly, so a production crawl can never read the server's disk.
    """

    def __init__(self, allow_file: bool = False):
        self.allow_file = allow_file
        self.blocked: List[str] = []

    def _is_allowed(self, url: str) -> bool:
        scheme = (urlparse(url).scheme or "").lower()
        if scheme in _INERT_SCHEMES:
            return True
        if scheme == "file":
            return self.allow_file
        try:
            validate_target_url(url)
            return True
        except UnsafeURLError:
            return False
        except Exception:
            # An unexpected failure in the validator must not open the gate.
            return False

    def install(self, page) -> None:
        def handler(route):
            url = route.request.url
            if self._is_allowed(url):
                try:
                    route.continue_()
                except Exception:
                    pass
                return
            # Record the host, not the full URL: the path of a blocked
            # internal request is exactly the sort of thing not worth
            # persisting, and the host is what an operator needs to see.
            host = urlparse(url).hostname or url
            note = f"Blocked a request to '{host}' -- it resolves somewhere this crawler must not go."
            if note not in self.blocked:
                self.blocked.append(note)
            try:
                route.abort()
            except Exception:
                pass

        page.route("**/*", handler)


def install_navigation_guard(page, allow_file: bool = False) -> NavigationGuard:
    guard = NavigationGuard(allow_file=allow_file)
    guard.install(page)
    return guard
