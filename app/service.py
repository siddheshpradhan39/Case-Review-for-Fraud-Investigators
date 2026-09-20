"""Domain logic: ingest, queries, notes, dispositions, bulk actions, escalation. Everything is audited."""
import hashlib
import json

from . import blocklist, db, related, rulestore
from .calibration import calibrate, load_calibration
from .features import add_derived, load_raw
from .rules import LEVEL_ORDER, evaluate
from .scoring import baseline_anomaly, level_for, score_case

LAST_RESCORED = 0
LAST_BLOCKLIST = {}
TERMINAL = {"CLEARED", "SIU_REFERRED", "FRAUD_CONFIRMED", "DECLINED"}
STATUSES = ["NEW", "IN_REVIEW", "CONFIRMED", "ESCALATED", "CLEARED", "SIU_REFERRED", "FRAUD_CONFIRMED", "DECLINED"]


# ------------------------------------------------------------------ ingest
def ingest():
    """Idempotent: (re)computes features/rules/scores, preserves status/notes/assessments."""
    db.init()
    related.reset()
    cal = calibrate()[0]
    cfg = rulestore.load_config()
    rows = add_derived(load_raw())
    with db.conn() as c:
        for r in rows:
            fired = evaluate(r, cal, cfg)
            an = baseline_anomaly(r, rows)
            score, rule_score, _ = score_case(fired, an)
            data = {k: v for k, v in r.items() if not k.startswith("_")}
            c.execute("""INSERT INTO cases(case_id,claim_number,claim_date,care_type,state,amount,score,rule_score,level,
                         anomaly,data,rules,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(case_id) DO UPDATE SET score=excluded.score, rule_score=excluded.rule_score,
                         level=excluded.level, anomaly=excluded.anomaly, data=excluded.data, rules=excluded.rules""",
                      (r["case_id"], r["claim_number"], r["claim_date"], r["care_type"], r["state"],
                       r["claim_amount_usd"], score, rule_score, level_for(score), an,
                       json.dumps(data), json.dumps(fired), db.now()))
    global LAST_RESCORED, LAST_BLOCKLIST
    LAST_RESCORED = _apply_precedent()
    LAST_BLOCKLIST = apply_blocklist()
    return len(rows)


def _apply_precedent():
    """Pass 2: human outcome labels feed back into scoring. A case that closely resembles a confirmed-fraud case fires R17."""
    from .rules import fire_precedent
    related.reset()
    changed = 0
    if not db.one("SELECT 1 FROM outcomes WHERE outcome='FRAUD'") or not rulestore.load_config().enabled("R17"):
        return 0
    for row in db.rows("SELECT case_id, rules, anomaly, score FROM cases"):
        look = related.fraud_lookalikes(row["case_id"])
        if not look:
            continue
        fired = json.loads(row["rules"]) + [fire_precedent(look)]
        score, rule_score, _ = score_case(fired, row["anomaly"])
        db.run("UPDATE cases SET rules=?, score=?, rule_score=?, level=? WHERE case_id=?",
               (json.dumps(fired), score, rule_score, level_for(score), row["case_id"]))
        changed += score != row["score"]
    return changed


def apply_blocklist():
    """Hard rules. Every case that hits an ACTIVE blocklist entry is declined (status DECLINED, prior status remembered);
    a case that no longer hits any entry is released back to its prior status; an overridden case stays overridden unless a NEW entry hits it.
    Cases already closed (cleared / SIU / confirmed fraud) are never re-opened: they get an alert row (enforced=0) instead."""
    entries = blocklist.list_entries(active_only=True)
    hits = blocklist.evaluate(entries) if entries else {}
    now = db.now()
    result = {"declined": [], "released": [], "alerts": []}
    for c in db.rows("SELECT case_id, status FROM cases"):
        cid, status = c["case_id"], c["status"]
        h = hits.get(cid, [])
        d = db.one("SELECT * FROM declines WHERE case_id=?", (cid,))
        ids = sorted({x["entry_id"] for x in h})
        if not h:
            if d:
                if d["enforced"] and status == "DECLINED" and not d["overridden"]:
                    _set_status(cid, d["prev_status"] or "NEW", "system", "system", "BLOCKLIST_RELEASED", {"reason": "no longer matches any active blocklist entry"})
                    db.run("INSERT INTO blocklist_events(case_id,entry_id,event,ts,actor) VALUES(?,?,?,?,?)", (cid, ",".join(json.loads(d["entry_ids"])), "released", now, "system"))
                    result["released"].append(cid)
                db.run("DELETE FROM declines WHERE case_id=?", (cid,))
            continue
        if d and d["overridden"] and set(ids) <= set(json.loads(d["covered"] or "[]")):
            continue  # a supervisor already reviewed exactly these entries
        if status in blocklist.ENFORCEABLE or status == "DECLINED":
            prev = d["prev_status"] if d and status == "DECLINED" else status
            newly = status != "DECLINED"
            db.run("DELETE FROM declines WHERE case_id=?", (cid,))
            db.run("INSERT INTO declines(case_id,entry_ids,details,prev_status,enforced,declined_at,overridden,covered) VALUES(?,?,?,?,?,?,0,'[]')",
                   (cid, json.dumps(ids), json.dumps(h), prev, 1, now if newly or not d else d["declined_at"]))
            if newly:
                _set_status(cid, "DECLINED", "system", "system", "BLOCKLIST_DECLINED", {"entries": ids, "reasons": [x["detail"] for x in h]})
                for eid in ids:
                    db.run("INSERT INTO blocklist_events(case_id,entry_id,event,ts,actor) VALUES(?,?,?,?,?)", (cid, eid, "declined", now, "system"))
                result["declined"].append(cid)
        else:  # already closed: never silently re-open, but surface it
            if not d:
                db.audit("system", "system", "BLOCKLIST_ALERT", cid, {"entries": ids, "reasons": [x["detail"] for x in h], "status": status})
            db.run("DELETE FROM declines WHERE case_id=?", (cid,))
            db.run("INSERT INTO declines(case_id,entry_ids,details,prev_status,enforced,declined_at,overridden,covered) VALUES(?,?,?,?,?,?,0,'[]')",
                   (cid, json.dumps(ids), json.dumps(h), status, 0, now))
            result["alerts"].append(cid)
    return result


def override_decline(case_id, reason, actor, role):
    """Supervisor overturns an auto-decline. Requires a written reason; the reason becomes a case note (so agents and colleagues
    see it) and counts against the entries that fired (a false-positive signal in the Blocklist tab)."""
    if role != "supervisor":
        raise ValueError("Only a supervisor can override a blocklist decline")
    if not (reason or "").strip():
        raise ValueError("A written reason is required to override a decline")
    d = db.one("SELECT * FROM declines WHERE case_id=? AND enforced=1", (case_id,))
    if not d or db.one("SELECT status FROM cases WHERE case_id=?", (case_id,))["status"] != "DECLINED":
        raise ValueError("This case is not currently declined by the blocklist")
    ids = json.loads(d["entry_ids"])
    db.run("UPDATE declines SET overridden=1, covered=?, override_by=?, override_reason=?, override_at=? WHERE case_id=?", (json.dumps(ids), actor, reason.strip(), db.now(), case_id))
    add_note(case_id, f"[BLOCKLIST DECLINE OVERRIDDEN by {actor}] {reason.strip()}", actor, role)
    for eid in ids:
        db.run("INSERT INTO blocklist_events(case_id,entry_id,event,ts,actor) VALUES(?,?,?,?,?)", (case_id, eid, "overridden", db.now(), actor))
    _set_status(case_id, "IN_REVIEW", actor, role, "BLOCKLIST_OVERRIDDEN", {"entries": ids, "reason": reason.strip(), "was": d["prev_status"]})


def declined_cases():
    out = []
    for d in db.rows("SELECT d.*, c.status, c.care_type, c.state, c.amount, c.score, c.level FROM declines d JOIN cases c USING(case_id) ORDER BY d.declined_at DESC"):
        d["details"] = json.loads(d["details"])
        d["entry_ids"] = json.loads(d["entry_ids"])
        out.append(d)
    return out


def rescore(actor="system"):
    """Re-apply the live rule set to every case. Returns what changed so the UI can show the impact and offer AI re-assessment
    of exactly the cases whose fired rules changed (assessments go stale via input_hash, which covers rules + score)."""
    before = {r["case_id"]: (r["level"], r["score"], r["rules"]) for r in db.rows("SELECT case_id, level, score, rules FROM cases")}
    ingest()
    after = {r["case_id"]: (r["level"], r["score"], r["rules"]) for r in db.rows("SELECT case_id, level, score, rules FROM cases")}
    changes = [{"case_id": c, "from": before[c][0], "to": after[c][0], "score_from": before[c][1], "score_to": after[c][1]}
               for c in after if c in before and (before[c][0] != after[c][0] or before[c][1] != after[c][1])]
    rules_changed = [c for c in after if c in before and before[c][2] != after[c][2]]
    stale = sum(1 for c in rules_changed if (a := latest_assessment(c)) and a["status"] == "OK" and a["input_hash"] != input_hash(c))
    return {"cases": len(after), "rules_changed": len(rules_changed), "level_changes": [x for x in changes if x["from"] != x["to"]],
            "score_changes": len(changes), "ai_stale": stale, "blocklist": LAST_BLOCKLIST}


def mark_outcome(case_id, outcome, reason, actor, role):
    """Post-hoc ground truth from a human (e.g. a claim that went through as low risk / was cleared turns out to be fraud).
    Stored as a labelled outcome (not just a note): future agents see it, lookalikes fire R17, bulk clear refuses them."""
    if outcome not in ("FRAUD", "LEGITIMATE"):
        raise ValueError("outcome must be FRAUD or LEGITIMATE")
    if not (reason or "").strip():
        raise ValueError("Describe what was discovered (required)")
    c = db.one("SELECT status, level, score FROM cases WHERE case_id=?", (case_id,))
    if not c:
        raise ValueError("unknown case")
    a = latest_assessment(case_id)
    ai_level = a["ai_level"] if a and a["status"] == "OK" else None
    ai_action = a["action"] if a and a["status"] == "OK" else None
    missed = int(outcome == "FRAUD" and (c["status"] == "CLEARED" or c["level"] in ("LOW", "MEDIUM") or ai_level in ("LOW", "MEDIUM") or ai_action == "CLEAR_FALSE_POSITIVE"))
    db.run("DELETE FROM outcomes WHERE case_id=?", (case_id,))
    db.run("""INSERT INTO outcomes(case_id,outcome,reason,marked_by,marked_at,prev_status,rule_level,rule_score,ai_level,ai_score,ai_action,missed)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (case_id, outcome, reason.strip(), actor, db.now(), c["status"], c["level"], c["score"], ai_level,
                                                     a["ai_score"] if ai_level else None, ai_action, missed))
    if outcome == "FRAUD":
        _set_status(case_id, "FRAUD_CONFIRMED", actor, role, "STATUS_CHANGE", {"reason": reason})
    db.audit(actor, role, "OUTCOME_MARKED", case_id, {"outcome": outcome, "missed_by_system": bool(missed), "system_had_rated": c["level"], "ai_level": ai_level, "reason": reason})
    ingest()  # re-score everything: lookalikes of a confirmed fraud now fire R17
    return {"missed_by_system": bool(missed), "rescored_cases": LAST_RESCORED,
            "lookalikes_flagged": [r["case_id"] for r in db.rows("SELECT case_id, rules FROM cases") if '"R17"' in r["rules"]]}


def retract_outcome(case_id, actor, role):
    o = db.one("SELECT * FROM outcomes WHERE case_id=?", (case_id,))
    if not o:
        raise ValueError("no outcome recorded on this case")
    db.run("DELETE FROM outcomes WHERE case_id=?", (case_id,))
    if o["outcome"] == "FRAUD":
        _set_status(case_id, o["prev_status"] or "IN_REVIEW", actor, role, "STATUS_CHANGE", {"reason": "fraud outcome retracted"})
    purged = _purge(o["reason"], case_id, o["marked_at"])
    db.audit(actor, role, "OUTCOME_RETRACTED", case_id, {"purged": purged})
    ingest()
    return purged


def _hydrate(row):
    row["data"] = json.loads(row["data"])
    row["rules"] = json.loads(row["rules"])
    return row


def latest_assessment(case_id):
    a = db.one("SELECT * FROM assessments WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,))
    if a:
        for k in ("result", "warnings", "trace"):
            a[k] = json.loads(a[k]) if a[k] else None
    return a


def assessment_history(case_id):
    return [{k: v for k, v in a.items() if k in ("id", "created_at", "status", "model", "ai_score", "ai_level",
                                               "verdict", "action")}
            for a in db.rows("SELECT * FROM assessments WHERE case_id=? ORDER BY id DESC", (case_id,))]


def notes_for(case_id):
    return db.rows("SELECT * FROM notes WHERE case_id=? ORDER BY id", (case_id,))


def feedback_for(case_id):
    return db.rows("SELECT * FROM feedback WHERE case_id=? ORDER BY id", (case_id,))


def input_hash(case_id):
    """Hash of everything the AI reasons over. If it changes (new note, rejected indicator, new status), the
    assessment is stale."""
    c = db.one("SELECT data,rules,score FROM cases WHERE case_id=?", (case_id,))
    payload = [c["data"], c["rules"], c["score"], [(n["id"], n["text"]) for n in notes_for(case_id)],
               [(f["indicator_key"], f["decision"]) for f in feedback_for(case_id)],
               # notes/decisions on related cases are part of what the AI reasons over
               [(c["case_id"], c["status"], [(n["note_id"], n["text"]) for n in c["notes"]]) for c in related.context(case_id)]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def case_summary_row(row):
    """Row for queue table: case + denormalised latest assessment."""
    row = _hydrate(row)
    a = latest_assessment(row["case_id"])
    row["ai"] = None
    if a:
        row["ai"] = {"status": a["status"], "ai_score": a["ai_score"], "ai_level": a["ai_level"],
                     "verdict": a["verdict"], "action": a["action"],
                     "stale": a["input_hash"] != input_hash(row["case_id"]),
                     "summary": (a["result"] or {}).get("summary"),
                     "lane": ((a["result"] or {}).get("crew") or {}).get("lane"),
                     "contested": ((a["result"] or {}).get("crew") or {}).get("contested"),
                     "confidence": ((a["result"] or {}).get("crew") or {}).get("confidence_score")}
    row["fired_ids"] = [r["rule_id"] for r in row["rules"]]
    row["max_rule_level"] = max((LEVEL_ORDER.index(r["level"]) for r in row["rules"]), default=-1)
    row["note_count"] = db.one("SELECT COUNT(*) n FROM notes WHERE case_id=?", (row["case_id"],))["n"]
    return row


def list_cases(f=None):
    f = f or {}
    where, args = [], []
    if f.get("min_score") is not None:
        where.append("COALESCE(score,0) >= ?"); args.append(f["min_score"])
    if f.get("max_score") is not None:
        where.append("score < ?" if f.get("max_exclusive", True) else "score <= ?"); args.append(f["max_score"])
    for key, col in (("levels", "level"), ("care_types", "care_type"), ("states", "state"), ("statuses", "status")):
        if f.get(key):
            where.append(f"{col} IN ({','.join('?' * len(f[key]))})"); args += list(f[key])
    if f.get("q"):
        where.append("(case_id LIKE ? OR claim_number LIKE ?)"); args += [f"%{f['q']}%"] * 2
    sort = {"score": "score", "amount": "amount", "date": "claim_date", "case_id": "case_id"}.get(f.get("sort"), "score")
    order = "ASC" if f.get("order") == "asc" else "DESC"
    rows = db.rows(f"SELECT * FROM cases {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {sort} {order}, amount DESC", args)
    out = [case_summary_row(r) for r in rows]
    if f.get("rule_ids"):
        need = set(f["rule_ids"])
        mode_all = f.get("rule_mode", "any") == "all"
        out = [r for r in out if (need <= set(r["fired_ids"]) if mode_all else need & set(r["fired_ids"]))]
    if f.get("ai_levels"):
        out = [r for r in out if r["ai"] and r["ai"]["ai_level"] in f["ai_levels"]]
    if f.get("ai_disagrees"):
        out = [r for r in out if r["ai"] and r["ai"]["ai_level"] and r["ai"]["ai_level"] != r["level"]]
    return out


def get_case(case_id):
    row = db.one("SELECT * FROM cases WHERE case_id=?", (case_id,))
    if not row:
        return None
    row = case_summary_row(row)
    row["notes"] = notes_for(case_id)
    row["assessment"] = latest_assessment(case_id)
    row["assessment_history"] = assessment_history(case_id)
    row["feedback"] = feedback_for(case_id)
    row["audit"] = [{**a, "detail": json.loads(a["detail"] or "{}")} for a in
                    db.rows("SELECT * FROM audit WHERE case_id=? ORDER BY id DESC LIMIT 100", (case_id,))]
    row["escalations"] = db.rows("SELECT * FROM escalations WHERE case_id=? ORDER BY id DESC", (case_id,))
    row["related_context"] = related.context(case_id)
    row["outcome"] = related.outcome_of(case_id)
    dec = db.one("SELECT * FROM declines WHERE case_id=?", (case_id,))
    if dec:
        dec["details"] = json.loads(dec["details"])
        dec["entry_ids"] = json.loads(dec["entry_ids"])
    row["decline"] = dec
    row["linked"] = [{"case_id": x, **_brief(x)} for x in row["data"].get("linked_case_ids", [])]
    row["input_hash"] = input_hash(case_id)
    return row


def _brief(case_id):
    r = db.one("SELECT case_id,care_type,amount,score,level,status,claim_date FROM cases WHERE case_id=?", (case_id,))
    return r or {}


# ------------------------------------------------------------------ notes / feedback
def add_note(case_id, text, actor, role):
    text = (text or "").strip()
    if not text:
        raise ValueError("Note is empty")
    nid = db.run("INSERT INTO notes(case_id,author,role,text,created_at) VALUES(?,?,?,?,?)",
                 (case_id, actor, role, text, db.now()))
    db.audit(actor, role, "NOTE_ADDED", case_id, {"note_id": nid, "text": text})
    return nid


def _snippet(text):
    return " ".join(text.lower().split())


def _shingles(text, n=4):
    w = "".join(ch if ch.isalnum() or ch in "-' " else " " for ch in text.lower()).split()
    return {" ".join(w[i:i + n]) for i in range(max(1, len(w) - n + 1))} if len(w) >= n else ({" ".join(w)} if len(" ".join(w)) >= 12 else set())


def _norm(t):
    return " ".join("".join(ch if ch.isalnum() or ch in "-' " else " " for ch in (t or "").lower()).split())


def _purge(text, case_id, since, note_id=None):
    """Remove every derived trace of a piece of text:
      * assessments/chat from any case that COULD have seen it (its own case, and every case that had this case as related context)
        created since `since` - this catches paraphrases;
      * any assessment/chat/audit row anywhere that quotes a 4-word phrase of it verbatim;
      * audit rows that captured it are redacted; the cached briefing narrative is dropped."""
    grams = _shingles(text)
    audience = {case_id} | {r["case_id"] for r in db.rows("SELECT case_id FROM cases") if case_id in {c for c, _ in related.related_ids(r["case_id"])}}
    hit = lambda blob: any(g in _norm(blob) for g in grams)
    purged = {"assessments": 0, "chat_messages": 0, "audit_redacted": 0}
    affected = {case_id}
    for a in db.rows("SELECT id, case_id, created_at, result, warnings, trace FROM assessments"):
        if (a["case_id"] in audience and a["created_at"] >= since) or hit(" ".join(str(a[k] or "") for k in ("result", "warnings", "trace"))):
            db.run("DELETE FROM assessments WHERE id=?", (a["id"],))
            db.run("DELETE FROM feedback WHERE assessment_id=?", (a["id"],))
            purged["assessments"] += 1
            affected.add(a["case_id"])
    for m in db.rows("SELECT id, case_id, content, ts FROM chat"):
        if (m["case_id"] in audience and m["ts"] >= since) or hit(m["content"]):
            db.run("DELETE FROM chat WHERE id=?", (m["id"],))
            purged["chat_messages"] += 1
    for r in db.rows("SELECT id, detail FROM audit"):
        d = r["detail"] or ""
        if (note_id is not None and f'"note_id": {note_id}' in d) or hit(d):
            db.run("UPDATE audit SET detail=? WHERE id=?", (json.dumps({"redacted": "content removed"}), r["id"]))
            purged["audit_redacted"] += 1
    db.run("DELETE FROM kv WHERE k='briefing_narrative'")
    purged["affected_cases"] = sorted(affected)
    return purged


def delete_note(case_id, note_id, actor, role):
    """Hard-delete a note AND purge every derived trace so it is gone 'anywhere':
      * the note row (so it leaves the case packet, chat context, get_case_notes, search_notes, related-case context)
      * audit rows that captured its text (redacted; the fact of deletion is itself audited, without the text)
      * AI assessments that could have seen it: same case created at/after the note, or any case whose stored output quotes it
      * chat messages likewise, and the cached briefing narrative
    Returns what was purged; affected cases' assessments are regenerated by the batch."""
    n = db.one("SELECT * FROM notes WHERE id=? AND case_id=?", (note_id, case_id))
    if not n:
        raise ValueError("note not found on this case")
    db.run("DELETE FROM notes WHERE id=?", (note_id,))
    purged = _purge(n["text"], case_id, n["created_at"], note_id)
    db.audit(actor, role, "NOTE_DELETED", case_id, {"note_id": note_id, "note_author": n["author"], "purged": purged})
    return purged


def set_feedback(case_id, assessment_id, key, text, decision, actor, role):
    if decision not in ("ACCEPT", "REJECT", "CLEAR"):
        raise ValueError("decision must be ACCEPT/REJECT/CLEAR")
    db.run("DELETE FROM feedback WHERE case_id=? AND assessment_id=? AND indicator_key=?", (case_id, assessment_id, key))
    if decision != "CLEAR":
        db.run("INSERT INTO feedback(case_id,assessment_id,indicator_key,indicator_text,decision,actor,ts) VALUES(?,?,?,?,?,?,?)",
               (case_id, assessment_id, key, text, decision, actor, db.now()))
    db.audit(actor, role, f"AI_FINDING_{decision}", case_id, {"indicator": key, "text": text})


# ------------------------------------------------------------------ dispositions
def _set_status(case_id, status, actor, role, action, detail):
    prev = db.one("SELECT status FROM cases WHERE case_id=?", (case_id,))["status"]
    db.run("UPDATE cases SET status=?, updated_at=? WHERE case_id=?", (status, db.now(), case_id))
    db.audit(actor, role, action, case_id, {"from": prev, "to": status, **detail})


def _guard_declined(case_id):
    cur = db.one("SELECT status FROM cases WHERE case_id=?", (case_id,))
    if cur and cur["status"] == "DECLINED":
        raise ValueError("Declined by the blocklist. A supervisor must override the decline first.")


def set_status(case_id, status, reason, actor, role):
    _guard_declined(case_id)
    if status == "DECLINED":
        raise ValueError("DECLINED is set only by the blocklist")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    if status in ("CLEARED", "CONFIRMED") and not (reason or "").strip():
        raise ValueError("A reason is required to clear or confirm a case")
    if status == "SIU_REFERRED" and role != "supervisor":
        raise ValueError("Only a supervisor can refer to SIU")
    if reason:
        add_note(case_id, f"[{status}] {reason}", actor, role)
    _set_status(case_id, status, actor, role, "STATUS_CHANGE", {"reason": reason})


def bulk_evaluate(case_ids_or_filter, allow_without_ai=False):
    """Classify a candidate set for bulk clearing. Returns (clearable, blocked[{case,reasons}])."""
    cases = case_ids_or_filter
    clearable, blocked = [], []
    for c in cases:
        reasons = []
        if c["status"] in TERMINAL:
            reasons.append(f"already {c['status']}")
        if any(r["rule_id"] == "R17" for r in c["rules"]):
            reasons.append("lookalike of a human-confirmed fraud case (R17)")
        hi = [r["rule_id"] + ":" + r["level"] for r in c["rules"] if r["level"] in ("HIGH", "CRITICAL")]
        if hi:
            reasons.append("HIGH/CRITICAL rule fired: " + ", ".join(hi))
        ai = c["ai"]
        if ai is None or ai["status"] != "OK":
            if not allow_without_ai:
                reasons.append("no AI second opinion (LLM unavailable / not run)")
        else:
            if ai["ai_level"] in ("MEDIUM", "HIGH", "CRITICAL"):
                reasons.append(f"AI level is {ai['ai_level']}")
            if ai["action"] in ("ESCALATE_SIU", "PROVIDER_AUDIT", "REQUEST_RECORDS", "CONTACT_MEMBER"):
                reasons.append(f"AI recommends {ai['action']}")
            if ai["stale"]:
                reasons.append("notes/feedback changed since the AI review")
        (blocked if reasons else clearable).append({"case": c, "reasons": reasons} if reasons else c)
    return clearable, blocked


def bulk_clear(case_ids, reason, actor, role, allow_without_ai=False):
    if not (reason or "").strip():
        raise ValueError("A reason is required for bulk clear")
    cases = [case_summary_row(db.one("SELECT * FROM cases WHERE case_id=?", (i,))) for i in case_ids]
    ok, blocked = bulk_evaluate(cases, allow_without_ai)
    for c in ok:
        ai = c["ai"]
        _set_status(c["case_id"], "CLEARED", actor, role, "BULK_CLEAR",
                    {"reason": reason, "score": c["score"], "rule_level": c["level"],
                     "ai_reviewed": bool(ai and ai["status"] == "OK"), "ai_level": ai and ai["ai_level"]})
        db.run("INSERT INTO notes(case_id,author,role,text,created_at) VALUES(?,?,?,?,?)",
               (c["case_id"], actor, role, f"[BULK CLEARED] {reason}", db.now()))
    return {"cleared": [c["case_id"] for c in ok],
            "blocked": [{"case_id": b["case"]["case_id"], "reasons": b["reasons"]} for b in blocked]}


# ------------------------------------------------------------------ escalation
def escalate(case_id, reason, summary, actor, role):
    _guard_declined(case_id)
    if not (reason or "").strip():
        raise ValueError("A reason is required to escalate")
    eid = db.run("INSERT INTO escalations(case_id,from_user,reason,summary,created_at) VALUES(?,?,?,?,?)",
                 (case_id, actor, reason, summary or "", db.now()))
    add_note(case_id, f"[ESCALATED to supervisor] {reason}", actor, role)
    _set_status(case_id, "ESCALATED", actor, role, "ESCALATED", {"reason": reason, "escalation_id": eid})
    return eid


def decide_escalation(eid, decision, note, actor, role):
    if role != "supervisor":
        raise ValueError("Only a supervisor can decide escalations")
    e = db.one("SELECT * FROM escalations WHERE id=?", (eid,))
    if not e or e["status"] != "PENDING":
        raise ValueError("Escalation not found or already decided")
    if decision == "APPROVE":
        status, new = "APPROVED", "SIU_REFERRED"
    elif decision == "RETURN":
        if not (note or "").strip():
            raise ValueError("A note is required when returning a case")
        status, new = "RETURNED", "IN_REVIEW"
    else:
        raise ValueError("decision must be APPROVE or RETURN")
    db.run("UPDATE escalations SET status=?,decided_by=?,decision_note=?,decided_at=? WHERE id=?",
           (status, actor, note, db.now(), eid))
    if note:
        add_note(e["case_id"], f"[SUPERVISOR {decision}] {note}", actor, role)
    _set_status(e["case_id"], new, actor, role, f"ESCALATION_{status}", {"escalation_id": eid, "note": note})


def list_escalations(status=None):
    q = "SELECT e.*, c.care_type, c.amount, c.score, c.level FROM escalations e JOIN cases c ON c.case_id=e.case_id"
    args = ()
    if status:
        q += " WHERE e.status=?"; args = (status,)
    return db.rows(q + " ORDER BY e.id DESC", args)


def audit_log(limit=500, case_id=None):
    q, a = "SELECT * FROM audit", ()
    if case_id:
        q += " WHERE case_id=?"; a = (case_id,)
    return db.rows(q + " ORDER BY id DESC LIMIT ?", a + (limit,))
