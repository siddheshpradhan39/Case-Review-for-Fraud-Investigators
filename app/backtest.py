"""Rule backtesting: precision / recall of a rule against labels, entirely in memory (no DB writes).

There are no fraud labels yet, so the default labels are SYNTHETIC and derived from the calculated risk level:
  loo      (default) label = the case's risk level recomputed WITHOUT the rule under test, so a rule is never scored
           against itself. Measures agreement with the REST of the system, not fraud.
  ai       label = the AI-judged level (latest OK assessment). More independent of any single rule.
  outcomes label = human-confirmed FRAUD / LEGITIMATE (the real labels; only cases that have one)
  blend    a confirmed outcome where one exists, otherwise the loo label
Everything reports n and Wilson 95% intervals: with 50 cases a bare percentage would mislead."""
import json
import math

from . import db, related, rulestore, service
from .calibration import load_calibration
from .customrules import LEVELS, RuleConfig
from .rules import RULE_META, evaluate, fire_precedent
from .scoring import baseline_anomaly, level_for, score_case

CUT = {"LOW": 0, "MEDIUM": 25, "HIGH": 50, "CRITICAL": 80}
RANK = {l: i for i, l in enumerate(LEVELS)}
MODES = ("loo", "ai", "outcomes", "blend")
WARN = {
    "loo": "Synthetic labels: 'positive' = the system's risk level WITHOUT this rule is at or above the chosen level. Precision/recall here measure agreement with the rest of the system, not fraud.",
    "ai": "Synthetic labels: 'positive' = the AI-judged level. Only cases with an AI review are used. Measures agreement with the AI, not fraud.",
    "outcomes": "Real labels: only cases an investigator confirmed as FRAUD or LEGITIMATE are used.",
    "blend": "Confirmed outcomes where they exist, otherwise the synthetic leave-one-out label.",
}


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0, c - h), 3), round(min(1, c + h), 3)]


def _cases():
    return [(r["case_id"], json.loads(r["data"]), r["anomaly"]) for r in db.rows("SELECT case_id, data, anomaly FROM cases ORDER BY case_id")]


def _lookalikes(cases):
    if not db.one("SELECT 1 FROM outcomes WHERE outcome='FRAUD'"):
        return {}
    return {cid: related.fraud_lookalikes(cid) for cid, _, _ in cases}


def score_all(cfg, cases, look):
    """case_id -> {fired: {rule_id: level}, score, level}. Same evaluate + R17 + noisy-OR path as production ingest."""
    cal = load_calibration()
    out = {}
    for cid, data, an in cases:
        fired = evaluate(dict(data), cal, cfg)
        if cfg.enabled("R17") and look.get(cid):
            fired.append(fire_precedent(look[cid]))
        score, _, _ = score_case(fired, an)
        out[cid] = {"fired": {r["rule_id"]: r["level"] for r in fired}, "score": score, "level": level_for(score)}
    return out


def _test_config(cfg_live, rule_id, draft):
    """Live rule set + the rule under test forced ACTIVE (a shadow/disabled/edited/new rule is tested as if it were live)."""
    if rule_id in RULE_META:
        patch = {"enabled": True}
        if draft:
            patch.update({k: v for k, v in (draft.get("params") or {}).items() if k in ("level", "elevated", "extreme")})
        return cfg_live.with_override(rule_id, patch)
    base = draft or rulestore.get(rule_id)
    if not base:
        raise ValueError("unknown rule")
    return cfg_live.with_custom({**base, "id": rule_id or "DRAFT", "status": "active"})


def _labels(mode, cfg_live, rule_id, positive_at, cases, look):
    thr = CUT[positive_at]
    outcomes = {r["case_id"]: r["outcome"] == "FRAUD" for r in db.rows("SELECT case_id, outcome FROM outcomes")}
    loo = None
    if mode in ("loo", "blend"):
        loo = {cid: v["score"] >= thr for cid, v in score_all(cfg_live.without(rule_id), cases, look).items()}
    if mode == "loo":
        return loo
    if mode == "ai":
        out = {}
        for cid, _, _ in cases:
            a = service.latest_assessment(cid)
            out[cid] = (RANK[a["ai_level"]] >= RANK[positive_at]) if a and a["status"] == "OK" else None
        return out
    if mode == "outcomes":
        return {cid: outcomes.get(cid) for cid, _, _ in cases}
    return {cid: outcomes.get(cid, loo[cid]) for cid, _, _ in cases}


def _metrics(pred, lab, n_excluded_note=True):
    tp = [c for c in pred if lab[c] is True and pred[c]]
    fp = [c for c in pred if lab[c] is False and pred[c]]
    fn = [c for c in pred if lab[c] is True and not pred[c]]
    tn = [c for c in pred if lab[c] is False and not pred[c]]
    n = len(tp) + len(fp) + len(fn) + len(tn)
    prec = len(tp) / (len(tp) + len(fp)) if tp or fp else None
    rec = len(tp) / (len(tp) + len(fn)) if tp or fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
    base = (len(tp) + len(fn)) / n if n else None
    return {"n": n, "positives": len(tp) + len(fn), "base_rate": round(base, 3) if base is not None else None,
            "tp": len(tp), "fp": len(fp), "fn": len(fn), "tn": len(tn),
            "precision": round(prec, 3) if prec is not None else None, "recall": round(rec, 3) if rec is not None else None,
            "f1": round(f1, 3) if f1 is not None else None,
            "lift": round(prec / base, 2) if prec is not None and base else None,
            "fire_rate": round((len(tp) + len(fp)) / n, 3) if n else None,
            "precision_ci": wilson(len(tp), len(tp) + len(fp)), "recall_ci": wilson(len(tp), len(tp) + len(fn))}, {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def backtest_rule(rule_id, draft=None, label_mode="loo", positive_at="HIGH", min_level=None, with_impact=True, cases=None, look=None):
    """Backtest one rule (existing, shadow, disabled, edited draft, or a brand-new draft with rule_id None)."""
    if label_mode not in MODES or positive_at not in CUT or (min_level and min_level not in RANK):
        raise ValueError("bad label_mode / positive_at / min_level")
    cases = cases or _cases()
    look = look if look is not None else _lookalikes(cases)
    rid = rule_id or "DRAFT"
    cfg_live = rulestore.load_config()
    cfg_test = _test_config(cfg_live, rid, draft)
    cal = load_calibration()
    fired_test = {}
    for cid, data, an in cases:
        if rid == "R17":
            fired_test[cid] = "MEDIUM" if look.get(cid) else None
            if look.get(cid) and len(look[cid]) >= 2:
                fired_test[cid] = "HIGH"
        else:
            fired_test[cid] = {r["rule_id"]: r["level"] for r in evaluate(dict(data), cal, cfg_test)}.get(rid)
    floor = RANK[min_level] if min_level else 0
    pred = {cid: (lv is not None and RANK[lv] >= floor) for cid, lv in fired_test.items()}
    lab = _labels(label_mode, cfg_live, rid, positive_at, cases, look)
    usable = {c: pred[c] for c in pred if lab[c] is not None}
    m, cells = _metrics(usable, lab)
    warnings = [WARN[label_mode]]
    if m["n"] < len(cases):
        warnings.append(f"{len(cases) - m['n']} of {len(cases)} cases have no label in this mode and are excluded (n={m['n']}).")
    if m["n"] < 30 or m["positives"] < 5:
        warnings.append(f"Small sample (n={m['n']}, positives={m['positives']}): read the confidence intervals, not the point estimates.")
    if m["tp"] + m["fp"] == 0:
        warnings.append("The rule never fires on labelled cases, so precision is undefined.")
    res = {"rule_id": rule_id, "label_mode": label_mode, "positive_at": positive_at, "min_level": min_level, **m, "cases": cells,
           "fires_on": [c for c, v in pred.items() if v], "excluded": len(cases) - m["n"], "warnings": warnings}
    if with_impact:
        res["impact"] = impact(cfg_live, cfg_test, cases, look)
        moved = {x["case_id"] for x in res["impact"]["changed"]} | {x["case_id"] for x in res["impact"]["level_changes"]}
        # cases where the rule fires but the score does not move: an equal-or-stronger rule already covers that domain (domain-capped noisy-OR)
        res["impact"]["no_effect_on"] = [c for c in res["fires_on"] if c not in moved]
    return res


def impact(cfg_live, cfg_test, cases, look):
    """What saving/activating this rule would do to every case's score and level."""
    a, b = score_all(cfg_live, cases, look), score_all(cfg_test, cases, look)
    ch = [{"case_id": c, "from": a[c]["level"], "to": b[c]["level"], "score_from": a[c]["score"], "score_to": b[c]["score"]}
          for c in a if a[c]["score"] != b[c]["score"]]
    return {"score_changes": len(ch), "level_changes": [x for x in ch if x["from"] != x["to"]], "changed": ch[:60]}


def leaderboard(label_mode="loo", positive_at="HIGH", min_level=None):
    if label_mode not in MODES or positive_at not in CUT or (min_level and min_level not in RANK):
        raise ValueError("bad label_mode / positive_at / min_level")
    cases = _cases()
    look = _lookalikes(cases)
    rows = []
    for r in rulestore.all_rules():
        try:
            res = backtest_rule(r["rule_id"], None, label_mode, positive_at, min_level, with_impact=False, cases=cases, look=look)
        except Exception as e:  # a broken rule must not take down the whole table
            rows.append({**r, "error": str(e)})
            continue
        rows.append({**r, **{k: res[k] for k in ("n", "positives", "tp", "fp", "fn", "tn", "precision", "recall", "f1", "lift", "fire_rate", "precision_ci", "recall_ci")},
                     "fires": len(res["fires_on"])})
    return {"rows": rows, "label_mode": label_mode, "positive_at": positive_at, "min_level": min_level, "warning": WARN[label_mode],
            "outcomes_available": db.one("SELECT COUNT(*) n FROM outcomes")["n"]}
