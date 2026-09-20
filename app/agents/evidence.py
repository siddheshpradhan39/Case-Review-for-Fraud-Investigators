"""Evidence bundle shared by verifier / challenger / panel: compact, deterministic, and the ONLY thing (plus tool results)
that grounding is checked against."""
from .. import db, related, service
from ..llm import tools


def bundle(case, with_similar=True):
    d = case["data"]
    b = {
        "case": {k: d[k] for k in ("case_id", "claim_number", "claim_date", "care_type", "state", "claim_amount_usd")},
        "signals": {k: d[k] for k in ("duplicate_service_billed", "weekly_visit_frequency", "member_provider_distance_miles",
                                       "prior_claims_last_12mo", "shared_contact_with_provider", "weekend_billing_ratio",
                                       "amount_vs_peer_avg_pct", "round_dollar_billing_ratio", "recent_policy_change_flag",
                                       "service_overlap_other_provider")},
        "derived": {k: d[k] for k in ("implied_weekly_travel_miles", "log_amount_robust_z", "amount_vs_care_type_median_x", "binary_flag_count")},
        "rule_engine": {"score": case["score"], "level": case["level"],
                        "fired_rules": [{k: r[k] for k in ("rule_id", "name", "level", "domain", "evidence")} for r in case["rules"]]},
        "linked_cases": [{"case_id": l["case_id"], "level": l.get("level"), "score": l.get("score"), "care_type": l.get("care_type"),
                          "amount": l.get("amount"), "claim_date": l.get("claim_date"), "status": l.get("status")} for l in case["linked"]],
        "notes": [{"author": n["author"], "role": n["role"], "at": n["created_at"], "text": n["text"]} for n in case["notes"]],
        "investigator_feedback": [{"indicator": f["indicator_text"], "decision": f["decision"]} for f in case["feedback"]],
        "current_status": case["status"],
    }
    if with_similar:
        b["nearest_similar_cases"] = [{k: v for k, v in x.items() if k != "notes"} for x in tools.find_similar_cases(case["case_id"], 3).get("similar", [])]
    # notes + dispositions on OTHER (linked / similar) cases: precedent context, never facts about this case
    b["related_case_notes"] = related.context(case["case_id"])
    return b


def related_text(case):
    return related.notes_text(related.context(case["case_id"]))


def linked_levels(case):
    return tuple(l.get("level") for l in case["linked"] if l.get("level"))


def related_ids(case):
    return [c["case_id"] for c in related.context(case["case_id"])] + [c for c, _ in related.related_ids(case["case_id"])]
