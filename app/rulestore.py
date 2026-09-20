"""Rule persistence: custom rules (full CRUD), built-in overrides (enable/disable, thresholds, level), versioning + audit.
load_config() gives rules.evaluate its live view; shadow rules are excluded from scoring and only added by the backtester."""
import json

from . import db, rules
from .calibration import load_calibration
from .customrules import (DOMAIN_CHOICES, FIELDS, LEVELS, RuleConfig, RuleError, describe, referenced_fields, validate_logic)

STATUSES = ("active", "shadow", "disabled")
_cache = {"stamp": None, "cfg": None}


def _stamp():
    a = db.one("SELECT COUNT(*) n, MAX(updated_at) m, MAX(version) v FROM custom_rules")
    b = db.one("SELECT COUNT(*) n, MAX(updated_at) m, MAX(version) v FROM rule_overrides")
    return (str(db.DB_PATH), a["n"], a["m"], a["v"], b["n"], b["m"], b["v"])


def invalidate():
    _cache["stamp"] = None


def _row_to_rule(r):
    r = dict(r)
    r["logic"] = json.loads(r["logic"])
    return r


def _overrides():
    out = {}
    for r in db.rows("SELECT * FROM rule_overrides"):
        out[r["rule_id"]] = {"enabled": bool(r["enabled"]), "level": r["level"], "elevated": r["elevated"], "extreme": r["extreme"]}
    return out


def load_config(include_shadow=False):
    """Live RuleConfig (cached; cheap stamp check per call). include_shadow=True is for backtests only."""
    try:
        st = _stamp()
    except Exception:  # tables not created yet
        return RuleConfig.empty()
    if _cache["stamp"] != st:
        _cache["stamp"], _cache["cfg"] = st, None
    if include_shadow:
        rows = db.rows("SELECT * FROM custom_rules WHERE status IN ('active','shadow') ORDER BY id")
        return RuleConfig(_overrides(), [_row_to_rule(r) for r in rows])
    if _cache["cfg"] is None:
        rows = db.rows("SELECT * FROM custom_rules WHERE status='active' ORDER BY id")
        _cache["cfg"] = RuleConfig(_overrides(), [_row_to_rule(r) for r in rows])
    return _cache["cfg"]


# ---------------------------------------------------------------- validation
def validate_definition(d, existing_id=None, check_name=True):
    if not isinstance(d, dict):
        raise RuleError("definition must be an object")
    name = (d.get("name") or "").strip()
    if not (3 <= len(name) <= 80):
        raise RuleError("name must be 3-80 characters")
    dup = db.one("SELECT id FROM custom_rules WHERE lower(name)=lower(?)", (name,))
    if check_name and ((dup and dup["id"] != existing_id) or name.lower() in {v[0].lower() for v in rules.RULE_META.values()}):
        raise RuleError(f"a rule named '{name}' already exists")
    if d.get("level") not in LEVELS:
        raise RuleError(f"level must be one of {LEVELS}")
    if d.get("domain") not in DOMAIN_CHOICES:
        raise RuleError(f"domain must be one of {DOMAIN_CHOICES}")
    if d.get("status", "active") not in STATUSES:
        raise RuleError(f"status must be one of {STATUSES}")
    validate_logic(d.get("logic"))
    return {"name": name, "description": (d.get("description") or "").strip()[:400], "level": d["level"], "domain": d["domain"],
            "status": d.get("status", "active"), "logic": d["logic"]}


def _next_id():
    ids = [int(r["id"][1:]) for r in db.rows("SELECT id FROM custom_rules") if r["id"][1:].isdigit()]
    return f"X{(max(ids) + 1 if ids else 1):02d}"


def _version(rule_id, version, definition, actor, note):
    db.run("INSERT INTO rule_versions(rule_id,version,definition,changed_by,changed_at,note) VALUES(?,?,?,?,?,?)",
           (rule_id, version, json.dumps(definition), actor, db.now(), note))


# ---------------------------------------------------------------- custom rules
def create(d, actor, role="investigator"):
    v = validate_definition(d)
    rid = _next_id()
    db.run("""INSERT INTO custom_rules(id,name,description,domain,level,status,logic,created_by,created_at,updated_by,updated_at,version)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,1)""", (rid, v["name"], v["description"], v["domain"], v["level"], v["status"], json.dumps(v["logic"]),
                                                     actor, db.now(), actor, db.now()))
    _version(rid, 1, v, actor, "created")
    db.audit(actor, role, "RULE_CREATED", None, {"rule_id": rid, **{k: v[k] for k in ("name", "level", "domain", "status")}, "logic": describe(v["logic"])})
    invalidate()
    return get(rid)


def update(rid, d, actor, role="investigator"):
    cur = db.one("SELECT * FROM custom_rules WHERE id=?", (rid,))
    if not cur:
        raise RuleError("unknown custom rule")
    merged = {**_row_to_rule(cur), **d}
    v = validate_definition(merged, existing_id=rid)
    ver = cur["version"] + 1
    db.run("""UPDATE custom_rules SET name=?,description=?,domain=?,level=?,status=?,logic=?,updated_by=?,updated_at=?,version=? WHERE id=?""",
           (v["name"], v["description"], v["domain"], v["level"], v["status"], json.dumps(v["logic"]), actor, db.now(), ver, rid))
    _version(rid, ver, v, actor, "edited")
    db.audit(actor, role, "RULE_UPDATED", None, {"rule_id": rid, "version": ver, "status": v["status"], "level": v["level"], "logic": describe(v["logic"])})
    invalidate()
    return get(rid)


def delete(rid, actor, role="investigator"):
    cur = db.one("SELECT * FROM custom_rules WHERE id=?", (rid,))
    if not cur:
        raise RuleError("unknown custom rule")
    db.run("DELETE FROM custom_rules WHERE id=?", (rid,))
    _version(rid, cur["version"] + 1, {"deleted": True, "name": cur["name"]}, actor, "deleted")
    db.audit(actor, role, "RULE_DELETED", None, {"rule_id": rid, "name": cur["name"]})
    invalidate()


def get(rid):
    r = db.one("SELECT * FROM custom_rules WHERE id=?", (rid,))
    return _row_to_rule(r) if r else None


# ---------------------------------------------------------------- built-in overrides
def set_override(rid, patch, actor, role="investigator"):
    if rid not in rules.RULE_META:
        raise RuleError("unknown built-in rule")
    cur = _overrides().get(rid, {"enabled": True, "level": None, "elevated": None, "extreme": None})
    new = {**cur, **{k: v for k, v in patch.items() if k in ("enabled", "level", "elevated", "extreme")}}
    if "level" in patch and patch["level"] is not None:
        if rid not in rules.LEVEL_RULES:
            raise RuleError(f"{rid} has tiered levels; tune its thresholds instead")
        if patch["level"] not in LEVELS:
            raise RuleError(f"level must be one of {LEVELS}")
    for k in ("elevated", "extreme"):
        if patch.get(k) is not None:
            if rid not in rules.THRESHOLD_RULES:
                raise RuleError(f"{rid} has no numeric thresholds")
            if isinstance(patch[k], bool) or not isinstance(patch[k], (int, float)):
                raise RuleError(f"{k} threshold must be a number")
    if new["elevated"] is not None and new["extreme"] is not None and new["elevated"] > new["extreme"]:
        raise RuleError("the MEDIUM threshold must not exceed the HIGH threshold")
    row = db.one("SELECT version FROM rule_overrides WHERE rule_id=?", (rid,))
    ver = (row["version"] if row else 0) + 1
    db.run("DELETE FROM rule_overrides WHERE rule_id=?", (rid,))
    if new["enabled"] and new["level"] is None and new["elevated"] is None and new["extreme"] is None:
        # back at the calibrated default: no override row (so it is not shown as 'tuned')
        _version(rid, ver, {"reset": True}, actor, "back to default")
        db.audit(actor, role, "RULE_TUNED", None, {"rule_id": rid, "override": "default"})
        invalidate()
        return new
    db.run("INSERT INTO rule_overrides(rule_id,enabled,level,elevated,extreme,updated_by,updated_at,version) VALUES(?,?,?,?,?,?,?,?)",
           (rid, int(bool(new["enabled"])), new["level"], new["elevated"], new["extreme"], actor, db.now(), ver))
    _version(rid, ver, new, actor, "override")
    db.audit(actor, role, "RULE_TUNED", None, {"rule_id": rid, "override": new})
    invalidate()
    return new


def reset(rid, actor, role="investigator"):
    if not db.one("SELECT 1 FROM rule_overrides WHERE rule_id=?", (rid,)):
        return
    db.run("DELETE FROM rule_overrides WHERE rule_id=?", (rid,))
    _version(rid, 0, {"reset": True}, actor, "reset to calibrated default")
    db.audit(actor, role, "RULE_RESET", None, {"rule_id": rid})
    invalidate()


def history(rid):
    return [{**r, "definition": json.loads(r["definition"])} for r in db.rows("SELECT * FROM rule_versions WHERE rule_id=? ORDER BY id DESC", (rid,))]


# ---------------------------------------------------------------- catalogue for the UI / LLM tool
def field_catalogue():
    cats = {}
    for f in ("care_type", "state"):
        cats[f] = sorted({r[f] for r in db.rows(f"SELECT DISTINCT {f} FROM cases")})
    return [{"name": k, "type": t, "label": lab, "values": cats.get(k)} for k, (t, lab) in FIELDS.items()]


def all_rules():
    """Every rule (built-in + custom, any status) with its effective definition and defaults, for the Rules tab."""
    cal = load_calibration()
    cfg = load_config(include_shadow=True)
    base = {r["rule_id"]: r for r in rules.catalog(cal, RuleConfig.empty())}
    out = []
    for rid, (name, domain, desc) in rules.RULE_META.items():
        ov = cfg.overrides.get(rid, {})
        item = {"rule_id": rid, "kind": "builtin", "name": name, "domain": domain, "description": desc, "logic": base[rid]["logic"],
                "status": "active" if ov.get("enabled", True) is not False else "disabled",
                "tuned": bool(ov) and (ov.get("enabled") is False or any(ov.get(k) is not None for k in ("level", "elevated", "extreme")))}
        if rid in rules.THRESHOLD_RULES:
            sig = cal["signals"][rules.THRESHOLD_RULES[rid]]
            item["params"] = {"type": "thresholds", "signal": rules.THRESHOLD_RULES[rid], "default_elevated": sig["elevated_threshold"], "default_extreme": sig["extreme_threshold"],
                              "elevated": ov.get("elevated"), "extreme": ov.get("extreme")}
        elif rid in rules.LEVEL_RULES:
            item["params"] = {"type": "level", "default_level": rules.LEVEL_RULES[rid], "level": ov.get("level")}
        out.append(item)
    for r in cfg.custom:
        full = get(r["id"])
        out.append({"rule_id": r["id"], "kind": "custom", "name": r["name"], "domain": r["domain"], "description": r["description"], "level": r["level"],
                    "logic": describe(r["logic"]), "logic_tree": r["logic"], "status": r["status"], "version": full["version"],
                    "created_by": full["created_by"], "updated_by": full["updated_by"], "updated_at": full["updated_at"]})
    for r in db.rows("SELECT id FROM custom_rules WHERE status='disabled'"):
        full = get(r["id"])
        out.append({"rule_id": full["id"], "kind": "custom", "name": full["name"], "domain": full["domain"], "description": full["description"], "level": full["level"],
                    "logic": describe(full["logic"]), "logic_tree": full["logic"], "status": "disabled", "version": full["version"],
                    "created_by": full["created_by"], "updated_by": full["updated_by"], "updated_at": full["updated_at"]})
    return out
