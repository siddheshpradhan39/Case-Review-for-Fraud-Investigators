"""Cross-case knowledge: which OTHER cases are relevant to this one, and what their notes/decisions say.
Related = cases sharing the claim number (linked) + nearest cases by signal profile. Notes and dispositions on those
cases are precedent context for the agents (never facts about the case under review).
No dependency on service.py so that service.input_hash can use it without an import cycle."""
import json
import math

from . import db
from .features import CONTINUOUS

SIM_FIELDS = CONTINUOUS + ["duplicate_service_billed", "shared_contact_with_provider", "recent_policy_change_flag",
                           "service_overlap_other_provider", "log_amount_robust_z"]
K_SIMILAR = 4
MAX_NOTES = 8        # notes from related cases sent to an agent
MAX_NOTE_CHARS = 350
_cache = None


def reset():
    """Call after ingest (features may have changed)."""
    global _cache
    _cache = None


def _vectors():
    global _cache
    if _cache is None:
        rows = [(r["case_id"], json.loads(r["data"])) for r in db.rows("SELECT case_id, data FROM cases")]
        rng = {f: (min(d[f] for _, d in rows), max(d[f] for _, d in rows)) for f in SIM_FIELDS}
        _cache = {cid: ([(d[f] - rng[f][0]) / ((rng[f][1] - rng[f][0]) or 1) for f in SIM_FIELDS], d.get("linked_case_ids", []))
                  for cid, d in rows}
    return _cache


def nn_threshold():
    """Data-derived 'close enough' distance: the median nearest-neighbour distance across the portfolio."""
    v = _vectors()
    nn = []
    for cid, (vec, _) in v.items():
        nn.append(min(math.dist(vec, o) for c2, (o, _) in v.items() if c2 != cid))
    nn.sort()
    return round(nn[len(nn) // 2], 3)


def outcome_of(case_id):
    return db.one("SELECT outcome, reason, missed, prev_status, rule_level, ai_level, marked_by, marked_at FROM outcomes WHERE case_id=?", (case_id,))


def fraud_lookalikes(case_id):
    """Confirmed-FRAUD cases (human-labelled) that resemble this one: within the median nearest-neighbour distance."""
    fraud = {r["case_id"] for r in db.rows("SELECT case_id FROM outcomes WHERE outcome='FRAUD'")} - {case_id}
    if not fraud:
        return []
    thr = nn_threshold()
    return [(cid, d) for d, cid in similar(case_id, 50) if cid in fraud and d <= thr]


def similar(case_id, k=K_SIMILAR):
    v = _vectors()
    if case_id not in v:
        return []
    me = v[case_id][0]
    return sorted((round(math.dist(me, vec), 3), cid) for cid, (vec, _) in v.items() if cid != case_id)[:k]


def related_ids(case_id, k=K_SIMILAR):
    """[(case_id, relation)] linked first, then nearest similar."""
    v = _vectors()
    out = [(c, "linked") for c in (v.get(case_id, ([], []))[1])]
    seen = {c for c, _ in out}
    out += [(c, "similar") for _, c in similar(case_id, k) if c not in seen]
    return out


def context(case_id, k=K_SIMILAR):
    """Related cases that carry information: notes and/or a human disposition. Used in prompts, hashing and the UI."""
    out, budget = [], MAX_NOTES
    rel_list = related_ids(case_id, k)
    have = {c for c, _ in rel_list}
    rel_list += [(c, "confirmed_fraud_lookalike") for c, _ in fraud_lookalikes(case_id)[:3] if c not in have]
    for cid, rel in rel_list:
        c = db.one("SELECT case_id, level, score, status, care_type FROM cases WHERE case_id=?", (cid,))
        notes = db.rows("SELECT id, author, role, text, created_at FROM notes WHERE case_id=? ORDER BY id", (cid,))
        oc = outcome_of(cid)
        if not notes and c["status"] == "NEW" and not oc:
            continue
        take = notes[-budget:] if budget > 0 else []
        budget -= len(take)
        out.append({"case_id": cid, "relation": rel, "level": c["level"], "score": c["score"], "status": c["status"],
                    "care_type": c["care_type"],
                    "confirmed_outcome": ({"outcome": oc["outcome"], "reason": oc["reason"][:MAX_NOTE_CHARS], "system_had_rated": f"{oc['rule_level']} / AI {oc['ai_level']}",
                                           "missed_by_system": bool(oc["missed"])} if oc else None),
                    "notes": [{"note_id": n["id"], "author": n["author"], "at": n["created_at"], "text": n["text"][:MAX_NOTE_CHARS]} for n in take]})
    return out


def notes_text(ctx):
    """Everything in related context that an agent may legitimately quote (notes + outcome reasons)."""
    return "\n".join([n["text"] for c in ctx for n in c["notes"]] + [c["confirmed_outcome"]["reason"] for c in ctx if c["confirmed_outcome"]])
