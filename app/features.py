"""Raw CSV loading + derived features.

Every derived feature exists because the data analysis showed it separates cohorts
(see docs/RULES.md for the evidence). Nothing here is invented: `days_since_policy_change`
would be useful but cannot be derived from a 0/1 flag, so it is deliberately absent.
"""
import csv
import math
import statistics as st
from pathlib import Path

DATA_CSV = Path(__file__).resolve().parent.parent / "data" / "sample_cases.csv"

BINARY = ["duplicate_service_billed", "shared_contact_with_provider",
          "recent_policy_change_flag", "service_overlap_other_provider"]
CONTINUOUS = ["weekly_visit_frequency", "member_provider_distance_miles", "prior_claims_last_12mo",
              "weekend_billing_ratio", "amount_vs_peer_avg_pct", "round_dollar_billing_ratio"]
SIGNALS = BINARY + CONTINUOUS
NUMERIC = ["claim_amount_usd"] + SIGNALS

# Fraud "domains": signals in one domain are correlated evidence of the same thing,
# so the scorer counts a domain once (see scoring.py).
DOMAINS = {
    "Billing": ["duplicate_service_billed", "round_dollar_billing_ratio", "amount_vs_peer_avg_pct",
                "weekend_billing_ratio", "claim_amount_usd"],
    "Utilization": ["weekly_visit_frequency", "prior_claims_last_12mo"],
    "Geography": ["member_provider_distance_miles", "service_overlap_other_provider",
                  "implied_weekly_travel_miles"],
    "Relationship": ["shared_contact_with_provider"],
    "Timing": ["recent_policy_change_flag"],
}


def load_raw(path=DATA_CSV):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for c in NUMERIC:
            v = float(r[c])
            r[c] = int(v) if v == int(v) else v
    return rows


def robust_z(values):
    """Return fn(x) -> robust z using median/MAD (1.4826 scaling)."""
    med = st.median(values)
    mad = st.median(abs(v - med) for v in values) or 1e-9
    return lambda x: (x - med) / (1.4826 * mad), med, mad


def add_derived(rows):
    """Adds derived features in place. Needs the whole portfolio for portfolio-relative ones."""
    logs = [math.log10(r["claim_amount_usd"]) for r in rows]
    zfn, _, _ = robust_z(logs)
    by_type = {}
    for r in rows:
        by_type.setdefault(r["care_type"], []).append(r["claim_amount_usd"])
    type_med = {k: st.median(v) for k, v in by_type.items()}
    claim_groups = {}
    for r in rows:
        claim_groups.setdefault(r["claim_number"], []).append(r["case_id"])
    for r in rows:
        r["implied_weekly_travel_miles"] = r["weekly_visit_frequency"] * r["member_provider_distance_miles"] * 2
        r["log_amount_robust_z"] = round(zfn(math.log10(r["claim_amount_usd"])), 2)
        r["amount_vs_care_type_median_x"] = round(r["claim_amount_usd"] / type_med[r["care_type"]], 2)
        r["binary_flag_count"] = sum(int(r[b]) for b in BINARY)
        r["linked_case_ids"] = [c for c in claim_groups[r["claim_number"]] if c != r["case_id"]]
    return rows
