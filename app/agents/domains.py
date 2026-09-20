"""Which rules/fields belong to which specialist, and the evidence slice + hash each one sees.
Specialists see ONLY their slice, so (a) prompts are small and (b) a change outside a slice does not invalidate that
specialist's cached finding (incremental re-assessment)."""
import hashlib
import json

SPECIALISTS = {
    "billing": {
        "title": "Billing & Timing",
        "rules": {"R01", "R04", "R07", "R08", "R09", "R12", "R16"},
        "fields": ["claim_amount_usd", "duplicate_service_billed", "amount_vs_peer_avg_pct", "amount_vs_care_type_median_x",
                   "round_dollar_billing_ratio", "weekend_billing_ratio", "log_amount_robust_z", "recent_policy_change_flag"],
        "tools": ["get_peer_stats"],
        "focus": "duplicate billing, amount vs peers, round-dollar and weekend billing patterns, policy-change timing",
    },
    "collusion": {
        "title": "Collusion & Linkage",
        "rules": {"R03", "R15"},
        "fields": ["shared_contact_with_provider", "claim_number", "linked_case_ids"],
        "tools": ["get_linked_cases"],
        "focus": "member-provider shared contact details and other cases sharing the same claim number (data-entry duplicate vs reuse)",
    },
    "geo_util": {
        "title": "Geography & Utilization",
        "rules": {"R02", "R05", "R06", "R10", "R11"},
        "fields": ["member_provider_distance_miles", "service_overlap_other_provider", "implied_weekly_travel_miles",
                   "weekly_visit_frequency", "prior_claims_last_12mo", "care_type"],
        "tools": ["get_peer_stats"],
        "focus": "travel distance, visit frequency, overlapping providers, claim history velocity, physical plausibility",
    },
}


DOMAIN_TO_SPECIALIST = {"Billing": "billing", "Timing": "billing", "Geography": "geo_util", "Utilization": "geo_util",
                        "Relationship": "collusion", "Integrity": "collusion"}


def specialists_for(fired_ids, linked, rules=None):
    """Spawn ONLY specialists that have a fired signal in their domain (linkage always counts for collusion).
    Custom rules (ids not in any specialist's set) are routed by their domain; domain 'Custom' has no specialist."""
    out = []
    for k, d in SPECIALISTS.items():
        if d["rules"] & set(fired_ids) or (k == "collusion" and linked):
            out.append(k)
    known = set().union(*(d["rules"] for d in SPECIALISTS.values()))
    for r in rules or []:
        if r["rule_id"] not in known and r["rule_id"][:1] == "X":
            k = DOMAIN_TO_SPECIALIST.get(r["domain"])
            if k and k not in out:
                out.append(k)
    return out


def slice_for(case, domain):
    d = SPECIALISTS[domain]
    data = case["data"]
    return {
        "case_id": case["case_id"],
        "fields": {**{f: data.get(f) for f in d["fields"] if f in data},
                   **{f: data.get(f) for r in case["rules"] if r["rule_id"][:1] == "X" and DOMAIN_TO_SPECIALIST.get(r["domain"]) == domain
                      for f in (r.get("fields") or {}) if f in data}},
        "fired_rules": [{k: r[k] for k in ("rule_id", "name", "level", "evidence")} for r in case["rules"]
                        if r["rule_id"] in d["rules"] or (r["rule_id"][:1] == "X" and DOMAIN_TO_SPECIALIST.get(r["domain"]) == domain)],
    }


def slice_hash(sl):
    return hashlib.sha256(json.dumps(sl, sort_keys=True, default=str).encode()).hexdigest()[:16]
