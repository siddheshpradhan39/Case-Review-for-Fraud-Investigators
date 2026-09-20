"""Morning briefing: computed queue stats (always real) + an LLM narrative that may only restate those stats."""
import hashlib
import json
import re
from collections import Counter

from . import clusters, db, service
from .llm import client
from .llm.guardrails import ungrounded_figures
from .rules import LEVEL_ORDER

LEVELS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def stats(top_n=8):
    cases = service.list_cases({"sort": "score", "order": "desc"})
    open_cases = [c for c in cases if c["status"] not in service.TERMINAL]
    by_level = {l: {"count": 0, "exposure_usd": 0} for l in LEVELS}
    for c in open_cases:
        by_level[c["level"]]["count"] += 1
        by_level[c["level"]]["exposure_usd"] += c["amount"]
    hist = Counter(min(int(c["score"] // 10), 9) for c in open_cases)
    ai_ok = [c for c in open_cases if c["ai"] and c["ai"]["status"] == "OK"]
    disagree = [c for c in ai_ok if c["ai"]["ai_level"] != c["level"]]
    cl = clusters.cluster_context()
    top = sorted(open_cases, key=lambda c: (-(c["ai"]["ai_score"] if c["ai"] and c["ai"]["ai_score"] is not None else c["score"]), -c["amount"]))[:top_n]
    return {
        "total_cases": len(cases), "open_cases": len(open_cases),
        "status_counts": dict(Counter(c["status"] for c in cases)),
        "risk_counts": by_level,
        "score_histogram": [{"bucket": f"{i*10}-{i*10+9}" if i < 9 else "90-100", "count": hist.get(i, 0)} for i in range(10)],
        "clearable_low_risk": sum(1 for c in open_cases if c["score"] < 10),
        "ai": {"assessed": len(ai_ok), "unavailable": sum(1 for c in open_cases if c["ai"] and c["ai"]["status"] != "OK"),
               "not_run": sum(1 for c in open_cases if not c["ai"]),
               "stale": sum(1 for c in ai_ok if c["ai"]["stale"]),
               "disagree_with_rules": [{"case_id": c["case_id"], "rule_level": c["level"], "ai_level": c["ai"]["ai_level"]}
                                       for c in disagree]},
        "pending_escalations": len(service.list_escalations("PENDING")),
        "declined_by_blocklist": len([d for d in service.declined_cases() if d["enforced"] and d["status"] == "DECLINED"]),
        "blocklist_alerts": len([d for d in service.declined_cases() if not d["enforced"]]),
        "top_cases": [{"case_id": c["case_id"], "care_type": c["care_type"], "state": c["state"], "amount": c["amount"],
                       "rule_score": c["score"], "rule_level": c["level"],
                       "ai_score": c["ai"] and c["ai"]["ai_score"], "ai_level": c["ai"] and c["ai"]["ai_level"],
                       "rules": c["fired_ids"], "status": c["status"], "action": c["ai"] and c["ai"]["action"],
                       "summary": c["ai"] and c["ai"]["summary"]} for c in top],
        "clusters": cl["cofiring_clusters"], "abnormal_firing": cl["abnormal_firing"],
        "rule_fire_rates": cl["rule_fire_rates"], "linked_claim_numbers": cl["linked_claim_numbers"],
    }


def headlines(s):
    """Deterministic one-liners straight from the stats (no LLM) so the briefing is never empty or invented."""
    rc = s["risk_counts"]
    out = [f"{s['open_cases']} open cases: {rc['CRITICAL']['count']} critical, {rc['HIGH']['count']} high, "
           f"{rc['MEDIUM']['count']} medium, {rc['LOW']['count']} low. "
           f"${rc['CRITICAL']['exposure_usd'] + rc['HIGH']['exposure_usd']:,.0f} exposure sits in critical+high."]
    if s["clearable_low_risk"]:
        out.append(f"{s['clearable_low_risk']} cases score below 10 -- candidates for bulk clear (subject to guardrails).")
    for c in s["clusters"][:2]:
        out.append(f"Cluster of {c['size']} cases sharing {', '.join(c['signature_rules'])} (avg score {c['avg_score']}, "
                   f"${c['total_amount']:,.0f}).")
    for f in s["abnormal_firing"][:3]:
        out.append(f"Abnormal firing: {f['rule_id']} fires on {f['fired']}/{f['of']} {f['segment']} cases "
                   f"({f['segment_rate']:.0%} vs {f['rest_rate']:.0%} elsewhere, p={f['p_value']}).")
    for l in s["linked_claim_numbers"]:
        out.append(f"Claim number {l['claim_number']} appears on {', '.join(l['case_ids'])}.")
    if s.get("declined_by_blocklist"):
        out.append(f"{s['declined_by_blocklist']} case(s) auto-declined by the blocklist (hard rules) and excluded from the working queue; a supervisor can override.")
    if s.get("blocklist_alerts"):
        out.append(f"{s['blocklist_alerts']} already-closed case(s) now match a blocklist entry: review them.")
    if s["pending_escalations"]:
        out.append(f"{s['pending_escalations']} escalation(s) awaiting supervisor decision.")
    return out


async def narrative(s, force=False):
    """LLM briefing paragraph. Cached by stats hash. Numbers are verified against the stats."""
    compact = {k: s[k] for k in ("open_cases", "risk_counts", "clearable_low_risk", "pending_escalations", "clusters",
                                  "abnormal_firing", "linked_claim_numbers")}
    compact["top_cases"] = [{k: t[k] for k in ("case_id", "care_type", "amount", "rule_score", "ai_score", "rules", "action")}
                            for t in s["top_cases"][:6]]
    text = json.dumps(compact, default=str)
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    cached = db.one("SELECT v FROM kv WHERE k='briefing_narrative'")
    if cached and not force:
        c = json.loads(cached["v"])
        if c["hash"] == h:
            return c
    msgs = [{"role": "system", "content": "You write the morning briefing for a fraud investigation team lead. Using ONLY "
             "the JSON stats, write 4-6 short bullet lines (plain text, start each with '- '): overall risk picture, the "
             "cases to open first and why, any rule clusters/abnormal firing worth attention, and what can be safely "
             "bulk-cleared. Every number must appear in the JSON. No invention."},
            {"role": "user", "content": text}]
    try:
        msg, model = await client.chat(msgs, max_tokens=700, temperature=0.2)
        out = (msg.get("content") or "").strip()
        bad = ungrounded_figures(out, text)
        res = {"hash": h, "status": "OK", "text": out, "model": model, "unverified_figures": bad}
    except client.LLMError as e:
        res = {"hash": h, "status": "LLM_UNAVAILABLE", "text": None, "error": str(e)[:200]}
    if res["status"] == "OK":
        db.run("INSERT INTO kv(k,v) VALUES('briefing_narrative',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(res),))
    return res
