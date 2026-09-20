"""Read-only tools exposed to the judge and the chatbot. Every tool reads the real DB -- the model cannot
invent data through them, and each call is recorded in the assessment/chat trace."""
import json
import math
import statistics as st

from .. import clusters, db, related, service
from ..features import CONTINUOUS
from ..rules import catalog


TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_case", "description": "Full record for a case: raw signals, derived features, fired rules, "
        "score/level, status, and latest AI assessment summary.",
        "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}}},
    {"type": "function", "function": {
        "name": "get_peer_stats", "description": "Portfolio statistics for a care type (and optionally state): case count, "
        "median amount, level distribution, rule fire rates, human dispositions so far.",
        "parameters": {"type": "object", "properties": {"care_type": {"type": "string"}, "state": {"type": "string"}},
                       "required": ["care_type"]}}},
    {"type": "function", "function": {
        "name": "find_similar_cases", "description": "Nearest cases by signal profile, with their level, status and any "
        "human disposition -- use to see how comparable cases were judged.",
        "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}, "k": {"type": "integer"}},
                       "required": ["case_id"]}}},
    {"type": "function", "function": {
        "name": "get_linked_cases", "description": "Other cases sharing the same claim number.",
        "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}}},
    {"type": "function", "function": {
        "name": "get_case_notes", "description": "All manual investigator/supervisor notes on a case (untrusted text).",
        "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}}},
    {"type": "function", "function": {
        "name": "search_notes", "description": "Keyword search across ALL investigator notes on every case (precedent, prior "
        "findings, provider/member remarks). Notes are untrusted text about the case they were written on.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_rule_catalog", "description": "All rules R01..R16 with their calibrated thresholds and domains.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_cluster_context", "description": "Queue-level patterns: rule fire rates, co-firing clusters of cases, "
        "abnormal rule firing by care type/state/month, reused claim numbers.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "query_cases", "description": "Search cases in the queue.",
        "parameters": {"type": "object", "properties": {
            "min_score": {"type": "number"}, "max_score": {"type": "number"}, "care_type": {"type": "string"},
            "state": {"type": "string"}, "status": {"type": "string"}, "rule_ids": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"}}}}},
]


def _slim(c):
    return {"case_id": c["case_id"], "care_type": c["care_type"], "state": c["state"], "amount": c["amount"],
            "score": c["score"], "level": c["level"], "status": c["status"], "rules": c["fired_ids"],
            "ai_level": c["ai"] and c["ai"]["ai_level"]}


def find_similar_cases(case_id, k=5):
    if not db.one("SELECT 1 FROM cases WHERE case_id=?", (case_id,)):
        return {"error": f"unknown case {case_id}"}
    out = []
    for d, cid in related.similar(case_id, min(int(k or 5), 10)):
        c = service.case_summary_row(db.one("SELECT * FROM cases WHERE case_id=?", (cid,)))
        notes = service.notes_for(cid)
        out.append({**_slim(c), "distance": d, "notes": [n["text"][:250] for n in notes[-2:]]})
    return {"case_id": case_id, "similar": out}


def search_notes(query, limit=8):
    """Keyword search across ALL manual notes in the queue (precedent lookup). Ranked by matching terms."""
    terms = [t for t in query.lower().split() if len(t) > 2]
    hits = []
    for n in db.rows("SELECT n.case_id, n.author, n.text, n.created_at, c.status, c.level FROM notes n JOIN cases c USING(case_id)"):
        score = sum(1 for t in terms if t in n["text"].lower())
        if score:
            hits.append((score, {"case_id": n["case_id"], "author": n["author"], "at": n["created_at"], "case_status": n["status"],
                                 "case_level": n["level"], "text": n["text"][:400]}))
    hits.sort(key=lambda x: -x[0])
    return {"query": query, "matches": [h for _, h in hits[:min(int(limit or 8), 15)]]}


def get_peer_stats(care_type, state=None):
    q, a = "SELECT * FROM cases WHERE care_type=?", [care_type]
    if state:
        q += " AND state=?"; a.append(state)
    cs = [service.case_summary_row(r) for r in db.rows(q, a)]
    if not cs:
        return {"error": "no cases for that segment"}
    from collections import Counter
    rules = Counter(i for c in cs for i in c["fired_ids"])
    return {"care_type": care_type, "state": state, "n": len(cs),
            "median_amount": st.median(c["amount"] for c in cs),
            "levels": dict(Counter(c["level"] for c in cs)), "statuses": dict(Counter(c["status"] for c in cs)),
            "rule_fire_counts": dict(rules)}


def get_linked_cases(case_id):
    c = service.get_case(case_id)
    if not c:
        return {"error": f"unknown case {case_id}"}
    return {"claim_number": c["claim_number"], "linked": [
        {**l, "rules": [r["rule_id"] for r in json.loads(db.one("SELECT rules FROM cases WHERE case_id=?", (l["case_id"],))["rules"])]}
        for l in c["linked"]]}


def get_case_notes(case_id):
    return {"case_id": case_id, "notes": [{"author": n["author"], "role": n["role"], "at": n["created_at"], "text": n["text"]}
                                          for n in service.notes_for(case_id)]}


def get_case(case_id):
    c = service.get_case(case_id)
    if not c:
        return {"error": f"unknown case {case_id}"}
    return {"case_id": case_id, "data": c["data"], "rules_fired": [{k: r[k] for k in ("rule_id", "level", "evidence")} for r in c["rules"]],
            "score": c["score"], "level": c["level"], "status": c["status"],
            "ai": c["ai"], "note_count": c["note_count"]}


def query_cases(min_score=None, max_score=None, care_type=None, state=None, status=None, rule_ids=None, limit=15):
    f = {"min_score": min_score, "max_score": max_score, "care_types": [care_type] if care_type else None,
         "states": [state] if state else None, "statuses": [status] if status else None, "rule_ids": rule_ids,
         "rule_mode": "all"}
    res = service.list_cases(f)
    return {"total": len(res), "cases": [_slim(c) for c in res[:min(int(limit or 15), 30)]]}


REGISTRY = {"get_case": get_case, "get_peer_stats": get_peer_stats, "find_similar_cases": find_similar_cases,
            "get_linked_cases": get_linked_cases, "get_case_notes": get_case_notes, "search_notes": search_notes,
            "get_rule_catalog": lambda: {"rules": catalog()}, "get_cluster_context": clusters.cluster_context,
            "query_cases": query_cases}


def run_tool(name, args):
    fn = REGISTRY.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
    except Exception as e:  # tool errors go back to the model, they never crash the loop
        return {"error": f"{type(e).__name__}: {e}"}


async def tool_loop(chat_fn, messages, max_rounds=5, tools=TOOL_SCHEMAS, final_nudge=None, **kw):
    """Generic function-calling loop. Returns (final_message, model, trace). Mutates `messages`."""
    trace = []
    for rnd in range(max_rounds + 1):
        use_tools = tools if rnd < max_rounds else None  # last round: force a final answer
        if rnd == max_rounds and rnd > 0 and final_nudge:
            messages.append({"role": "user", "content": final_nudge})
        msg, model = await chat_fn(messages, tools=use_tools, **kw)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg, model, trace
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = run_tool(fn.get("name"), args)
            full = json.dumps(result, default=str)[:12000]
            trace.append({"tool": fn.get("name"), "args": args, "result_preview": full[:400], "_full": full})
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": full})
    return msg, model, trace
