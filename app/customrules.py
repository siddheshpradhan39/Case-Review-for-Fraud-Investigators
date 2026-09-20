"""Custom-rule DSL: a validated declarative condition tree, evaluated WITHOUT eval/exec (no code execution from the UI).

  logic = {"op": "AND"|"OR", "conds": [ {"field","cmp","value"} | <nested group> ]}   (depth <= 3, <= 12 conditions)

Also defines RuleConfig, the (DB-free) bundle of built-in overrides + custom rules that rules.evaluate consumes."""
import math
import operator
from dataclasses import dataclass, field, replace

MAX_DEPTH, MAX_CONDS = 3, 12
NUM_CMP = (">=", ">", "<=", "<", "==", "!=")
CAT_CMP = ("==", "!=", "in", "not_in")
LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
DOMAIN_CHOICES = ["Billing", "Utilization", "Geography", "Relationship", "Timing", "Integrity", "Custom"]

# name -> (type, label). Types: number | flag (0/1) | category
FIELDS = {
    "claim_amount_usd": ("number", "Claim amount ($)"),
    "weekly_visit_frequency": ("number", "Visits per week"),
    "member_provider_distance_miles": ("number", "Member-provider distance (mi)"),
    "prior_claims_last_12mo": ("number", "Prior claims (12 mo)"),
    "weekend_billing_ratio": ("number", "Weekend billing ratio"),
    "amount_vs_peer_avg_pct": ("number", "Amount vs peer average (%)"),
    "round_dollar_billing_ratio": ("number", "Round-dollar billing ratio"),
    "implied_weekly_travel_miles": ("number", "Implied weekly travel miles (derived)"),
    "log_amount_robust_z": ("number", "Amount robust z-score, log scale (derived)"),
    "amount_vs_care_type_median_x": ("number", "Amount as multiple of care-type median (derived)"),
    "binary_flag_count": ("number", "Number of binary flags fired (derived)"),
    "duplicate_service_billed": ("flag", "Duplicate service billed"),
    "shared_contact_with_provider": ("flag", "Shared contact with provider"),
    "recent_policy_change_flag": ("flag", "Recent policy change"),
    "service_overlap_other_provider": ("flag", "Service overlaps another provider"),
    "care_type": ("category", "Care type"),
    "state": ("category", "State"),
}


class RuleError(ValueError):
    pass


def _validate_cond(c):
    f, cmp_, v = c.get("field"), c.get("cmp"), c.get("value")
    if f not in FIELDS:
        raise RuleError(f"unknown field '{f}'")
    typ = FIELDS[f][0]
    if typ == "category":
        if cmp_ not in CAT_CMP:
            raise RuleError(f"{f}: operator must be one of {CAT_CMP}")
        if cmp_ in ("in", "not_in"):
            if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
                raise RuleError(f"{f}: '{cmp_}' needs a non-empty list of text values")
        elif not isinstance(v, str) or not v:
            raise RuleError(f"{f}: value must be text")
    else:
        if cmp_ not in NUM_CMP:
            raise RuleError(f"{f}: operator must be one of {NUM_CMP}")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise RuleError(f"{f}: value must be a finite number")
        if typ == "flag" and v not in (0, 1):
            raise RuleError(f"{f}: flag value must be 0 or 1")


def validate_logic(logic, depth=1):
    """Returns the number of conditions; raises RuleError."""
    if not isinstance(logic, dict) or logic.get("op") not in ("AND", "OR"):
        raise RuleError("each group needs op AND or OR")
    if depth > MAX_DEPTH:
        raise RuleError(f"groups nested deeper than {MAX_DEPTH}")
    conds = logic.get("conds")
    if not isinstance(conds, list) or not conds:
        raise RuleError("a group needs at least one condition")
    n = 0
    for c in conds:
        if isinstance(c, dict) and "conds" in c:
            n += validate_logic(c, depth + 1)
        elif isinstance(c, dict):
            _validate_cond(c)
            n += 1
        else:
            raise RuleError("malformed condition")
    if n > MAX_CONDS:
        raise RuleError(f"too many conditions (max {MAX_CONDS})")
    return n


_OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq, "!=": operator.ne}


def _test(c, case):
    v, want, cmp_ = case.get(c["field"]), c["value"], c["cmp"]
    if v is None:
        return False
    if cmp_ == "in":
        return v in want
    if cmp_ == "not_in":
        return v not in want
    return _OPS[cmp_](v, want)


def evaluate_logic(logic, case):
    """-> (matched: bool, true_conditions: list[dict])"""
    hits = []
    results = []
    for c in logic["conds"]:
        if "conds" in c:
            ok, sub = evaluate_logic(c, case)
            results.append(ok)
            hits += sub
        else:
            ok = _test(c, case)
            results.append(ok)
            if ok:
                hits.append(c)
    matched = all(results) if logic["op"] == "AND" else any(results)
    return matched, (hits if matched else [])


def describe(logic):
    parts = []
    for c in logic["conds"]:
        if "conds" in c:
            parts.append("(" + describe(c) + ")")
        else:
            v = ", ".join(c["value"]) if isinstance(c["value"], list) else c["value"]
            parts.append(f"{c['field']} {c['cmp'].replace('_', ' ')} {v}")
    return f" {logic['op']} ".join(parts)


def referenced_fields(logic):
    out = []
    for c in logic["conds"]:
        out += referenced_fields(c) if "conds" in c else [c["field"]]
    return list(dict.fromkeys(out))


def fire(rule, case):
    """Evaluate one custom rule dict against a case. Returns the fired-rule dict (same shape as built-ins) or None."""
    from .rules import LEVEL_W
    ok, hits = evaluate_logic(rule["logic"], case)
    if not ok:
        return None
    ev = "; ".join(f"{c['field']}={case.get(c['field'])} ({c['cmp'].replace('_', ' ')} {', '.join(c['value']) if isinstance(c['value'], list) else c['value']})" for c in hits)
    return {"rule_id": rule["id"], "name": rule["name"], "level": rule["level"], "domain": rule["domain"], "weight": LEVEL_W[rule["level"]],
            "evidence": f"custom rule: {describe(rule['logic'])} -> {ev}", "fields": {f: case.get(f) for f in referenced_fields(rule["logic"])}}


@dataclass
class RuleConfig:
    """overrides: rule_id -> {"enabled": bool, "level": str|None, "elevated": num|None, "extreme": num|None}
    custom: list of ACTIVE custom rule dicts (shadow rules are added only by the backtester)."""
    overrides: dict = field(default_factory=dict)
    custom: list = field(default_factory=list)

    @staticmethod
    def empty():
        return RuleConfig()

    def enabled(self, rid):
        return self.overrides.get(rid, {}).get("enabled", True) is not False

    def without(self, rid):
        """Copy with one rule switched off (built-in via override, custom by removal) - used for leave-one-out labels."""
        ov = {k: dict(v) for k, v in self.overrides.items()}
        ov.setdefault(rid, {})["enabled"] = False
        return replace(self, overrides=ov, custom=[r for r in self.custom if r["id"] != rid])

    def with_custom(self, rule):
        return replace(self, custom=[r for r in self.custom if r["id"] != rule["id"]] + [rule])

    def with_override(self, rid, patch):
        ov = {k: dict(v) for k, v in self.overrides.items()}
        ov.setdefault(rid, {}).update(patch)
        return replace(self, overrides=ov)
