"""Investigation Crew with Adaptive Ensemble. Returns the same `out` dict shape as the single judge so storage,
UI and bulk-clear guardrails are unchanged; extra crew telemetry rides inside result["crew"]."""
import asyncio
import json
import statistics as st

from .. import db, service
from ..llm import client
from ..llm.guardrails import apply_guardrails, ground_indicator
from ..scoring import level_for
from . import domains, evidence, roles, router
from .runtime import AgentFailure, Budget, Run, StopReason, run_agent
from .schemas import Challenge, FastBatch, Finding, PanelVote, Verdict

BAND_CUTS = (25, 50, 80)
NEAR_CUT = 5
CONF_LABEL = lambda x: "LOW" if x < 0.5 else "MEDIUM" if x < 0.75 else "HIGH"


def _jd(o):
    return json.dumps(o, indent=1, default=str)


def _prev_blackboard(case_id):
    r = db.one("SELECT blackboard FROM runs WHERE case_id=? AND mode='crew' AND lane='DEEP' ORDER BY id DESC LIMIT 1", (case_id,))
    return json.loads(r["blackboard"]) if r and r["blackboard"] else {}


async def _specialist(run, case, domain, prev, full):
    sl = domains.slice_for(case, domain)
    h = domains.slice_hash(sl)
    cached = prev.get(domain)
    if cached and cached.get("hash") == h and not full:
        run.span(domain, "reuse", "cached finding", f"slice hash {h} unchanged")
        return domain, Finding(**cached["finding"]), h, True
    d = domains.SPECIALISTS[domain]
    res = await run_agent(run, domain, roles.SPECIALIST.format(title=d["title"], focus=d["focus"]), "Evidence slice:\n" + _jd(sl),
                          Finding, allowed_tools=d["tools"], tier="fast", budget=Budget(max_steps=3, max_tool_calls=1, max_tokens=8000))
    f: Finding = res.output
    fired_ids = {r["rule_id"] for r in case["rules"]}
    keep = []
    for e in f.evidence:
        ok, why = ground_indicator({"field": e.field, "value": e.value}, case["data"], set(), "")
        if ok:
            keep.append(e)
        else:
            run.span(domain, "grounding", "dropped evidence", f"{e.field}={e.value}: {why}")
    f.evidence = keep
    return domain, f, h, False


def _contest_reasons(plan_specs, findings, chal, verdict, ai_level, rule_score, rule_level):
    why = []
    if verdict.self_confidence < 0.5:
        why.append(f"verifier confidence low ({verdict.self_confidence:.2f})")
    if verdict.contested:
        why.append("verifier flagged contested: " + (verdict.contested_reason or "evidence splits"))
    if chal and chal.plausibility == "high":
        why.append("challenger benign story rated high plausibility")
    dirs = {f.direction for f in findings.values()}
    if "suspicious" in dirs and "benign" in dirs:
        why.append("specialists disagree (suspicious vs benign)")
    if ai_level != rule_level:
        why.append(f"AI band {ai_level} differs from rule band {rule_level}")
    near = min(abs(rule_score - c) for c in BAND_CUTS)
    if near <= NEAR_CUT:
        why.append(f"rule score {rule_score} is within {NEAR_CUT} of a band cut")
    return why


def _confidence(verdict, findings, chal, panel_agreement):
    parts = [(0.45, verdict.self_confidence)]
    if findings:
        dirs = [f.direction for f in findings.values()]
        top = max(set(dirs), key=dirs.count)
        parts.append((0.25, dirs.count(top) / len(dirs)))
    if chal:
        parts.append((0.15, {"low": 1.0, "medium": 0.6, "high": 0.2}[chal.plausibility]))
    if panel_agreement is not None:
        parts.append((0.35, panel_agreement))
    tw = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / tw, 2)


async def _panel(run, bundle, findings, chal, verdict):
    ctx = {"evidence": bundle, "findings": {k: v.model_dump() for k, v in findings.items()},
           "challenger": chal.model_dump() if chal else None, "verifier_draft": verdict.model_dump()}

    async def one(persona):
        r = await run_agent(run, f"panel:{persona}", roles.PANEL.format(persona=roles.PERSONAS[persona]), _jd(ctx), PanelVote,
                            tier="fast", temperature=0.4, budget=Budget(max_steps=2, max_tool_calls=0, max_tokens=9000))
        return persona, r.output

    res = await asyncio.gather(*(one(p) for p in roles.PERSONAS), return_exceptions=True)
    votes = {}
    for r in res:
        if isinstance(r, Exception):
            run.span("panel", "failure", "vote lost", str(r)[:200])
        else:
            votes[r[0]] = r[1]
    return votes


def _meta(votes, rule_score, verdict):
    """Deterministic meta-judge: median adjustment, majority action, agreement = share of votes landing in the final band."""
    if not votes:
        return None
    adj = int(round(st.median(v.score_adjustment for v in votes.values())))
    acts = [v.recommended_action for v in votes.values()]
    top = max(set(acts), key=acts.count)
    action = top if acts.count(top) > 1 else verdict.recommended_action
    final = level_for(min(100, max(0, rule_score + adj)))
    band_agree = sum(1 for v in votes.values() if level_for(min(100, max(0, rule_score + v.score_adjustment))) == final) / len(votes)
    adjs = [v.score_adjustment for v in votes.values()]
    spread = max(adjs) - min(adjs)
    # band agreement alone saturates at 100% for near-identical votes; blend in how far apart the adjustments are (0 spread=1.0, >=20 pts=0)
    agree = 0.5 * band_agree + 0.5 * max(0.0, 1 - spread / 20)
    return {"adjustment": adj, "action": action, "final_band": final, "agreement": round(agree, 2), "spread": spread,
            "split": band_agree < 0.67, "votes": {k: v.model_dump() for k, v in votes.items()}}


async def run_crew(case_id, full=False):
    case = service.get_case(case_id)
    plan = router.plan(case)
    if plan.lane == "FAST":
        return (await assess_fast_batch([case_id]))[case_id]
    run = Run(case_id)
    prev = _prev_blackboard(case_id) if plan.lane == "DEEP" else {}
    bundle = evidence.bundle(case)
    findings, board, reused, chal, agents = {}, {}, [], None, []
    stop = StopReason.FINAL

    if plan.lane == "DEEP":
        res = await asyncio.gather(*(_specialist(run, case, d, prev, full) for d in plan.specialists), return_exceptions=True)
        for d, r in zip(plan.specialists, res):
            if isinstance(r, Exception):
                run.span(d, "failure", "specialist failed", str(r)[:200])
                continue
            dom, f, h, was_cached = r
            findings[dom] = f
            board[dom] = {"hash": h, "finding": f.model_dump()}
            agents.append({"agent": dom, "reused": was_cached})
            if was_cached:
                reused.append(dom)
        if plan.specialists and not findings:
            raise client.LLMError("no specialist produced a finding")
        try:
            chal = (await run_agent(run, "challenger", roles.CHALLENGER,
                                    _jd({"evidence": bundle, "findings": {k: v.model_dump() for k, v in findings.items()}}),
                                    Challenge, tier="fast", budget=Budget(max_steps=2, max_tool_calls=0, max_tokens=9000))).output
            keep = []
            for a in chal.arguments:
                ok, why = ground_indicator({"field": a.field, "value": a.value}, case["data"], set(), "\n".join(n["text"] for n in case["notes"]), evidence.related_text(case))
                if ok:
                    keep.append(a)
                else:
                    run.span("challenger", "grounding", "dropped argument", f"{a.field}={a.value}: {why}")
            chal.arguments = keep
            agents.append({"agent": "challenger", "reused": False})
        except (AgentFailure, client.LLMError) as e:
            run.span("challenger", "failure", "challenger failed", str(e)[:200])

    vctx = {"evidence": bundle, "specialist_findings": {k: v.model_dump() for k, v in findings.items()} or "none (clear-cut lane)",
            "challenger": chal.model_dump() if chal else "none"}
    vres = await run_agent(run, "verifier", roles.VERIFIER, _jd(vctx), Verdict, tier="fast" if plan.lane == "OBVIOUS" else "strong",  # strong tier only where the evidence is ambiguous
                           
                           budget=Budget(max_steps=2, max_tool_calls=0, max_tokens=16000), max_tokens=2000)
    verdict: Verdict = vres.output
    agents.append({"agent": "verifier", "reused": False})
    linked = evidence.linked_levels(case)
    rel_text = evidence.related_text(case)
    check_packet = {"bundle": bundle, "findings": vctx["specialist_findings"], "challenger": vctx["challenger"]}
    raw = verdict.model_dump()
    _, ai_score0, ai_level0, *_ = apply_guardrails(raw, case["data"], case["rules"], case["score"], check_packet, case["notes"], linked, related_text=rel_text, related_ids=evidence.related_ids(case))

    reasons = _contest_reasons(plan.specialists, findings, chal, verdict, ai_level0, case["score"], case["level"]) if plan.lane == "DEEP" else \
        ([f"verifier confidence low ({verdict.self_confidence:.2f})"] if verdict.self_confidence < 0.5 else [])
    panel = None
    if reasons:
        votes = await _panel(run, bundle, findings, chal, verdict)
        panel = _meta(votes, case["score"], verdict)
        if panel:
            raw["score_adjustment"], raw["recommended_action"] = panel["adjustment"], panel["action"]
            agents += [{"agent": f"panel:{k}", "reused": False} for k in votes]
    result, ai_score, ai_level, verdict_label, warnings = apply_guardrails(raw, case["data"], case["rules"], case["score"], check_packet, case["notes"], linked, related_text=rel_text, related_ids=evidence.related_ids(case))
    conf = _confidence(verdict, findings, chal, panel["agreement"] if panel else None)
    if panel and panel["split"]:
        conf = min(conf, 0.45)
        warnings.append("judge panel split across bands: human decision needed, dissent shown in the trace")
    result["confidence"] = CONF_LABEL(conf)
    tot = run.totals()
    result["crew"] = {"lane": plan.lane, "lane_reason": plan.reason, "confidence_score": conf, "contested": bool(reasons), "contested_reasons": reasons,
                      "findings": {k: v.model_dump() for k, v in findings.items()}, "reused_specialists": reused,
                      "challenger": chal.model_dump() if chal else None, "verifier_self_confidence": verdict.self_confidence,
                      "panel": panel, "agents": agents, "usage": tot, "stop_reason": stop.value}
    rid = run.save(plan.lane, stop.value, bool(reasons), agents, board)
    result["crew"]["run_id"] = rid
    model = ",".join(tot["models"]) or "n/a"
    return {"model": model, "result": result, "ai_score": ai_score, "ai_level": ai_level, "verdict": verdict_label,
            "action": result["recommended_action"], "warnings": warnings, "trace": [], "rule_score": case["score"], "rule_level": case["level"]}


# ------------------------------------------------------------------ FAST lane (batched)
async def assess_fast_batch(case_ids):
    """One cheap call reviews up to N clean cases. Returns {case_id: out | LLMError}."""
    cases = {i: service.get_case(i) for i in case_ids}
    run = Run("batch:" + ",".join(case_ids))
    items = [{"case_id": i, "care_type": c["data"]["care_type"], "amount": c["data"]["claim_amount_usd"],
              "signals": evidence.bundle(c, with_similar=False)["signals"], "derived": evidence.bundle(c, with_similar=False)["derived"],
              "rule_engine": {"score": c["score"], "level": c["level"], "fired": []},
              "notes": [{"author": n["author"], "text": n["text"]} for n in c["notes"]],
              "related_case_notes": evidence.bundle(c, with_similar=False)["related_case_notes"]} for i, c in cases.items()]
    out = {}
    try:
        res = await run_agent(run, "fast", roles.FAST, _jd(items), FastBatch, tier="fast",
                              budget=Budget(max_steps=2, max_tool_calls=0, max_tokens=20000), max_tokens=400 * len(items) + 300)
        got = {r.case_id: r for r in res.output.results}
    except (AgentFailure, client.LLMError) as e:
        if len(case_ids) > 1:  # degrade: retry singly rather than fail the whole batch
            for i in case_ids:
                out[i] = (await assess_fast_batch([i]))[i]
            return out
        return {case_ids[0]: client.LLMError(str(e))}
    tot = run.totals()
    for i, c in cases.items():
        r = got.get(i)
        if not r:
            out[i] = client.LLMError("fast lane: case missing from batch reply")
            continue
        raw = {"summary": r.summary, "score_adjustment": r.score_adjustment, "recommended_action": r.recommended_action,
               "mitigating_factors": r.mitigating_factors, "open_questions": r.open_questions, "key_indicators": []}
        try:
            result, ai_score, ai_level, verdict, warnings = apply_guardrails(raw, c["data"], c["rules"], c["score"],
                                                                             {"items": items}, c["notes"], evidence.linked_levels(c),
                                                                             related_text=evidence.related_text(c), related_ids=evidence.related_ids(c))
        except ValueError as e:
            out[i] = client.LLMError(str(e))
            continue
        result["confidence"] = "HIGH" if not c["notes"] else "MEDIUM"
        result["crew"] = {"lane": "FAST", "lane_reason": "no rules fired, no linked cases", "confidence_score": None, "contested": False,
                          "contested_reasons": [], "findings": {}, "agents": [{"agent": "fast", "reused": False}], "batch_size": len(case_ids),
                          "usage": {**tot, "calls": round(tot["calls"] / len(case_ids), 2), "tokens_in": tot["tokens_in"] // len(case_ids),
                                    "tokens_out": tot["tokens_out"] // len(case_ids), "est_cost": tot["est_cost"] / len(case_ids)},
                          "stop_reason": "final"}
        out[i] = {"model": ",".join(tot["models"]), "result": result, "ai_score": ai_score, "ai_level": ai_level, "verdict": verdict,
                  "action": result["recommended_action"], "warnings": warnings, "trace": [], "rule_score": c["score"], "rule_level": c["level"]}
    # one persisted run per case (cost is amortised across the batch), spans attached to the first case
    for n, i in enumerate(case_ids):
        sub = Run(i)
        sub.usage, sub.spans, sub.t0 = run.usage, (run.spans if n == 0 else []), run.t0
        sub.save("FAST", "final", False, [{"agent": "fast"}], {}, batch_size=len(case_ids))
    return out
