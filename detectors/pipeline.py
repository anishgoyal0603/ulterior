"""Runs every detection layer against a captured FunnelTrace.

Ordering note: violations come back sorted by confidence descending, so the
strongest evidence is what a reader sees first. With evidence tiers now
capping confidence per detector (see taxonomy.py), that sort has a useful
side effect — provable findings naturally rise above indicative ones without
any special-casing, because a provable detector can reach 1.0 and an
indicative one can never exceed 0.6.
"""

from typing import List

from .layer1_rules import run_layer1, Violation
from .layer1_flow import run_layer1_flow
from .layer2_visual import run_layer2
from .layer3_language import run_layer3
from capture.funnel_walker import FunnelTrace


def audit(trace: FunnelTrace, use_llm: bool = False) -> List[Violation]:
    violations = []
    violations += run_layer1(trace)
    violations += run_layer1_flow(trace)
    violations += run_layer2(trace)
    violations += run_layer3(trace, use_llm=use_llm)
    violations.sort(key=lambda v: v.confidence, reverse=True)
    return violations
