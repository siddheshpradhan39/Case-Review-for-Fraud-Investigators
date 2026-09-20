"""Deterministic guardrails applied to every LLM judge output. The model proposes; this module disposes.

1. Score adjustment clamped to +-MAX_ADJ; the final level is *derived* from the final score (the model
   cannot assert a level directly).
2. CLEAR_FALSE_POSITIVE is refused if any HIGH/CRITICAL rule fired or the final level is above LOW.
3. Grounding: every key indicator must cite a real field whose value matches the case record (or quote an
   actual note); indicators citing a rule that did not fire are dropped.
4. Linked-case floor: no downgrade when a case sharing the claim number is CRITICAL.
5. Summary figures ($, %, decimals, >=3-digit numbers) not present in the evidence packet are flagged.
"""
import json
import re

from ..scoring import level_for

MAX_ADJ = 20
ACTIONS = ["CLEAR_FALSE_POSITIVE", "ROUTINE_REVIEW", "REQUEST_RECORDS", "CONTACT_MEMBER", "PROVIDER_AUDIT", "ESCALATE_SIU"]
NON_FIELD_SOURCES = {"note", "linked_case"}


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _values_match(claimed, actual):
    a, b = _num(claimed), _num(actual)
    if a is not None and b is not None:
        return abs(a - b) <= max(0.011 * abs(b), 0.011)
    return str(claimed).strip().lower() == str(actual).strip().lower()


def ground_indicator(ind, case_data, fired_ids, notes_text, related_text="", related_ids=()):
    """Returns (ok, reason)."""
    field = ind.get("field")
    rid = ind.get("rule_id")
    if rid and rid not in fired_ids:
        return False, f"cites {rid}, which did not fire on this case"
    if field == "note":
        quote = str(ind.get("value", "")).strip().lower()
        if len(quote) >= 6 and quote in notes_text.lower():
            return True, ""
        return False, "quotes text that is not in any note"
    if field in ("related_case", "nearest_similar_cases", "confirmed_fraud_lookalikes"):
        return (any(c in str(ind.get("value", "")) for c in related_ids), "cites a case that is not among the related cases")
    if field == "related_note":
        quote = str(ind.get("value", "")).strip().lower()
        if len(quote) >= 6 and quote in related_text.lower():
            return True, ""
        return False, "quotes text that is not in any related-case note"
    if field == "linked_case":
        return (str(ind.get("value")) in case_data.get("linked_case_ids", []), "linked case not found")
    if field not in case_data:
        return False, f"unknown field '{field}'"
    if not _values_match(ind.get("value"), case_data[field]):
        return False, f"{field}={ind.get('value')} but the record says {case_data[field]}"
    return True, ""


def _figures(text):
    """Numbers worth verifying, as floats. '$29.7k' -> 29700."""
    out = []
    for m in re.finditer(r"(\$?)(\d[\d,]*\.?\d*)(\s?[kK]\b|%)?", text or ""):
        dollar, raw, suf = m.groups()
        v = _num(raw)
        if v is None:
            continue
        if suf and suf.strip().lower() == "k":
            v *= 1000
        if dollar or (suf and suf.strip() == "%") or "." in raw or v >= 100:
            out.append(v)
    return out


def ungrounded_figures(text, packet_text):
    allowed = [abs(v) for v in (_num(t) for t in re.findall(r"\d[\d,]*\.?\d*", packet_text)) if v is not None]
    bad = []
    for f in _figures(text):
        if not any(abs(f - a) <= max(0.02 * abs(a), 0.06) for a in allowed):
            bad.append(f)
    return bad


def apply_guardrails(raw, case, fired, rule_score, packet, notes, linked_levels=(), related_text="", related_ids=()):
    """raw: parsed model JSON. Returns (result, warnings). Raises ValueError if unusable."""
    warnings = []
    if not isinstance(raw, dict) or not raw.get("summary"):
        raise ValueError("model output missing 'summary'")
    fired_ids = {r["rule_id"] for r in fired}
    hi_rules = [r["rule_id"] for r in fired if r["level"] in ("HIGH", "CRITICAL")]
    notes_text = "\n".join(n["text"] for n in notes)
    # derived-rule evidence (e.g. R13 domains_tripped) is legitimately citable when that rule fired
    case = {**case, **{k: v for r in fired for k, v in (r.get("fields") or {}).items()}}

    try:
        adj = int(round(float(raw.get("score_adjustment", 0))))
    except (TypeError, ValueError):
        adj = 0
        warnings.append("score_adjustment unreadable; treated as 0")
    if abs(adj) > MAX_ADJ:
        warnings.append(f"score_adjustment {adj} clamped to +-{MAX_ADJ}")
        adj = MAX_ADJ if adj > 0 else -MAX_ADJ
    if adj < 0 and "CRITICAL" in linked_levels:
        warnings.append(f"linked-case floor: a case sharing this claim number is CRITICAL, so the downgrade of {adj} was blocked")
        adj = 0
    ai_score = round(min(100.0, max(0.0, rule_score + adj)), 1)
    ai_level = level_for(ai_score)
    verdict = "UPGRADE" if adj > 0 else "DOWNGRADE" if adj < 0 else "CONFIRM"
    if ai_level == level_for(rule_score) and adj != 0:
        verdict += " (within band)"

    action = raw.get("recommended_action")
    if action not in ACTIONS:
        warnings.append(f"invalid action '{action}' replaced with ROUTINE_REVIEW")
        action = "ROUTINE_REVIEW"
    if action == "CLEAR_FALSE_POSITIVE" and "R17" in fired_ids:
        warnings.append("CLEAR_FALSE_POSITIVE refused: case resembles a human-confirmed fraud (R17); downgraded to ROUTINE_REVIEW")
        action = "ROUTINE_REVIEW"
    if action == "CLEAR_FALSE_POSITIVE" and (hi_rules or ai_level != "LOW"):
        why = f"HIGH/CRITICAL rules fired ({', '.join(hi_rules)})" if hi_rules else f"AI level is {ai_level}"
        warnings.append(f"CLEAR_FALSE_POSITIVE refused: {why}; downgraded to ROUTINE_REVIEW")
        action = "ROUTINE_REVIEW"

    def clean(items, kind):
        out = []
        for it in raw.get(items) or []:
            if not isinstance(it, dict):
                continue
            ok, why = ground_indicator(it, case, fired_ids, notes_text, related_text, related_ids)
            if ok:
                out.append({"rule_id": it.get("rule_id"), "field": it.get("field"), "value": it.get("value"),
                            "why": str(it.get("why", ""))[:400]})
            else:
                warnings.append(f"dropped ungrounded {kind} ({it.get('field')}={it.get('value')}): {why}")
        return out

    indicators = clean("key_indicators", "indicator")
    mitigating = clean("mitigating_factors", "mitigating factor")
    if not indicators and fired:
        warnings.append("no grounded key indicators survived validation")

    packet_text = json.dumps(packet, default=str) + f" {rule_score} {abs(adj)} {ai_score}"  # the model's own arithmetic is legitimate
    bad = ungrounded_figures(str(raw.get("summary", "")) + " " + str(raw.get("adjustment_reason", "")), packet_text)
    if bad:
        warnings.append("figures in the narrative not found in the evidence: " + ", ".join(f"{b:g}" for b in bad))

    conf = raw.get("confidence") if raw.get("confidence") in ("LOW", "MEDIUM", "HIGH") else "LOW"
    result = {
        "summary": str(raw["summary"])[:1200], "score_adjustment": adj,
        "adjustment_reason": str(raw.get("adjustment_reason", ""))[:600],
        "key_indicators": indicators, "mitigating_factors": mitigating, "recommended_action": action,
        "next_steps": [str(s)[:300] for s in (raw.get("next_steps") or [])][:6], "confidence": conf,
        "open_questions": [str(s)[:300] for s in (raw.get("open_questions") or [])][:5],
    }
    return result, ai_score, ai_level, verdict, warnings
