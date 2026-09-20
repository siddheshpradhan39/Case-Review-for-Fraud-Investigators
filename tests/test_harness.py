"""Agent harness behaviour, verified with a scripted fake LLM (no network): budgets, allowlists, no-progress,
repair loop, fail-closed, accounting, breaker, replay."""
import asyncio
import json

import pytest

from app import service
from app.agents import runtime
from app.agents.runtime import AgentFailure, Budget, Run, StopReason, run_agent
from app.agents.schemas import Finding
from app.llm import client

GOOD = json.dumps({"direction": "suspicious", "strength": 3, "summary": "s", "evidence": []})


def tc(name, args=None, i="1"):
    return {"content": "", "tool_calls": [{"id": i, "function": {"name": name, "arguments": json.dumps(args or {})}}]}


@pytest.fixture(autouse=True)
def _db():
    service.ingest()


@pytest.fixture
def script(monkeypatch):
    """script(list_of_replies) -> records the `tools` argument each call received."""
    seen = []

    def install(replies):
        it = iter(replies)

        async def fake(messages, tools=None, tier="fast", sink=None, **kw):
            seen.append(tools)
            r = next(it)
            if sink is not None:
                sink.append({"model": "fake/model", "tier": tier, "in": 100, "out": 50, "ms": 5})
            return (r if isinstance(r, dict) else {"content": r}), "fake/model"
        monkeypatch.setattr(client, "chat", fake)
        return seen
    return install


def go(run, **kw):
    return asyncio.run(run_agent(run, "t", "sys", "user", Finding, **kw))


def test_valid_output_stops_final(script):
    script([GOOD])
    res = go(Run("C1001"))
    assert res.stop_reason == StopReason.FINAL and res.output.strength == 3 and res.calls == 1


def test_tool_budget_removes_tools_after_limit(script):
    seen = script([tc("get_peer_stats", {"care_type": "Home Health Aide"}), GOOD])
    res = go(Run("C1001"), allowed_tools=["get_peer_stats"], budget=Budget(max_tool_calls=1))
    assert res.tools_called == ["get_peer_stats"] and res.stop_reason == StopReason.TOOL_BUDGET
    assert seen[0] is not None and seen[1] is None  # second call is forced final: no tools offered


def test_allowlist_violation_refused_and_recorded(script):
    script([tc("get_case", {"case_id": "C1001"}), GOOD])
    run = Run("C1001")
    res = go(run, allowed_tools=["get_peer_stats"], budget=Budget(max_tool_calls=2))
    assert res.tools_called == [] and any(s["kind"] == "violation" for s in run.spans)


def test_duplicate_call_is_no_progress(script):
    args = {"care_type": "Adult Day Care"}
    script([tc("get_peer_stats", args, "1"), tc("get_peer_stats", args, "2"), GOOD])
    run = Run("C1001")
    res = go(run, allowed_tools=["get_peer_stats"], budget=Budget(max_tool_calls=3))
    assert res.tools_called == ["get_peer_stats"] and res.stop_reason == StopReason.NO_PROGRESS
    assert any(s["kind"] == "no_progress" for s in run.spans)


def test_repair_loop_recovers(script):
    script(["not json at all", '{"direction": "maybe"}', GOOD])
    run = Run("C1001")
    res = go(run)
    assert res.output.direction == "suspicious" and sum(1 for s in run.spans if s["kind"] == "repair") == 2


def test_fail_closed_after_repairs(script):
    script(["nope", "nope", "nope"])
    with pytest.raises(AgentFailure):
        go(Run("C1001"), escalate=False)


def test_step_budget_forces_final(script):
    seen = script([tc("get_peer_stats", {"care_type": "x"}, "1"), tc("get_peer_stats", {"care_type": "y"}, "2"), GOOD])
    res = go(Run("C1001"), allowed_tools=["get_peer_stats"], budget=Budget(max_steps=2, max_tool_calls=5))
    assert res.stop_reason == StopReason.STEP_BUDGET and seen[-1] is None


def test_accounting_and_price():
    run = Run("C1001")
    run.usage += [{"model": "a", "tier": "fast", "in": 1_000_000, "out": 0, "ms": 1},
                  {"model": "b", "tier": "strong", "in": 0, "out": 1_000_000, "ms": 1}]
    t = run.totals()
    assert t["calls"] == 2 and t["est_cost"] == pytest.approx(0.25 + 15)


def test_circuit_breaker_lengthens_cooldown():
    client._fails.clear(); client._cooldown.clear()
    client._trip("m"); first = client._cooldown["m"]
    client._trip("m"); client._trip("m")
    assert client._cooldown["m"] - first > 30 and client._fails["m"] == 3


def test_replay_miss_raises(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(client, "_cassette", {})
    with pytest.raises(client.LLMError):
        asyncio.run(client.chat([{"role": "user", "content": "unrecorded"}]))


def test_escalates_to_strong_tier_after_failed_repairs(monkeypatch):
    tiers = []

    async def fake(messages, tools=None, tier="fast", sink=None, **kw):
        tiers.append(tier)
        if sink is not None:
            sink.append({"model": "fake/model", "tier": tier, "in": 10, "out": 5, "ms": 1})
        return {"content": GOOD if tier == "strong" else "garbage"}, "fake/model"
    monkeypatch.setattr(client, "chat", fake)
    run = Run("C1001")
    res = asyncio.run(run_agent(run, "t", "s", "u", Finding))
    assert res.output.strength == 3 and tiers[-1] == "strong" and tiers[0] == "fast"
    assert any(s["kind"] == "escalate" for s in run.spans)


def test_no_escalation_when_disabled(monkeypatch):
    async def fake(messages, tools=None, tier="fast", sink=None, **kw):
        return {"content": "garbage"}, "fake/model"
    monkeypatch.setattr(client, "chat", fake)
    with pytest.raises(AgentFailure):
        asyncio.run(run_agent(Run("C1001"), "t", "s", "u", Finding, escalate=False))
