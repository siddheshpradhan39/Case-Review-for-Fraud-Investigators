"""python -m app.evals.run [--perturb] [--shadow]  ->  docs/EVAL.md
Default: invariants + metrics from what is stored. --perturb: live counterfactual tests (temp DB copy).
--shadow: run the ORIGINAL single-judge on all cases (temp DB copy) and compare with the crew (cost + agreement)."""
import asyncio
import json
import os
import sys
from pathlib import Path

from .. import db, service
from . import invariants, metrics, perturb

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "data" / "eval_cache.json"


def _load():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _save(d):
    CACHE.write_text(json.dumps(d, indent=1, default=str))


async def shadow():
    """Single-judge on every case in a temp DB; returns per-case results + cost from its runs table."""
    os.environ["AGENT_MODE"] = "single"
    crew_res = {c["case_id"]: service.latest_assessment(c["case_id"]) for c in db.rows("SELECT case_id FROM cases")}
    with perturb.TempDB():
        service.ingest()
        db.run("DELETE FROM assessments")
        from ..llm import judge
        await judge.assess_all(force=True, concurrency=3)
        single = {c["case_id"]: service.latest_assessment(c["case_id"]) for c in db.rows("SELECT case_id FROM cases")}
        s_cost = metrics.cost_by_lane("single")
    os.environ["AGENT_MODE"] = "crew"
    rows, band_agree, act_agree, deltas = [], 0, 0, []
    for cid, a in crew_res.items():
        s = single.get(cid)
        if not (a and s and a["status"] == "OK" and s["status"] == "OK"):
            continue
        band_agree += a["ai_level"] == s["ai_level"]
        act_agree += a["action"] == s["action"]
        deltas.append(a["ai_score"] - s["ai_score"])
        if a["ai_level"] != s["ai_level"] or a["action"] != s["action"]:
            rows.append({"case": cid, "crew": f"{a['ai_level']} {a['ai_score']} {a['action']}", "single": f"{s['ai_level']} {s['ai_score']} {s['action']}"})
    n = len(deltas)
    return {"n": n, "band_agreement": round(band_agree / max(n, 1), 3), "action_agreement": round(act_agree / max(n, 1), 3),
            "mean_abs_score_delta": round(sum(abs(d) for d in deltas) / max(n, 1), 2), "disagreements": rows, "single_cost": s_cost}


def table(rows, cols):
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] + ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows])


def report(cache):
    inv = invariants.check()
    q, cost, ag = metrics.quality(), metrics.cost_by_lane("crew"), metrics.agreement()
    L = ["# Evaluation report (auto-generated: `python -m app.evals.run [--perturb] [--shadow]`)", "",
         "The data has **no fraud labels**, so nothing here claims accuracy. We evaluate *behaviour*: invariants that must hold whatever the model does, "
         "grounding, cost, agreement/calibration and adversarial counterfactuals.", "",
         f"## 1. Invariants ({sum(i['ok'] for i in inv)}/{len(inv)} pass)", "",
         table([{"check": i["name"], "result": "PASS" if i["ok"] else "**FAIL**", "detail": i["detail"]} for i in inv], ["check", "result", "detail"]), "",
         "## 2. Quality", "", "```", json.dumps(q, indent=1), "```", "",
         "## 3. Cost and latency by lane (latest crew run per case)",
         "Reference $ uses a configurable price table (default fast 0.25/1.25, strong 3/15 per 1M tokens); the free tier itself costs $0.", "",
         table([{"lane": k, **v} for k, v in cost.items() if not k.startswith("_")], ["lane", "cases", "calls_per_case", "tokens_per_case", "est_cost_per_case", "latency_p50_s", "latency_p95_s"]), "",
         f"Queue total: {cost.get('_total')}", "", "## 4. Agreement and calibration", "", "```", json.dumps(ag, indent=1), "```", "",
         "Sanity check on confidence: mean confidence of contested cases should be lower than uncontested ones "
         f"({ag['mean_confidence_contested']} vs {ag['mean_confidence_uncontested']}).", ""]
    L += ["## 4b. Human outcome feedback", "", "```", json.dumps(metrics.feedback(), indent=1), "```", ""]
    if cache.get("perturb"):
        p = cache["perturb"]
        L += [f"## 5. Counterfactual / adversarial tests ({sum(x['ok'] for x in p)}/{len(p)} pass)", "",
              table([{"test": x["name"], "result": "PASS" if x["ok"] else "**FAIL**", "detail": x["detail"]} for x in p], ["test", "result", "detail"]), ""]
    if cache.get("shadow"):
        s = cache["shadow"]
        sc = s["single_cost"].get("SINGLE", {})
        L += ["## 6. Shadow A/B: crew vs original single judge (same 50 cases)", "",
              f"- Band agreement {s['band_agreement']:.0%}, action agreement {s['action_agreement']:.0%}, mean |score delta| {s['mean_abs_score_delta']}",
              f"- Single judge: {s['single_cost'].get('_total')}; per case {sc}",
              f"- Crew: {cost.get('_total')}", "", "Disagreements (each should be reviewed by a human):", "",
              table(s["disagreements"], ["case", "crew", "single"]) if s["disagreements"] else "_none_", ""]
    (ROOT / "docs" / "EVAL.md").write_text("\n".join(L) + "\n")
    return inv


async def main():
    cache = _load()
    if "--perturb" in sys.argv:
        cache["perturb"] = await perturb.run_all()
    if "--shadow" in sys.argv:
        cache["shadow"] = await shadow()
    _save(cache)
    inv = report(cache)
    print("invariants:", sum(i["ok"] for i in inv), "/", len(inv))
    for i in inv:
        print(("PASS " if i["ok"] else "FAIL "), i["name"], "-", i["detail"])
    for p in cache.get("perturb", []):
        print(("PASS " if p["ok"] else "FAIL "), p["name"], "-", p["detail"])
    sys.exit(0 if all(i["ok"] for i in inv) else 1)


if __name__ == "__main__":
    asyncio.run(main())
