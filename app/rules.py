"""Rule registry R01..R16. Thresholds are read from data/calibration.json (natural-break calibration),
never hard-coded. Each fired rule carries the evidence (field=value + where it sits in the portfolio)
so the LLM judge and the investigator can verify it.
"""
import copy

from . import customrules
from .calibration import load_calibration
from .customrules import RuleConfig
from .features import CONTINUOUS, DOMAINS

LEVEL_W = {"LOW": 5, "MEDIUM": 12, "HIGH": 25, "CRITICAL": 40}
LEVEL_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SILENT_DRIFT_MIN = 3   # zero-flag cases have either 0 or 5 elevated signals; 3 = midpoint of that empty gap
CONV_HIGH, CONV_CRIT = 3, 4  # domains tripped at >= MEDIUM

# (id, name, domain, description) — descriptions are completed with calibrated numbers in catalog()
RULE_META = {
    "R01": ("Duplicate service billed", "Billing", "Same service billed more than once."),
    "R02": ("Service overlaps another provider", "Geography", "Member billed by two providers for overlapping service."),
    "R03": ("Shared contact info with provider", "Relationship", "Member and provider share contact details (collusion indicator)."),
    "R04": ("Recent policy change", "Timing", "Coverage changed shortly before the claim. Weak on its own."),
    "R05": ("Weekly visit frequency", "Utilization", "Visits per week vs portfolio natural breaks."),
    "R06": ("Member-provider distance", "Geography", "Travel distance vs portfolio natural breaks."),
    "R07": ("Amount vs peer average", "Billing", "Claim amount above same-care-type peer average."),
    "R08": ("Round-dollar billing ratio", "Billing", "Share of round-dollar charges (estimation/fabrication pattern)."),
    "R09": ("Weekend billing ratio", "Billing", "Share of services billed on weekends."),
    "R10": ("Prior claims (12 mo)", "Utilization", "Claim history velocity."),
    "R11": ("Implied weekly travel miles", "Geography", "DERIVED: visits/wk x distance x 2. Physical plausibility of the visit pattern."),
    "R12": ("High-value claim", "Billing", "DERIVED: robust z-score of log(amount); exposure indicator."),
    "R13": ("Multi-domain convergence", "Convergence", "DERIVED: independent fraud domains tripped at once. Signals co-move, so convergence is the real evidence."),
    "R14": ("Silent drift (no flags, many elevated signals)", "Convergence", "DERIVED: no binary flag fired but several continuous signals are elevated -- flag counting would miss it."),
    "R15": ("Claim number reused across cases", "Integrity", "Same claim number on more than one referral."),
    "R17": ("Lookalike of a confirmed-fraud case", "Precedent", "HUMAN FEEDBACK: resembles (median nearest-neighbour distance or closer) a case an investigator later confirmed as fraud."),
    "R16": ("High-value claim after policy change", "Timing", "Policy change combined with an above-p75 claim amount."),
}


def _tier(value, sig, cal):
    s = cal["signals"][sig]
    if value >= s["extreme_threshold"]:
        return "HIGH"
    if value >= s["elevated_threshold"]:
        return "MEDIUM"
    return None


def _ctx(sig, cal):
    g = cal["signals"][sig]["groups"]
    return (f"portfolio: baseline {g['baseline'][0]}..{g['baseline'][1]} ({g['baseline'][2]} cases), "
            f"elevated {g['elevated'][0]}..{g['elevated'][1]} ({g['elevated'][2]}), "
            f"extreme {g['extreme'][0]}..{g['extreme'][1]} ({g['extreme'][2]})")


def _fire(rid, level, evidence, fields):
    name, domain, _ = RULE_META[rid]
    return {"rule_id": rid, "name": name, "level": level, "domain": domain,
            "weight": LEVEL_W[level], "evidence": evidence, "fields": fields}


# Which built-in rules can be tuned from the Rules tab. Threshold rules: (signal, MEDIUM-tier name, HIGH-tier name). Level rules: single-level.
THRESHOLD_RULES = {"R05": "weekly_visit_frequency", "R06": "member_provider_distance_miles", "R07": "amount_vs_peer_avg_pct",
                   "R08": "round_dollar_billing_ratio", "R09": "weekend_billing_ratio", "R10": "prior_claims_last_12mo",
                   "R11": "implied_weekly_travel_miles"}
LEVEL_RULES = {"R01": "HIGH", "R02": "HIGH", "R03": "HIGH", "R04": "LOW", "R14": "MEDIUM", "R15": "HIGH", "R16": "MEDIUM"}


def _cfg(config):
    """Explicit config wins; otherwise the live one from the DB (lazy import: rulestore imports this module)."""
    if config is not None:
        return config
    try:
        from . import rulestore
        return rulestore.load_config()
    except Exception:
        return RuleConfig.empty()


def _tuned(cal, cfg):
    """Copy of the calibration with per-rule threshold overrides applied (default calibration is never mutated)."""
    if not any(v.get("elevated") is not None or v.get("extreme") is not None for v in cfg.overrides.values()):
        return cal
    t = copy.deepcopy(cal)
    for rid, sig in THRESHOLD_RULES.items():
        ov = cfg.overrides.get(rid, {})
        if ov.get("elevated") is not None:
            t["signals"][sig]["elevated_threshold"] = ov["elevated"]
        if ov.get("extreme") is not None:
            t["signals"][sig]["extreme_threshold"] = ov["extreme"]
    return t


def evaluate(case, cal=None, config=None):
    """case: dict with raw + derived features. Returns list of fired rules."""
    cfg = _cfg(config)
    base_cal = cal or load_calibration()
    cal = _tuned(base_cal, cfg)
    out = []
    c = case
    if c["duplicate_service_billed"]:
        out.append(_fire("R01", "HIGH", "duplicate_service_billed=1 (fires on "
                         f"{cal['binary']['duplicate_service_billed']['count']}/{cal['n_cases']} cases)",
                         {"duplicate_service_billed": 1}))
    if c["service_overlap_other_provider"]:
        out.append(_fire("R02", "HIGH", "service_overlap_other_provider=1 (fires on "
                         f"{cal['binary']['service_overlap_other_provider']['count']}/{cal['n_cases']} cases)",
                         {"service_overlap_other_provider": 1}))
    if c["shared_contact_with_provider"]:
        out.append(_fire("R03", "HIGH", "shared_contact_with_provider=1 (fires on "
                         f"{cal['binary']['shared_contact_with_provider']['count']}/{cal['n_cases']} cases)",
                         {"shared_contact_with_provider": 1}))
    if c["recent_policy_change_flag"]:
        out.append(_fire("R04", "LOW", "recent_policy_change_flag=1 (fires on "
                         f"{cal['binary']['recent_policy_change_flag']['count']}/{cal['n_cases']} cases; weak alone)",
                         {"recent_policy_change_flag": 1}))
    for rid, sig in [("R05", "weekly_visit_frequency"), ("R06", "member_provider_distance_miles"),
                     ("R07", "amount_vs_peer_avg_pct"), ("R08", "round_dollar_billing_ratio"),
                     ("R09", "weekend_billing_ratio"), ("R10", "prior_claims_last_12mo")]:
        lvl = _tier(c[sig], sig, cal)
        if lvl:
            th = cal["signals"][sig]
            out.append(_fire(rid, lvl, f"{sig}={c[sig]} (elevated>={th['elevated_threshold']}, "
                                       f"extreme>={th['extreme_threshold']}); {_ctx(sig, cal)}", {sig: c[sig]}))
    sig = "implied_weekly_travel_miles"
    th = cal["signals"][sig]
    if c[sig] >= th["elevated_threshold"]:
        lvl = "CRITICAL" if c[sig] >= th["extreme_threshold"] else "HIGH"
        out.append(_fire("R11", lvl, f"{sig}={c[sig]} = {c['weekly_visit_frequency']} visits/wk x "
                         f"{c['member_provider_distance_miles']} mi x 2 (elevated>={th['elevated_threshold']}, "
                         f"extreme>={th['extreme_threshold']}; portfolio median {th['median']})", {sig: c[sig]}))
    zth = cal["log_amount_robust_z"]["outlier_threshold"]
    if c["log_amount_robust_z"] >= zth:
        lvl = "HIGH" if c["claim_amount_usd"] >= cal["amount"]["p90"] else "MEDIUM"
        out.append(_fire("R12", lvl, f"claim_amount_usd={c['claim_amount_usd']} (log robust z={c['log_amount_robust_z']} >= {zth}; "
                         f"portfolio median ${cal['amount']['median']:.0f}, p90 ${cal['amount']['p90']:.0f})",
                         {"claim_amount_usd": c["claim_amount_usd"]}))
    if c["linked_case_ids"]:
        out.append(_fire("R15", "HIGH", f"claim_number {c['claim_number']} also appears on {', '.join(c['linked_case_ids'])}",
                         {"claim_number": c["claim_number"]}))
    if c["recent_policy_change_flag"] and c["claim_amount_usd"] >= cal["amount"]["p75"]:
        out.append(_fire("R16", "MEDIUM", f"policy change AND claim_amount_usd={c['claim_amount_usd']} >= p75 "
                         f"(${cal['amount']['p75']:.0f})", {"recent_policy_change_flag": 1,
                                                          "claim_amount_usd": c["claim_amount_usd"]}))
    # ---- overrides: single-level rules can change level; disabled built-ins drop out BEFORE composites so R13/R14 recompute ----
    for r in out:
        lv = cfg.overrides.get(r["rule_id"], {}).get("level")
        if lv in LEVEL_W and r["rule_id"] in LEVEL_RULES:
            r["level"], r["weight"] = lv, LEVEL_W[lv]
    out = [r for r in out if cfg.enabled(r["rule_id"])]
    # ---- custom (user-defined) rules ----
    for cr in cfg.custom:
        fired = customrules.fire(cr, c)
        if fired:
            out.append(fired)
    # ---- composite rules (need the base rules) ----
    tripped = sorted({r["domain"] for r in out if r["domain"] in DOMAINS and LEVEL_ORDER.index(r["level"]) >= 1})
    n_dom = len(tripped)
    if n_dom >= CONV_HIGH:
        out.append(_fire("R13", "CRITICAL" if n_dom >= CONV_CRIT else "HIGH",
                         f"{n_dom} independent domains tripped at >=MEDIUM: {', '.join(tripped)}",
                         {"domains_tripped": n_dom}))
    n_elev = sum(1 for s in CONTINUOUS + ["implied_weekly_travel_miles"]
                 if c[s] >= base_cal["signals"][s]["elevated_threshold"])   # R14 stays on the calibrated cut-offs
    c["_elevated_continuous_count"] = n_elev
    if c["binary_flag_count"] == 0 and n_elev >= SILENT_DRIFT_MIN:
        out.append(_fire("R14", "MEDIUM", f"0 binary flags but {n_elev}/7 continuous signals elevated "
                         "(no zero-flag case in the portfolio has 1-4)", {"elevated_continuous_count": n_elev}))
    lv = cfg.overrides.get("R14", {}).get("level")
    return [dict(r, level=lv, weight=LEVEL_W[lv]) if (r["rule_id"] == "R14" and lv in LEVEL_W) else r
            for r in out if cfg.enabled(r["rule_id"])]


def fire_precedent(lookalikes):
    """R17: built from human outcome labels, applied after the base rules (see service.ingest)."""
    ids = ", ".join(f"{c} (distance {d})" for c, d in lookalikes)
    return _fire("R17", "HIGH" if len(lookalikes) >= 2 else "MEDIUM",
                 f"resembles confirmed-fraud case(s): {ids}; threshold = median nearest-neighbour distance",
                 {"confirmed_fraud_lookalikes": [c for c, _ in lookalikes]})


def catalog(cal=None, config=None):
    """Rule catalog with calibrated thresholds (used by RULES.md, the UI and the LLM tool)."""
    cal = cal or load_calibration()
    th = cal["signals"]
    lines = {
        "R01": "binary flag -> HIGH", "R02": "binary flag -> HIGH", "R03": "binary flag -> HIGH",
        "R04": "binary flag -> LOW (12/50, weak alone)",
        "R05": f"visits/wk >={th['weekly_visit_frequency']['elevated_threshold']} MEDIUM, >={th['weekly_visit_frequency']['extreme_threshold']} HIGH",
        "R06": f"miles >={th['member_provider_distance_miles']['elevated_threshold']} MEDIUM, >={th['member_provider_distance_miles']['extreme_threshold']} HIGH",
        "R07": f"% vs peer >={th['amount_vs_peer_avg_pct']['elevated_threshold']} MEDIUM, >={th['amount_vs_peer_avg_pct']['extreme_threshold']} HIGH",
        "R08": f"round-dollar >={th['round_dollar_billing_ratio']['elevated_threshold']} MEDIUM, >={th['round_dollar_billing_ratio']['extreme_threshold']} HIGH",
        "R09": f"weekend ratio >={th['weekend_billing_ratio']['elevated_threshold']} MEDIUM, >={th['weekend_billing_ratio']['extreme_threshold']} HIGH",
        "R10": f"prior claims >={th['prior_claims_last_12mo']['elevated_threshold']} MEDIUM, >={th['prior_claims_last_12mo']['extreme_threshold']} HIGH",
        "R11": f"miles/wk >={th['implied_weekly_travel_miles']['elevated_threshold']} HIGH, >={th['implied_weekly_travel_miles']['extreme_threshold']} CRITICAL",
        "R12": f"log-amount robust z >={cal['log_amount_robust_z']['outlier_threshold']} MEDIUM (HIGH if >= p90 ${cal['amount']['p90']:.0f})",
        "R13": f">={CONV_HIGH} domains HIGH, >={CONV_CRIT} CRITICAL",
        "R14": f"0 flags and >={SILENT_DRIFT_MIN} elevated continuous signals -> MEDIUM",
        "R15": "same claim_number on >1 case -> HIGH",
        "R17": "within the median nearest-neighbour distance of a human-confirmed FRAUD case: 1 -> MEDIUM, 2+ -> HIGH (never bulk-clearable)",
        "R16": f"policy change and amount >= p75 (${cal['amount']['p75']:.0f}) -> MEDIUM",
    }
    out = [{"rule_id": k, "name": v[0], "domain": v[1], "description": v[2], "logic": lines[k]} for k, v in RULE_META.items()]
    for cr in _cfg(config).custom:
        out.append({"rule_id": cr["id"], "name": cr["name"], "domain": cr["domain"], "description": cr["description"] or "custom rule",
                    "logic": f"{customrules.describe(cr['logic'])} -> {cr['level']}"})
    return out
