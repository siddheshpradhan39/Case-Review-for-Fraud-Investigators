"""Auto-rule suggestion: turn a freshly confirmed FRAUD outcome into candidate detection rules.

WHY THIS EXISTS
When an investigator confirms that a case they had cleared (or rated low) actually was fraud,
that is the one moment real ground truth enters the system. Today that only fires rule R17
(a fixed nearest-neighbour lookalike check). It never asks "what, specifically, made this case
different from the queue around it, and should that become a standing rule?" This module does.

METHOD (the same family used for real fraud-rule mining, not invented for this repo):
  1. CANDIDATE GENERATION. For each field, build the tightest single condition that (a) is true
     for the confirmed-fraud case and (b) is already a calibrated elevated/extreme threshold where
     one exists (app.calibration), otherwise a threshold rounded just short of the case's own value
     ("box search" / bump-hunting, the PRIM method: shrink toward the case, never past it).
  2. CONTRAST TEST. Score each condition by how rare it is across the REST of the queue (every case
     that is not this confirmed fraud) using the exact binomial tail test already used for
     "abnormal firing" (app.clusters.binom_tail) — the same statistic, reused, not reinvented.
  3. SEQUENTIAL COVERING. Greedily AND-combine the strongest single conditions from DIFFERENT
     fields into 2-condition rules (RIPPER-style), keeping a combination only if it is rarer in
     the background than either condition alone and still covers every fraud case being mined.
  4. NOVELTY CHECK. Drop any candidate whose fired-case-set is near-identical (Jaccard) to an
     already-ACTIVE rule — do not suggest what the system already catches.
  5. BACKTEST. Every surviving candidate is backtested against REAL confirmed outcomes
     (app.backtest.backtest_rule, label_mode="outcomes") before it is ever saved.
  6. SAVE AS SHADOW, NEVER ACTIVE. Saved through the normal rulestore.create() path with
     status="shadow": it scores silently and changes nothing in the live queue. A human promotes
     it (the same PUT /api/rules/{id} status=active any custom rule already uses).
  7. LLM NAMING IS COSMETIC AND OPTIONAL. A background step (polish_names) asks the model for a
     name and one-sentence description ONLY — it is never shown the ability to change the logic,
     and if the model is unavailable the deterministic name/description already saved in step 6
     stands unchanged. The pattern-finding itself needs no model and never blocks on one.

Nothing here can make a rule live without a human. That governance mirrors the Blocklist and the
manual Rules tab: an AI may only PROPOSE.
"""
import json
import math
import statistics as st

from . import calibration, customrules, db, rules, rulestore
from .clusters import binom_tail
from .features import DOMAINS

ORIGIN = "ai_outcome_mining"
MAX_P = 0.30            # background fire-rate ceiling for a single-case seed: "fires on <30% of the rest of the queue"
MIN_LIFT = 1.5
TOP_SINGLES = 6          # how many single conditions feed the pairing step
MAX_SUGGESTIONS = 3       # candidates saved per mining run
DEDUP_JACCARD = 0.85     # a candidate this similar to an existing ACTIVE rule's fired-set is redundant

NUMERIC_FIELDS = [f for f, (t, _) in customrules.FIELDS.items() if t == "number"]
FLAG_FIELDS = [f for f, (t, _) in customrules.FIELDS.items() if t == "flag"]
# `state` is deliberately excluded: a 19-way field on 50 cases has states with 1-2 cases each, so almost
# any single-case field looks "rare" against it (spurious rarity, not signal) -- and geography as a
# standalone category is exactly the fairness risk already flagged for the deterministic engine (it can
# proxy for rural/urban or provider-network effects). None of the built-in rules key on raw state either;
# the miner follows the same precedent. care_type is kept: 5 values, 8-13 cases each, a real operational
# dimension, not an identifier-like field.
CATEGORY_FIELDS = [f for f, (t, _) in customrules.FIELDS.items() if t == "category" and f != "state"]
MIN_CATEGORY_N = 4   # defense in depth: any categorical candidate still needs this many cases portfolio-wide
FIELD_DOMAIN = {f: dom for dom, fs in DOMAINS.items() for f in fs}

# built-in rules already cover these at their calibrated thresholds; reuse those thresholds as
# candidates (well-understood, already vetted) instead of inventing new ones where one already exists
CALIBRATED_SIGNAL = {"weekly_visit_frequency", "member_provider_distance_miles", "prior_claims_last_12mo",
                     "weekend_billing_ratio", "amount_vs_peer_avg_pct", "round_dollar_billing_ratio",
                     "implied_weekly_travel_miles"}


def _round_toward(v, direction):
    """Tidy threshold that still keeps `v` on the satisfying side: floor toward v for >=, ceil for <=."""
    if v == 0:
        return 0.0
    mag = abs(v)
    step = 10 if mag >= 100 else (1 if mag >= 10 else (0.1 if mag >= 1 else 0.01))
    f = math.floor(v / step) * step if direction == ">=" else math.ceil(v / step) * step
    return round(f, 2)


def _population():
    """Every case's feature dict (same source scoring uses) plus its confirmed outcome, if any."""
    cases = {r["case_id"]: json.loads(r["data"]) for r in db.rows("SELECT case_id, data FROM cases")}
    outcomes = {r["case_id"]: r["outcome"] for r in db.rows("SELECT case_id, outcome FROM outcomes")}
    return cases, outcomes


def _numeric_candidates(field, fraud_values, background_values, cal_signals):
    """One or two candidate (field, cmp, value) tuples that fire on every case in fraud_values."""
    v = fraud_values[0]  # mining is seeded from one newly-confirmed case at a time
    med = st.median(background_values + fraud_values) if background_values else v
    direction = ">=" if v >= med else "<="
    cmp_ = direction
    out = []
    if field in CALIBRATED_SIGNAL and field in cal_signals:
        th = cal_signals[field]
        for key, kind in (("elevated_threshold", "calibrated"), ("extreme_threshold", "calibrated")):
            t = th[key]
            if (direction == ">=" and t <= v) or (direction == "<=" and t >= v):
                out.append((cmp_, t, kind))
    out.append((cmp_, _round_toward(v, direction), "exploratory"))
    # de-dup identical thresholds, keep calibrated ones first (they are more defensible)
    seen, uniq = set(), []
    for cmp_v, t, kind in out:
        if t not in seen:
            seen.add(t)
            uniq.append((cmp_v, t, kind))
    return uniq


def _score(field, cmp_, value, fraud_ids, cases, outcomes):
    """Exact-binomial contrast: how much rarer is this condition among the fraud case(s) than
    among the rest of the queue. Reuses clusters.binom_tail — the same test used for abnormal firing."""
    cond = {"field": field, "cmp": cmp_, "value": value}
    wrapped = {"op": "AND", "conds": [cond]}  # public evaluate_logic, not the private single-condition tester
    bg_ids = [cid for cid in cases if cid not in fraud_ids]
    k = sum(1 for cid in fraud_ids if customrules.evaluate_logic(wrapped, cases[cid])[0])
    n = len(fraud_ids)
    bg_hits = sum(1 for cid in bg_ids if customrules.evaluate_logic(wrapped, cases[cid])[0])
    p_bg = bg_hits / len(bg_ids) if bg_ids else 0.0
    p_bg_floor = max(p_bg, 0.5 / len(bg_ids)) if bg_ids else 1.0  # avoid a divide-by-zero "infinite lift"
    p_value = binom_tail(k, n, p_bg_floor)
    lift = (k / n) / p_bg_floor if n else 0
    return {"cond": cond, "k": k, "n": n, "background_hits": bg_hits, "background_n": len(bg_ids),
            "background_rate": round(p_bg, 4), "lift": round(lift, 2), "p_value": round(p_value, 4)}


def _level_for(p_bg):
    return "HIGH" if p_bg <= 0.10 else "MEDIUM"


def _existing_active_firesets(cases):
    """case_id fired by each currently ACTIVE rule (built-in + custom) — the novelty check compares against this."""
    cal = calibration.load_calibration()
    cfg = rulestore.load_config()
    by_rule = {}
    for cid, c in cases.items():
        for r in rules.evaluate(dict(c), cal, cfg):
            by_rule.setdefault(r["rule_id"], set()).add(cid)
    return by_rule


def _jaccard(a, b):
    return len(a & b) / len(a | b) if (a or b) else 0.0


def _fired_set(logic, cases):
    return {cid for cid, c in cases.items() if customrules.evaluate_logic(logic, c)[0]}


def mine(case_id):
    """Pure and deterministic: returns candidate rule dicts for one confirmed-FRAUD case. Saves nothing."""
    cases, outcomes = _population()
    if case_id not in cases or outcomes.get(case_id) != "FRAUD":
        return []
    fraud_ids = [cid for cid, o in outcomes.items() if o == "FRAUD"]
    target = cases[case_id]
    cal = calibration.load_calibration()["signals"]

    singles = []
    for f in NUMERIC_FIELDS:
        bg_vals = [cases[cid][f] for cid in cases if cid not in fraud_ids and f in cases[cid]]
        for cmp_, v, kind in _numeric_candidates(f, [target[f]], bg_vals, cal):
            s = _score(f, cmp_, v, fraud_ids, cases, outcomes)
            if s["n"] == s["k"] and s["p_value"] <= MAX_P and s["lift"] >= MIN_LIFT:
                singles.append({**s, "field": f, "kind": kind})
    for f in FLAG_FIELDS:
        if target.get(f) == 1:
            s = _score(f, "==", 1, fraud_ids, cases, outcomes)
            if s["n"] == s["k"] and s["p_value"] <= MAX_P and s["lift"] >= MIN_LIFT:
                singles.append({**s, "field": f, "kind": "flag"})
    for f in CATEGORY_FIELDS:
        v = target.get(f)
        if v is not None and sum(1 for cid in cases if cases[cid].get(f) == v) >= MIN_CATEGORY_N:
            s = _score(f, "==", v, fraud_ids, cases, outcomes)
            if s["n"] == s["k"] and s["p_value"] <= MAX_P and s["lift"] >= MIN_LIFT:
                singles.append({**s, "field": f, "kind": "category"})

    singles.sort(key=lambda s: s["background_rate"])
    top = singles[:TOP_SINGLES]

    combos = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b = top[i], top[j]
            if a["field"] == b["field"]:
                continue
            logic = {"op": "AND", "conds": [{"field": a["field"], "cmp": a["cond"]["cmp"], "value": a["cond"]["value"]},
                                            {"field": b["field"], "cmp": b["cond"]["cmp"], "value": b["cond"]["value"]}]}
            fired = _fired_set(logic, cases)
            k = len(fired & set(fraud_ids))
            if k < len(fraud_ids):  # must not lose recall on the fraud cases already known
                continue
            bg_ids = [cid for cid in cases if cid not in fraud_ids]
            bg_hits = len(fired & set(bg_ids))
            p_bg = bg_hits / len(bg_ids) if bg_ids else 0.0
            if p_bg >= min(a["background_rate"], b["background_rate"]):  # must genuinely narrow, not just repeat
                continue
            p_bg_floor = max(p_bg, 0.5 / len(bg_ids)) if bg_ids else 1.0
            p_value = binom_tail(k, len(fraud_ids), p_bg_floor)
            combos.append({"logic": logic, "fields": [a["field"], b["field"]], "background_rate": round(p_bg, 4),
                           "background_n": len(bg_ids), "background_hits": bg_hits, "k": k, "n": len(fraud_ids),
                           "p_value": round(p_value, 4), "lift": round((k / len(fraud_ids)) / p_bg_floor, 2),
                           "kind": "combo"})
    combos.sort(key=lambda c: c["background_rate"])

    candidates = []
    seen_desc = set()
    for s in top[:3]:
        logic = {"op": "AND", "conds": [{"field": s["field"], "cmp": s["cond"]["cmp"], "value": s["cond"]["value"]}]}
        candidates.append({"logic": logic, "fields": [s["field"]], **{k: s[k] for k in
                           ("k", "n", "background_rate", "background_n", "background_hits", "lift", "p_value", "kind")}})
    candidates += combos[:3]
    candidates.sort(key=lambda c: c["background_rate"])

    active_firesets = _existing_active_firesets(cases)
    out = []
    for c in candidates:
        desc = customrules.describe(c["logic"])
        if desc in seen_desc:
            continue
        fired = _fired_set(c["logic"], cases)
        if any(_jaccard(fired, s) >= DEDUP_JACCARD for s in active_firesets.values()):
            continue  # an active rule already catches essentially this same set of cases
        seen_desc.add(desc)
        domains = sorted({FIELD_DOMAIN.get(f, "Custom") for f in c["fields"]})
        domain = domains[0] if len(domains) == 1 else "Custom"
        labels = [customrules.FIELDS[f][1] for f in c["fields"]]
        out.append({
            "name": (" + ".join(labels) + " (mined)")[:80],
            "description": (f"Auto-suggested from case {case_id}, confirmed FRAUD. Condition: {desc}. "
                            f"Fires on {c['k']}/{c['n']} confirmed fraud case(s) and "
                            f"{c['background_rate']:.0%} of the rest of the queue (lift {c['lift']}x).")[:400],
            "domain": domain, "level": _level_for(c["background_rate"]), "logic": c["logic"],
            "stats": {"source_case_id": case_id, "k_fraud": c["k"], "n_fraud": c["n"],
                      "background_rate": c["background_rate"], "background_n": c["background_n"],
                      "background_hits": c["background_hits"], "lift": c["lift"], "p_value": c["p_value"],
                      "kind": c["kind"], "fields": c["fields"]},
        })
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def mine_and_save(case_id, actor="AI rule miner", role="investigator"):
    """mine(), backtest each survivor against real confirmed outcomes, save as shadow. Fully synchronous —
    no network call, safe to run inline inside mark_outcome(). Returns the saved rule dicts."""
    from . import backtest  # local import: backtest.py imports service.py, so this stays deferred to avoid a cycle
    candidates = mine(case_id)
    saved = []
    for c in candidates:
        try:
            bt = backtest.backtest_rule(None, draft={"name": c["name"], "level": c["level"], "domain": c["domain"], "logic": c["logic"]},
                                        label_mode="outcomes", positive_at="HIGH", with_impact=False)
        except Exception:
            bt = None
        stats = {**c["stats"], "backtest": {k: bt[k] for k in ("n", "positives", "precision", "recall", "precision_ci", "recall_ci")} if bt else None}
        rule = rulestore.create({"name": c["name"], "description": c["description"], "level": c["level"], "domain": c["domain"],
                                 "status": "shadow", "logic": c["logic"]}, actor, role, origin=ORIGIN, source_case_id=case_id, mined_stats=stats)
        saved.append(rule)
    return saved


def discard_from_case(case_id):
    """Called when a FRAUD outcome is retracted: remove any not-yet-promoted (still shadow) suggestions it
    spawned. A suggestion a human already activated is left alone — that decision stands."""
    removed = []
    for r in db.rows("SELECT id FROM custom_rules WHERE origin=? AND source_case_id=? AND status='shadow'", (ORIGIN, case_id)):
        rulestore.delete(r["id"], "AI rule miner", "investigator")
        removed.append(r["id"])
    return removed


async def polish_names(rule_ids):
    """Optional, cosmetic, best-effort: ask the LLM for a nicer name/description for each rule.
    Never touches the logic. On any failure (no key, timeout, malformed reply) the rule keeps the
    deterministic name mine_and_save() already gave it — the suggestion is already fully usable."""
    from .llm import rule_draft
    for rid in rule_ids:
        r = rulestore.get(rid)
        if not r or r.get("origin") != ORIGIN:
            continue
        try:
            polished = await rule_draft.name_mined_rule(r["logic"], r.get("mined_stats") or {}, r.get("source_case_id"))
            rulestore.update(rid, {"name": polished["name"], "description": polished["description"]}, "AI rule miner", "investigator")
        except Exception:
            continue
