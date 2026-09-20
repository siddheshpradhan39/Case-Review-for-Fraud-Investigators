"""Deterministic router + spawn planner. NO LLM: depth of investigation scales with how hard the case is.
  FAST    no rule fired and no linked case      -> 1 batched cheap call (no tools, no specialists)
  OBVIOUS rule engine says CRITICAL             -> verifier only (facts are unambiguous; specialists would only restate them)
  DEEP    everything else                       -> spawn only the specialists whose domain has a fired signal,
                                                   then challenger + verifier (+ panel if contested)"""
from dataclasses import dataclass

from .domains import specialists_for


@dataclass
class Plan:
    lane: str
    specialists: list
    reason: str


def plan(case) -> Plan:
    fired = [r["rule_id"] for r in case["rules"]]
    linked = case["data"].get("linked_case_ids", [])
    if not fired and not linked:
        return Plan("FAST", [], "no rules fired, no linked cases")
    if case["level"] == "CRITICAL":
        return Plan("OBVIOUS", [], f"rule engine CRITICAL ({case['score']})")
    sp = specialists_for(fired, linked, case["rules"])
    return Plan("DEEP", sp, f"{len(fired)} rules fired" + (f", linked to {', '.join(linked)}" if linked else ""))
