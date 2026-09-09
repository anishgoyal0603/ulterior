"""
Registry of available site adapters. TO ONBOARD A NEW REAL SITE (e-commerce,
flight OTA, or anything else): write a new adapter module following the
pattern in capture/adapters/demo_ecommerce.py or demo_flight.py (a handful
of FunnelStep entries with real https:// URLs), then add one line here.
Nothing else in the codebase needs to change.
"""

from capture.adapters.demo_ecommerce import ECOMMERCE_DARK_DEMO
from capture.adapters.demo_ecommerce_clean import ECOMMERCE_CLEAN_DEMO
from capture.adapters.demo_flight import FLIGHT_DARK_DEMO
from capture.adapters.hosted_demo import HOSTED_DARK_DEMO, HOSTED_CLEAN_DEMO
from capture.adapters.demo_all_patterns import (
    FORCED_ACTION_DEMO, SUBSCRIPTION_TRAP_DEMO, SAAS_BILLING_DEMO,
    NAGGING_DEMO, ROGUE_ALERT_DEMO, CLEAN_CONTROL_DEMO,
)

ADAPTER_REGISTRY = {
    # The two hosted adapters walk the bundled storefront over real HTTP and
    # are what the public demo page runs. Listed first because they are the
    # ones a visitor should reach for.
    "hosted_dark_demo": HOSTED_DARK_DEMO,
    "hosted_clean_demo": HOSTED_CLEAN_DEMO,
    "ecommerce_dark_demo": ECOMMERCE_DARK_DEMO,
    "ecommerce_clean_demo": ECOMMERCE_CLEAN_DEMO,
    "flight_dark_demo": FLIGHT_DARK_DEMO,
    "forced_action_demo": FORCED_ACTION_DEMO,
    "subscription_trap_demo": SUBSCRIPTION_TRAP_DEMO,
    "saas_billing_demo": SAAS_BILLING_DEMO,
    "nagging_demo": NAGGING_DEMO,
    "rogue_alert_demo": ROGUE_ALERT_DEMO,
    "clean_control_demo": CLEAN_CONTROL_DEMO,
    # "flipkart": FLIPKART_ADAPTER,          <- add real sites here
    # "makemytrip": MAKEMYTRIP_ADAPTER,      <- once you have a real adapter file
}
