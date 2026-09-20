"""Quality / cost / agreement metrics computed from what the system actually stored (assessments, runs, feedback)."""
import json
import statistics as st
from collections import Counter, defaultdict

from .. import db, service


def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


def latest_runs(mode="crew"):
    rows = db.rows("SELECT * FROM runs WHERE mode=? ORDER BY id", (mode,))
    last = {}
    for r in rows:
        last[r["case_id"]] = r
    return last


def quality():
    cases = [r["case_id"] for r in db.rows("SELECT case_id FROM cases")]
    ass = {c: service.latest_assessment(c) for c in cases}
    ok = {c: a for c, a in ass.items() if a and a["status"] == "OK"}
    kept = sum(len(a["result"]["key_indicators"]) + len(a["result"]["mitigating_factors"]) for a in ok.values())
    dropped = sum(1 for a in ok.values() for w in (a["warnings"] or []) if w.startswith("dropped ungrounded"))
    fig = sum(1 for a in ok.values() if any("figures in the narrative" in w for w in (a["warnings"] or [])))
    fb = db.rows("SELECT decision, COUNT(*) n FROM feedback GROUP BY decision")
    return {"cases": len(cases), "assessed_ok": len(ok), "ok_rate": round(len(ok) / len(cases), 3),
            "indicators_kept": kept, "indicators_dropped": dropped,
            "grounding_survival": round(kept / (kept + dropped), 3) if kept + dropped else None,
            "assessments_with_unverified_figures": fig, "human_feedback": {r["decision"]: r["n"] for r in fb}}


def cost_by_lane(mode="crew"):
    by = defaultdict(list)
    for r in latest_runs(mode).values():
        by[r["lane"]].append(r)
    out = {}
    for lane, rs in by.items():
        out[lane] = {"cases": len(rs), "calls_per_case": round(st.mean(r["calls"] for r in rs), 2),
                     "tokens_per_case": int(st.mean(r["tokens_in"] + r["tokens_out"] for r in rs)),
                     "est_cost_per_case": round(st.mean(r["est_cost"] for r in rs), 4),
                     "latency_p50_s": round(_pct([r["latency_ms"] for r in rs], .5) / 1000, 1),
                     "latency_p95_s": round(_pct([r["latency_ms"] for r in rs], .95) / 1000, 1)}
    tot = latest_runs(mode).values()
    out["_total"] = {"cases": len(tot), "calls": round(sum(r["calls"] for r in tot), 1), "tokens": sum(r["tokens_in"] + r["tokens_out"] for r in tot),
                     "est_cost": round(sum(r["est_cost"] for r in tot), 3)}
    return out


def agreement():
    crews = []
    for c in db.rows("SELECT case_id FROM cases"):
        a = service.latest_assessment(c["case_id"])
        if a and a["status"] == "OK" and (a["result"].get("crew") or {}).get("lane"):
            crews.append((c["case_id"], a["result"]["crew"], a))
    deep = [x for x in crews if x[1]["lane"] == "DEEP"]
    panels = [x[1]["panel"] for x in crews if x[1].get("panel")]
    plaus = Counter((x[1]["challenger"] or {}).get("plausibility") for x in deep if x[1].get("challenger"))
    conf_c = [x[1]["confidence_score"] for x in deep if x[1]["contested"] and x[1]["confidence_score"] is not None]
    conf_n = [x[1]["confidence_score"] for x in deep if not x[1]["contested"] and x[1]["confidence_score"] is not None]
    spec = []
    for _, c, _a in deep:
        dirs = [f["direction"] for f in c["findings"].values()]
        if len(dirs) > 1:
            spec.append(dirs.count(max(set(dirs), key=dirs.count)) / len(dirs))
    flips = sum(1 for _, c, a in deep if a["verdict"].startswith("DOWNGRADE") and (c.get("challenger") or {}).get("plausibility") in ("medium", "high"))
    return {"deep_cases": len(deep), "contested": sum(1 for x in crews if x[1]["contested"]),
            "panel_runs": len(panels), "panel_mean_agreement": round(st.mean(p["agreement"] for p in panels), 2) if panels else None,
            "panel_splits": sum(1 for p in panels if p["split"]),
            "specialist_mean_agreement": round(st.mean(spec), 2) if spec else None,
            "challenger_plausibility": dict(plaus),
            "challenger_downgrade_rate": round(flips / len(deep), 2) if deep else None,
            "mean_confidence_contested": round(st.mean(conf_c), 2) if conf_c else None,
            "mean_confidence_uncontested": round(st.mean(conf_n), 2) if conf_n else None,
            "stop_reasons": dict(Counter(r["stop_reason"] for r in latest_runs("crew").values())),
            "reused_specialists": sum(len(x[1].get("reused_specialists") or []) for x in crews)}


def feedback():
    """Human outcome labels and what they did: the closest thing to ground truth this system ever gets."""
    o = db.rows("SELECT case_id, outcome, missed, rule_level, ai_level, prev_status FROM outcomes")
    r17 = [r["case_id"] for r in db.rows("SELECT case_id, rules FROM cases") if '"R17"' in r["rules"]]
    fraud = [x for x in o if x["outcome"] == "FRAUD"]
    return {"outcomes": len(o), "confirmed_fraud": len(fraud), "missed_by_system": sum(x["missed"] for x in fraud),
            "confirmed_legitimate": sum(1 for x in o if x["outcome"] == "LEGITIMATE"), "lookalikes_flagged_R17": len(r17),
            "note": "no outcomes recorded yet" if not o else "each miss is a labelled example for re-fitting thresholds/weights"}
