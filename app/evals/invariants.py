"""Behavioural invariants. There are no fraud labels, so we don't claim accuracy; we assert properties that must hold
regardless of model quality, each derived from the data analysis or from a guardrail promise."""
import json
from collections import Counter

from .. import db, service
from ..agents import router
from ..llm.guardrails import ground_indicator
from ..scoring import level_for

CRITICAL8 = {"C1006", "C1021", "C1024", "C1030", "C1031", "C1034", "C1036", "C1037"}
SILENT = {"C1026", "C1040", "C1042"}
ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _latest():
    out = {}
    for r in db.rows("SELECT case_id FROM cases"):
        a = service.latest_assessment(r["case_id"])
        out[r["case_id"]] = (service.get_case(r["case_id"]), a)
    return out


def check():
    data = _latest()
    res = []

    def add(name, ok, detail=""):
        res.append({"name": name, "ok": bool(ok), "detail": detail})

    ok_all = {k: v for k, v in data.items() if v[1] and v[1]["status"] == "OK"}
    add("All 50 cases have an OK AI assessment", len(ok_all) == 50, f"{len(ok_all)}/50 OK")
    lanes = Counter(router.plan(c).lane for c, _ in data.values())
    add("Router lanes are exactly FAST 29 / OBVIOUS 8 / DEEP 13", lanes == {"FAST": 29, "OBVIOUS": 8, "DEEP": 13}, str(dict(lanes)))
    bad = [k for k in CRITICAL8 if k in ok_all and (ORDER.index(ok_all[k][1]["ai_level"]) < 2 or ok_all[k][1]["action"] == "CLEAR_FALSE_POSITIVE")]
    add("The 8 all-signal cases are never cleared and never below HIGH", not bad, f"violations: {bad}" if bad else "held on all 8")
    clean = [k for k, (c, _) in data.items() if not c["rules"] and not c["data"]["linked_case_ids"]]
    bad = [k for k in clean if k in ok_all and (ok_all[k][1]["action"] in ("ESCALATE_SIU", "PROVIDER_AUDIT") or ORDER.index(ok_all[k][1]["ai_level"]) >= 2)]
    add("No clean case (0 rules, unlinked) is escalated or rated HIGH+", not bad, f"violations: {bad}" if bad else f"held on {len(clean)}")
    bad = [k for k in SILENT if k in ok_all and ok_all[k][1]["action"] == "CLEAR_FALSE_POSITIVE"]
    add("Silent-drift cases (C1026/C1040/C1042) are never cleared", not bad, f"violations: {bad}" if bad else "held on 3")
    if "C1001" in ok_all:
        a = ok_all["C1001"][1]
        add("C1001 (shares claim number with CRITICAL C1031) is not downgraded", a["ai_score"] >= a["rule_score"], f"rule {a['rule_score']} -> AI {a['ai_score']}")
    bad = [k for k, (c, a) in ok_all.items() if a["action"] == "CLEAR_FALSE_POSITIVE" and any(r["level"] in ("HIGH", "CRITICAL") for r in c["rules"])]
    add("CLEAR_FALSE_POSITIVE never recommended where a HIGH/CRITICAL rule fired", not bad, f"violations: {bad}" if bad else "held")
    bad = [k for k, (c, a) in ok_all.items() if abs(a["ai_score"] - a["rule_score"]) > 20.01 or a["ai_level"] != level_for(a["ai_score"])]
    add("AI score within ±20 of rule score and level derived from score", not bad, f"violations: {bad}" if bad else "held on all")
    bad, total = [], 0
    for k, (c, a) in ok_all.items():
        for i in a["result"]["key_indicators"] + a["result"]["mitigating_factors"]:
            total += 1
            ok, why = ground_indicator(i, {**c["data"], **{f: v for r in c["rules"] for f, v in (r.get("fields") or {}).items()}},
                                       {r["rule_id"] for r in c["rules"]}, "\n".join(n["text"] for n in c["notes"]))
            if not ok:
                bad.append((k, i.get("field"), why))
    add("Every stored indicator re-verifies against the record (independent re-check)", not bad, f"{total} checked; violations: {bad[:3]}" if bad else f"{total} checked")
    bad = [k for k, (c, a) in ok_all.items() if not (a["result"].get("summary") or "").strip()]
    add("Every OK assessment has a non-empty summary", not bad, str(bad))
    return res
