"""Queue-level pattern detection: rule co-firing clusters and abnormal rule fire-rates by segment."""
import itertools
import json
from collections import Counter
from math import comb

from . import db

COMPOSITE = {"R13", "R14"}  # derived-from-other-rules; excluded so clusters reflect independent evidence


def _cases():
    out = []
    for r in db.rows("SELECT case_id,care_type,state,claim_date,amount,score,level,status,rules FROM cases"):
        r["rules"] = json.loads(r["rules"])
        r["ids"] = {x["rule_id"] for x in r["rules"]} - COMPOSITE
        r["month"] = r["claim_date"][:7]
        out.append(r)
    return out


def binom_tail(k, n, p):
    """P(X >= k), X~Bin(n,p). Exact."""
    if p <= 0:
        return 0.0 if k > 0 else 1.0
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def cofiring_clusters(cases, jaccard=0.6, min_size=2):
    """Greedy agglomerative clustering of cases by Jaccard similarity of fired-rule sets."""
    active = [c for c in cases if c["ids"]]
    groups = [[c] for c in active]

    def sig(g):  # union rules in >=70% of members
        cnt = Counter(i for c in g for i in c["ids"])
        return {i for i, k in cnt.items() if k / len(g) >= 0.7}

    def sim(a, b):
        sa, sb = sig(a), sig(b)
        return len(sa & sb) / len(sa | sb) if sa | sb else 0

    merged = True
    while merged:
        merged = False
        best = (0, None, None)
        for i, j in itertools.combinations(range(len(groups)), 2):
            s = sim(groups[i], groups[j])
            if s > best[0]:
                best = (s, i, j)
        if best[0] >= jaccard:
            _, i, j = best
            groups[i] += groups[j]
            del groups[j]
            merged = True
    out = []
    for g in groups:
        if len(g) < min_size:
            continue
        s = sorted(sig(g))
        out.append({
            "size": len(g), "signature_rules": s, "case_ids": sorted(c["case_id"] for c in g),
            "total_amount": sum(c["amount"] for c in g), "avg_score": round(sum(c["score"] for c in g) / len(g), 1),
            "care_types": dict(Counter(c["care_type"] for c in g)), "states": dict(Counter(c["state"] for c in g)),
            "levels": dict(Counter(c["level"] for c in g)),
        })
    return sorted(out, key=lambda x: (-x["avg_score"], -x["size"]))


def abnormal_firing(cases, min_k=3, max_p=0.02, min_lift=1.8):
    """Rule fire-rate inside a segment vs the rest of the portfolio (exact binomial tail).
    With 50 cases and many segment x rule tests, treat these as leads to check, not proof."""
    n_total = len(cases)
    rule_ids = sorted({i for c in cases for i in c["ids"]})
    findings = []
    for dim in ("care_type", "state", "month"):
        for seg in sorted({c[dim] for c in cases}):
            inside = [c for c in cases if c[dim] == seg]
            outside = [c for c in cases if c[dim] != seg]
            if len(inside) < 4 or not outside:
                continue
            for rid in rule_ids:
                k = sum(1 for c in inside if rid in c["ids"])
                p_out = sum(1 for c in outside if rid in c["ids"]) / len(outside)
                if k < min_k or p_out == 0:
                    p_out = max(p_out, 0.5 / len(outside))
                if k < min_k:
                    continue
                p = binom_tail(k, len(inside), p_out)
                lift = (k / len(inside)) / p_out
                if p <= max_p and lift >= min_lift:
                    findings.append({"dimension": dim, "segment": seg, "rule_id": rid, "fired": k, "of": len(inside),
                                     "segment_rate": round(k / len(inside), 2), "rest_rate": round(p_out, 2),
                                     "lift": round(lift, 1), "p_value": round(p, 4)})
    return sorted(findings, key=lambda f: f["p_value"])


def rule_fire_rates(cases):
    n = len(cases)
    cnt = Counter(i for c in cases for i in c["ids"])
    return [{"rule_id": k, "fired": v, "rate": round(v / n, 3)} for k, v in sorted(cnt.items())]


def cluster_context():
    cs = _cases()
    return {"n_cases": len(cs), "rule_fire_rates": rule_fire_rates(cs), "cofiring_clusters": cofiring_clusters(cs),
            "abnormal_firing": abnormal_firing(cs),
            "linked_claim_numbers": _links(cs)}


def _links(cs):
    by = {}
    for r in db.rows("SELECT case_id, claim_number FROM cases"):
        by.setdefault(r["claim_number"], []).append(r["case_id"])
    return [{"claim_number": k, "case_ids": v} for k, v in by.items() if len(v) > 1]
