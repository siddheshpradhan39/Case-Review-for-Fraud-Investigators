"""Agent harness: one bounded, observable, fail-closed agent loop.

What the harness guarantees for EVERY agent, regardless of prompt quality or model behaviour:
  * budgets      - max LLM steps, tool calls, tokens, wall-clock; hitting one FORCES a final answer (no tools)
  * allowlists   - an agent can only execute tools it was given; violations are refused, recorded, and returned to the model
  * no-progress  - repeating an identical tool call is not executed again; the loop is pushed to finalise
  * typed output - JSON must validate against a pydantic schema; failures go back to the model as a repair prompt (max 2)
  * accounting   - every call's tokens/latency/model is recorded and priced (reference price table)
  * tracing      - every LLM call and tool call becomes a span (persisted in `spans`, shown in the UI)
  * fail-closed  - unrecoverable failure raises AgentFailure/LLMError; callers degrade, they never fabricate output
"""
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ValidationError

from .. import db
from ..llm import client, tools


class StopReason(str, Enum):
    FINAL = "final"
    TOOL_BUDGET = "tool_budget"
    STEP_BUDGET = "step_budget"
    TOKEN_BUDGET = "token_budget"
    TIME_BUDGET = "time_budget"
    NO_PROGRESS = "no_progress"


class AgentFailure(Exception):
    pass


@dataclass
class Budget:
    max_steps: int = 4
    max_tool_calls: int = 1
    max_tokens: int = 14000
    max_seconds: float = 150


def price(usage):
    """Reference $ (configurable, per 1M tokens). Free-tier actual cost is $0; this shows what the same call
    pattern would cost on paid models so cost engineering is measurable."""
    fast = [float(x) for x in os.environ.get("PRICE_FAST", "0.25,1.25").split(",")]
    strong = [float(x) for x in os.environ.get("PRICE_STRONG", "3,15").split(",")]
    p = strong if usage.get("tier") == "strong" else fast
    return (usage["in"] * p[0] + usage["out"] * p[1]) / 1e6


@dataclass
class Run:
    """One assessment run for one case (or one FAST batch): usage + spans, persisted at the end."""
    case_id: str
    mode: str = "crew"
    usage: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    t0: float = field(default_factory=time.time)

    def span(self, agent, kind, name, detail="", ms=0, tokens=0):
        self.spans.append({"agent": agent, "kind": kind, "name": name, "detail": str(detail)[:500], "ms": ms, "tokens": tokens})

    def totals(self):
        return {"calls": len(self.usage), "tokens_in": sum(u["in"] for u in self.usage), "tokens_out": sum(u["out"] for u in self.usage),
                "est_cost": round(sum(price(u) for u in self.usage), 6), "latency_ms": int((time.time() - self.t0) * 1000),
                "models": sorted({u["model"].split("/")[-1] for u in self.usage})}

    def save(self, lane, stop_reason, contested, agents, blackboard, batch_size=1):
        t = self.totals()
        rid = db.run("""INSERT INTO runs(case_id,created_at,mode,lane,calls,tokens_in,tokens_out,est_cost,latency_ms,stop_reason,
                        contested,agents,blackboard,batch_size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (self.case_id, db.now(), self.mode, lane, round(t["calls"] / batch_size, 2), t["tokens_in"] // batch_size,
                      t["tokens_out"] // batch_size, t["est_cost"] / batch_size, t["latency_ms"], stop_reason, int(contested),
                      json.dumps(agents), json.dumps(blackboard, default=str), batch_size))
        for s in self.spans:
            db.run("INSERT INTO spans(run_id,case_id,agent,kind,name,detail,ms,tokens) VALUES(?,?,?,?,?,?,?,?)",
                   (rid, self.case_id, s["agent"], s["kind"], s["name"], s["detail"], s["ms"], s["tokens"]))
        return rid


@dataclass
class AgentResult:
    output: BaseModel
    stop_reason: StopReason
    calls: int
    tokens: int
    tools_called: list


def _errs(e: ValidationError):
    return "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])


async def run_agent(run: Run, name, system, user, schema, allowed_tools=(), tier="fast", budget: Budget = None,
                    temperature=0.1, max_tokens=1500, escalate=True) -> AgentResult:
    """Escalation ladder: if a fast-tier agent exhausts its repairs, retry ONCE on the strong tier (cost only when needed)."""
    try:
        return await _run_agent(run, name, system, user, schema, allowed_tools, tier, budget, temperature, max_tokens)
    except AgentFailure as e:
        if tier == "fast" and escalate:
            run.span(name, "escalate", "fast -> strong", str(e)[:200])
            return await _run_agent(run, name, system, user, schema, allowed_tools, "strong", budget, temperature, max_tokens)
        raise


async def _run_agent(run: Run, name, system, user, schema, allowed_tools, tier, budget, temperature, max_tokens) -> AgentResult:
    budget = budget or Budget()
    defs = [t for t in tools.TOOL_SCHEMAS if t["function"]["name"] in set(allowed_tools)]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    local, seen, tools_called = [], set(), []
    steps = repairs = 0
    stop, forced = StopReason.FINAL, False
    t_start = time.time()
    while True:
        used = sum(u["in"] + u["out"] for u in local)
        if not forced and used > budget.max_tokens:
            forced, stop = True, StopReason.TOKEN_BUDGET
        if not forced and time.time() - t_start > budget.max_seconds:
            forced, stop = True, StopReason.TIME_BUDGET
        if not forced and steps >= budget.max_steps:
            forced, stop = True, StopReason.STEP_BUDGET
        if forced and not messages[-1].get("_nudged"):
            messages.append({"role": "user", "content": "Budget reached. Reply now with ONLY the final JSON object.", "_nudged": True})
        use_defs = None if (forced or not defs or len(tools_called) >= budget.max_tool_calls) else defs
        steps += 1
        n0 = len(local)
        msg, model = await client.chat([{k: v for k, v in m.items() if k != "_nudged"} for m in messages], tools=use_defs,
                                       tier=tier, sink=local, temperature=temperature, max_tokens=max_tokens)
        for u in local[n0:]:
            run.usage.append(u)
            run.span(name, "llm", u["model"], f"in={u['in']} out={u['out']}", u["ms"], u["in"] + u["out"])
        calls = msg.get("tool_calls") or []
        if calls and use_defs:
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for tc in calls:
                fn = tc.get("function", {})
                tname = fn.get("name")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                key = (tname, json.dumps(args, sort_keys=True))
                if tname not in set(allowed_tools):
                    result = {"error": f"tool '{tname}' is not permitted for this agent"}
                    run.span(name, "violation", tname, "not in allowlist")
                elif key in seen:
                    result = {"note": "identical call already made; answer now"}
                    run.span(name, "no_progress", tname, "duplicate call refused")
                    forced, stop = True, StopReason.NO_PROGRESS
                elif len(tools_called) >= budget.max_tool_calls:
                    result = {"error": "tool budget exhausted; answer now"}
                    forced, stop = True, StopReason.TOOL_BUDGET
                else:
                    t1 = time.time()
                    result = tools.run_tool(tname, args)
                    seen.add(key)
                    tools_called.append(tname)
                    run.span(name, "tool", tname, json.dumps(args) + " -> " + json.dumps(result, default=str)[:200], int((time.time() - t1) * 1000))
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": json.dumps(result, default=str)[:8000]})
            if len(tools_called) >= budget.max_tool_calls and stop == StopReason.FINAL:
                stop = StopReason.TOOL_BUDGET
                forced = True
            continue
        try:
            out = schema.model_validate(client.extract_json(msg.get("content")))
            return AgentResult(out, stop, len(local), sum(u["in"] + u["out"] for u in local), tools_called)
        except (ValueError, ValidationError) as e:
            detail = _errs(e) if isinstance(e, ValidationError) else str(e)
            run.span(name, "repair", "invalid_output", detail)
            if repairs >= 2:
                raise AgentFailure(f"{name}: no valid output after {repairs} repairs ({detail})")
            repairs += 1
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            messages.append({"role": "user", "content": f"Invalid output ({detail}). Reply again with ONLY the JSON object matching the schema."})
