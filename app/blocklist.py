"""Blocklist = HARD rules: a case that hits an active entry is declined outright (status DECLINED), bypassing scoring/AI.
Entry kinds (all expressed as validated data, never code):
  claim_number / provider_id / member_id   exact-match lists (provider/member need those columns in the data; the sample CSV has none)
  segment                                   state and/or care_type
  condition                                 the same condition DSL as the Rules tab, but the outcome is DECLINE not a score
  fraud_history                             rules over human-confirmed outcomes: N frauds on the same claim number / provider / member,
                                            N frauds or a fraud RATE (over >= min_cases labelled cases) in a segment
Self is always excluded from history metrics, so a case is never declined because of its own fraud label.
Governance: investigators may only PROPOSE entries (saved paused); a supervisor activates/edits/deletes them."""
import json
from collections import defaultdict

from . import db
from .customrules import RuleError, describe, evaluate_logic, validate_logic

KINDS = ("claim_number", "provider_id", "member_id", "segment", "condition", "fraud_history")
METRICS = {"claim_number_frauds": "confirmed frauds on the same claim number", "provider_frauds": "confirmed frauds on the same provider_id",
           "member_frauds": "confirmed frauds on the same member_id", "segment_fraud_count": "confirmed frauds in the same segment",
           "segment_fraud_rate": "confirmed-fraud rate in the same segment (of labelled cases)"}
DIMS = ("state", "care_type")
STATUSES = ("active", "paused")
BROAD_SHARE = 0.25
ENFORCEABLE = {"NEW", "IN_REVIEW", "ESCALATED", "CONFIRMED"}


def _values(spec):
    v = spec.get("values")
    if not isinstance(v, list) or not v or len(v) > 500 or not all(isinstance(x, str) and 2 <= len(x.strip()) <= 60 for x in v):
        raise RuleError("provide 1-500 values (2-60 characters each)")
    return sorted({x.strip().upper() for x in v})


def validate_spec(kind, spec):
    if kind not in KINDS:
        raise RuleError(f"kind must be one of {KINDS}")
    if not isinstance(spec, dict):
        raise RuleError("spec must be an object")
    if kind in ("claim_number", "provider_id", "member_id"):
        return {"values": _values(spec)}
    if kind == "segment":
        out = {k: (spec.get(k) or "").strip() for k in DIMS if (spec.get(k) or "").strip()}
        if not out:
            raise RuleError("choose a state and/or a care type")
        return out
    if kind == "condition":
        validate_logic(spec.get("logic"))
        return {"logic": spec["logic"]}
    m = spec.get("metric")
    if m not in METRICS:
        raise RuleError(f"metric must be one of {list(METRICS)}")
    t = spec.get("threshold")
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        raise RuleError("threshold must be a number")
    out = {"metric": m, "threshold": t}
    if m == "segment_fraud_rate":
        if not 0 < t <= 1:
            raise RuleError("a fraud-rate threshold must be between 0 and 1 (e.g. 0.3 = 30%)")
        mc = spec.get("min_cases", 5)
        if isinstance(mc, bool) or not isinstance(mc, int) or mc < 1:
            raise RuleError("min_cases must be a whole number >= 1")
        out["min_cases"] = mc
    elif t < 1 or t != int(t):
        raise RuleError("a fraud-count threshold must be a whole number >= 1")
    if m.startswith("segment"):
        dims = spec.get("dims") or ["state", "care_type"]
        if not isinstance(dims, list) or not dims or not set(dims) <= set(DIMS):
            raise RuleError(f"dims must be a non-empty subset of {DIMS}")
        out["dims"] = sorted(set(dims))
    return out


def summarize(kind, spec):
    if kind in ("claim_number", "provider_id", "member_id"):
        return f"{kind.replace('_', ' ')} in [{', '.join(spec['values'][:4])}{', …' if len(spec['values']) > 4 else ''}] ({len(spec['values'])})"
    if kind == "segment":
        return "segment " + " & ".join(f"{k} = {v}" for k, v in spec.items())
    if kind == "condition":
        return describe(spec["logic"])
    if spec["metric"] == "segment_fraud_rate":
        return f"fraud rate ≥ {spec['threshold']:.0%} in same {' + '.join(spec['dims'])} (≥ {spec['min_cases']} labelled cases)"
    tail = f" in same {' + '.join(spec['dims'])}" if "dims" in spec else ""
    return f"≥ {int(spec['threshold'])} {METRICS[spec['metric']]}{tail}"


# ------------------------------------------------------------------ evaluation
def load_cases():
    return [{"case_id": r["case_id"], "status": r["status"], "data": json.loads(r["data"])} for r in db.rows("SELECT case_id, status, data FROM cases")]


def load_outcomes():
    return {r["case_id"]: r["outcome"] for r in db.rows("SELECT case_id, outcome FROM outcomes")}


class _Ctx:
    def __init__(self, cases, outcomes):
        self.outcomes = outcomes
        self.by = {k: defaultdict(list) for k in ("claim_number", "provider_id", "member_id")}
        self.seg = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # dims -> key -> [frauds, labelled]
        for c in cases:
            d = c["data"]
            for k in self.by:
                if d.get(k):
                    self.by[k][str(d[k]).strip().upper()].append(c["case_id"])
        self.cases = cases

    def segment(self, dims):
        key = tuple(dims)
        if key not in self.seg:
            for c in self.cases:
                o = self.outcomes.get(c["case_id"])
                if o:
                    k = tuple(c["data"].get(x) for x in dims)
                    self.seg[key][k][1] += 1
                    self.seg[key][k][0] += o == "FRAUD"
        return self.seg[key]


def _match(entry, c, ctx):
    """-> detail string if this case hits the entry, else None."""
    d, spec, k, cid = c["data"], entry["spec"], entry["kind"], c["case_id"]
    if k in ("claim_number", "provider_id", "member_id"):
        v = str(d.get(k) or "").strip().upper()
        return f"{k.replace('_', ' ')} {v} is on the blocklist" if v and v in spec["values"] else None
    if k == "segment":
        return "case is in blocked segment " + " & ".join(f"{a}={b}" for a, b in spec.items()) if all(d.get(a) == b for a, b in spec.items()) else None
    if k == "condition":
        ok, hits = evaluate_logic(spec["logic"], d)
        return "matches hard condition: " + "; ".join(f"{h['field']}={d.get(h['field'])}" for h in hits) if ok else None
    m = spec["metric"]
    if m in ("claim_number_frauds", "provider_frauds", "member_frauds"):
        field = m.replace("_frauds", "")
        val = str(d.get(field) or "").strip().upper()
        if not val:
            return None
        n = sum(1 for o in ctx.by[field][val] if o != cid and ctx.outcomes.get(o) == "FRAUD")
        return f"{n} other confirmed fraud(s) on {field.replace('_', ' ')} {val} (limit {int(spec['threshold'])})" if n >= spec["threshold"] else None
    dims = spec["dims"]
    frauds, lab = ctx.segment(dims).get(tuple(d.get(x) for x in dims), [0, 0])
    mine = ctx.outcomes.get(cid)
    frauds -= mine == "FRAUD"
    lab -= bool(mine)
    seg = ", ".join(f"{x}={d.get(x)}" for x in dims)
    if m == "segment_fraud_count":
        return f"{frauds} confirmed fraud(s) in segment {seg} (limit {int(spec['threshold'])})" if frauds >= spec["threshold"] else None
    if lab >= spec["min_cases"] and frauds / lab >= spec["threshold"]:
        return f"confirmed-fraud rate {frauds}/{lab} = {frauds / lab:.0%} in segment {seg} (limit {spec['threshold']:.0%}, min {spec['min_cases']} labelled)"
    return None


def evaluate(entries, cases=None, outcomes=None):
    """{case_id: [{entry_id, name, kind, reason, detail}]} for the given entries (any status)."""
    cases = cases if cases is not None else load_cases()
    ctx = _Ctx(cases, outcomes if outcomes is not None else load_outcomes())
    out = defaultdict(list)
    for e in entries:
        for c in cases:
            det = _match(e, c, ctx)
            if det:
                out[c["case_id"]].append({"entry_id": e["id"], "name": e["name"], "kind": e["kind"], "reason": e["reason"], "detail": det})
    return dict(out)


def preview(draft, cases=None):
    """What WOULD be declined if this entry were active (nothing is written)."""
    d = {"id": draft.get("id") or "DRAFT", "name": draft.get("name") or "draft", "reason": draft.get("reason") or "", "kind": draft.get("kind"),
         "spec": validate_spec(draft.get("kind"), draft.get("spec"))}
    cases = cases if cases is not None else load_cases()
    hits = evaluate([d], cases).items()
    byid = {c["case_id"]: c for c in cases}
    return {"count": len(hits), "share": round(len(hits) / max(len(cases), 1), 3), "summary": summarize(d["kind"], d["spec"]),
            "cases": [{"case_id": cid, "status": byid[cid]["status"], "detail": h[0]["detail"], "enforceable": byid[cid]["status"] in ENFORCEABLE,
                       "already_declined": byid[cid]["status"] == "DECLINED"} for cid, h in sorted(hits)],
            "has_ids": {k: sum(1 for c in cases if c["data"].get(k)) for k in ("provider_id", "member_id")}}


# ------------------------------------------------------------------ store
def _row(r):
    r = dict(r)
    r["spec"] = json.loads(r["spec"])
    r["summary"] = summarize(r["kind"], r["spec"])
    return r


def get(bid):
    r = db.one("SELECT * FROM blocklist WHERE id=?", (bid,))
    return _row(r) if r else None


def list_entries(active_only=False):
    q = "SELECT * FROM blocklist" + (" WHERE status='active'" if active_only else "") + " ORDER BY id"
    return [_row(r) for r in db.rows(q)]


def _next_id():
    ids = [int(r["id"][1:]) for r in db.rows("SELECT id FROM blocklist") if r["id"][1:].isdigit()]
    return f"B{(max(ids) + 1 if ids else 1):02d}"


def _validate(d, existing_id=None):
    name = (d.get("name") or "").strip()
    if not 3 <= len(name) <= 80:
        raise RuleError("name must be 3-80 characters")
    dup = db.one("SELECT id FROM blocklist WHERE lower(name)=lower(?)", (name,))
    if dup and dup["id"] != existing_id:
        raise RuleError(f"an entry named '{name}' already exists")
    reason = (d.get("reason") or "").strip()
    if len(reason) < 5:
        raise RuleError("a decline reason is required (it is shown on every declined case)")
    if d.get("status", "paused") not in STATUSES:
        raise RuleError(f"status must be one of {STATUSES}")
    return {"name": name, "kind": d.get("kind"), "spec": validate_spec(d.get("kind"), d.get("spec")), "reason": reason[:300], "status": d.get("status", "paused")}


def _guard_broad(v, confirm_broad):
    if v["status"] == "active":
        p = preview({**v, "id": "DRAFT"})
        if p["share"] > BROAD_SHARE and not confirm_broad:
            raise RuleError(f"BROAD: this entry would decline {p['count']} of {len(load_cases())} cases ({p['share']:.0%}). Confirm to proceed.")


def _version(bid, ver, definition, actor, note):
    db.run("INSERT INTO rule_versions(rule_id,version,definition,changed_by,changed_at,note) VALUES(?,?,?,?,?,?)", (bid, ver, json.dumps(definition), actor, db.now(), note))


def create(d, actor, role, confirm_broad=False):
    v = _validate(d)
    if role != "supervisor":
        v["status"] = "paused"  # investigators propose; a supervisor activates
    _guard_broad(v, confirm_broad)
    bid = _next_id()
    db.run("""INSERT INTO blocklist(id,name,kind,spec,reason,status,created_by,created_at,updated_by,updated_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,1)""",
           (bid, v["name"], v["kind"], json.dumps(v["spec"]), v["reason"], v["status"], actor, db.now(), actor, db.now()))
    _version(bid, 1, v, actor, "created" if v["status"] == "active" else "proposed")
    db.audit(actor, role, "BLOCKLIST_CREATED", None, {"entry_id": bid, "name": v["name"], "kind": v["kind"], "status": v["status"], "summary": summarize(v["kind"], v["spec"])})
    return get(bid)


def update(bid, d, actor, role, confirm_broad=False):
    cur = get(bid)
    if not cur:
        raise RuleError("unknown blocklist entry")
    merged = {**cur, **{k: v for k, v in d.items() if v is not None}}
    v = _validate(merged, existing_id=bid)
    if role != "supervisor" and (cur["status"] == "active" or v["status"] == "active"):
        raise RuleError("only a supervisor can edit an active entry or activate one")
    _guard_broad(v, confirm_broad)
    ver = cur["version"] + 1
    db.run("UPDATE blocklist SET name=?,kind=?,spec=?,reason=?,status=?,updated_by=?,updated_at=?,version=? WHERE id=?",
           (v["name"], v["kind"], json.dumps(v["spec"]), v["reason"], v["status"], actor, db.now(), ver, bid))
    _version(bid, ver, v, actor, "edited")
    db.audit(actor, role, "BLOCKLIST_UPDATED", None, {"entry_id": bid, "version": ver, "status": v["status"], "summary": summarize(v["kind"], v["spec"])})
    return get(bid)


def delete(bid, actor, role):
    if role != "supervisor":
        raise RuleError("only a supervisor can delete a blocklist entry")
    cur = get(bid)
    if not cur:
        raise RuleError("unknown blocklist entry")
    db.run("DELETE FROM blocklist WHERE id=?", (bid,))
    _version(bid, cur["version"] + 1, {"deleted": True, "name": cur["name"]}, actor, "deleted")
    db.audit(actor, role, "BLOCKLIST_DELETED", None, {"entry_id": bid, "name": cur["name"]})


def history(bid):
    return [{**r, "definition": json.loads(r["definition"])} for r in db.rows("SELECT * FROM rule_versions WHERE rule_id=? ORDER BY id DESC", (bid,))]


def stats():
    """Per entry: current hits, cases ever declined, supervisor overrides (a false-positive proxy), and how current hits were labelled."""
    entries = list_entries()
    hits = evaluate([e for e in entries if e["status"] == "active"])
    outcomes = load_outcomes()
    out = {}
    for e in entries:
        cur = [c for c, hs in hits.items() if any(h["entry_id"] == e["id"] for h in hs)]
        ev = db.rows("SELECT event, COUNT(DISTINCT case_id) n FROM blocklist_events WHERE entry_id=? GROUP BY event", (e["id"],))
        evd = {r["event"]: r["n"] for r in ev}
        out[e["id"]] = {"hits_now": len(cur), "hit_cases": sorted(cur), "ever_declined": evd.get("declined", 0), "overridden": evd.get("overridden", 0),
                        "hit_confirmed_fraud": sum(1 for c in cur if outcomes.get(c) == "FRAUD"),
                        "hit_confirmed_legit": sum(1 for c in cur if outcomes.get(c) == "LEGITIMATE")}
    return out
