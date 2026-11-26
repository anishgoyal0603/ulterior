"""
Community price history: prices that extension users chose to share.

Off by default in the extension; a person switches it on and the browser
asks their permission first. What arrives is a product id ("amazon.in:B0X..")
and a price. This module validates it, stores it against today's date, and
answers history questions with the MEDIAN of each day's reports -- a single
wrong or malicious report cannot move a day's price.

Reads are POST on purpose: product ids stay out of URLs, and so out of every
proxy and access log between the browser and here.
"""
import re
import statistics
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

from . import config

# Kept in step with SUPPORTED_SITES in extension/src/core.js (a test checks).
SUPPORTED_SITES = [
    "amazon.in", "flipkart.com", "myntra.com", "ajio.com", "meesho.com", "nykaa.com",
    "nykaafashion.com", "tatacliq.com", "jiomart.com", "snapdeal.com", "firstcry.com",
    "croma.com", "reliancedigital.in", "lenskart.com", "bigbasket.com",
    "swiggy.com", "zomato.com", "blinkit.com", "zeptonow.com",
    "bookmyshow.com", "district.in", "makemytrip.com", "goibibo.com", "cleartrip.com",
    "yatra.com", "ixigo.com", "easemytrip.com", "redbus.in", "trivago.in", "booking.com",
    "agoda.com", "goindigo.in", "airindia.com", "spicejet.com", "akasaair.com", "oyorooms.com",
]
_DEV_SITES = {"127.0.0.1", "localhost"}

KEY_RE = re.compile(r"^([a-z0-9.-]{3,60}):([A-Za-z0-9/_.~%+-]{1,160})$")
MIN_PRICE, MAX_PRICE = 1.0, 10_000_000.0


def site_of_key(key: str):
    m = KEY_RE.match(key or "")
    if not m:
        return None
    site = m.group(1)
    if site in SUPPORTED_SITES or (site in _DEV_SITES and not config.IS_PRODUCTION):
        return site
    return None


def today() -> date:
    return datetime.now(timezone.utc).date()


class _SeenToday:
    """One report per (sender, product, day) counts. Kept in memory only and
    bounded; the sender is a hash that is never written anywhere."""

    def __init__(self, cap: int = 200_000):
        self.cap = cap
        self.items: "OrderedDict[tuple, None]" = OrderedDict()

    def first_time(self, sender: str, key: str, day: date) -> bool:
        k = (hash(sender), key, day)
        if k in self.items:
            return False
        self.items[k] = None
        if len(self.items) > self.cap:
            self.items.popitem(last=False)
        return True


seen_today = _SeenToday()


def daily_medians(rows) -> List[dict]:
    """rows: (observed_on, price). Returns [{day, price, reports}] oldest first."""
    by_day: Dict[date, list] = defaultdict(list)
    for d, p in rows:
        by_day[d].append(p)
    out = []
    for d in sorted(by_day):
        vals = by_day[d]
        out.append({"day": d.isoformat(), "price": round(statistics.median(vals), 2), "reports": len(vals)})
    return out


def window_start(days: int) -> date:
    return today() - timedelta(days=max(0, days))
