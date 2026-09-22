"""AI-suggested rules mined from confirmed fraud outcomes (app.rule_mining).

Covers: candidate generation is pure/read-only; every candidate validates against the DSL and
fires on the case that seeded it; nothing is ever saved as active; the fairness fix that excludes
high-cardinality `state` from mining; backtesting against real outcomes; novelty dedup against an
existing active rule; the LLM naming step is cosmetic-only, optional, and cannot alter the logic;
retracting a fraud outcome cleans up un-promoted suggestions but leaves a promoted one alone; the
HTTP surface (mark outcome -> GET suggested -> PUT to activate).
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import customrules, db, main, related, rule_mining, rulestore, service
from app.llm import client

ME = ("Alex Rivera", "investigator")
SUP = ("Sam Okafor", "supervisor")


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    related.reset()
    rulestore.invalidate()
    service.ingest()
    yield
    related.reset()
    rulestore.invalidate()


def a_low_score_case():
    """Any case the rule engine currently rates low/clean — the realistic 'missed it' scenario."""
    cases = service.list_cases({"sort": "score", "order": "asc"})
    return cases[5]


def stub_llm(monkeypatch, replies):
    it = iter(replies)

    async def fake(messages, tools=None, tier="fast", sink=None, **kw):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return {"content": r}, "fake/model"
    monkeypatch.setattr(client, "chat", fake)


# ---------------------------------------------------------------- mining is pure and deterministic
def test_mine_is_read_only():
    victim = a_low_score_case()
    service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    before = {r["rule_id"] for r in rulestore.all_rules() if r["kind"] == "custom"}
    rule_mining.mine(victim["case_id"])
    rule_mining.mine(victim["case_id"])
    after = {r["rule_id"] for r in rulestore.all_rules() if r["kind"] == "custom"}
    assert before == after  # mine() never writes; only mine_and_save() does, and mark_outcome already ran it once


def test_mine_only_fires_on_fraud_outcomes():
    victim = a_low_score_case()
    assert rule_mining.mine(victim["case_id"]) == []  # no outcome recorded yet
    service.mark_outcome(victim["case_id"], "LEGITIMATE", "checked out fine", *ME)
    assert rule_mining.mine(victim["case_id"]) == []  # LEGITIMATE never seeds a suggestion


# ---------------------------------------------------------------- what mark_outcome(FRAUD) produces
def test_marking_fraud_saves_shadow_candidates_that_validate_and_fire_on_the_source_case():
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "FRAUD", "provider admitted billing pattern on audit", *ME)
    assert res["suggested_rules"], "expected at least one mined candidate"
    sugg = rulestore.suggested()
    assert {r["rule_id"] for r in sugg} == set(res["suggested_rules"])
    for r in sugg:
        assert r["status"] == "shadow" and r["origin"] == "ai_outcome_mining"
        assert r["source_case_id"] == victim["case_id"]
        full = rulestore.get(r["rule_id"])
        customrules.validate_logic(full["logic"])  # raises RuleError if the DSL is somehow invalid
        assert customrules.fire(full, victim["data"]) is not None, "a mined rule must fire on the case that seeded it"
        assert r["mined_stats"]["background_rate"] <= rule_mining.MAX_P + 1e-9


def test_legitimate_outcome_mines_nothing():
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "LEGITIMATE", "confirmed clean", *ME)
    assert res["suggested_rules"] == []
    assert rulestore.suggested() == []


def test_mined_rules_are_never_active_and_never_move_a_live_score(monkeypatch):
    """Isolate the mining feature from the PRE-EXISTING R17 lookalike effect (marking any case FRAUD
    legitimately re-scores its nearest neighbours -- that is documented, expected behaviour and not
    what this test is about). Compare mining on vs. mining stubbed to a no-op: the two must produce
    IDENTICAL scores everywhere, proving the shadow rules themselves never touch a live score."""
    victim = a_low_score_case()

    real_mine_and_save = rule_mining.mine_and_save
    monkeypatch.setattr(rule_mining, "mine_and_save", lambda *a, **k: [])
    service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    baseline_scores = {c["case_id"]: c["score"] for c in service.list_cases({})}
    service.retract_outcome(victim["case_id"], *ME)

    # restore ONLY this one attribute -- monkeypatch.undo() would also undo the autouse tmpdb
    # fixture's db.DB_PATH patch (same monkeypatch instance), which once genuinely leaked a
    # mark_outcome call through to the real data/cases.db. Never call undo() mid-test.
    monkeypatch.setattr(rule_mining, "mine_and_save", real_mine_and_save)
    service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    assert rulestore.suggested()  # mining actually ran and saved something this time
    cfg = rulestore.load_config()  # active only -- shadow rules must not appear here
    active_custom_ids = {r["rule_id"] for r in rulestore.all_rules() if r["kind"] == "custom" and r["status"] == "active"}
    assert not ({r["rule_id"] for r in rulestore.suggested()} & active_custom_ids)
    mined_scores = {c["case_id"]: c["score"] for c in service.list_cases({})}
    assert mined_scores == baseline_scores, "a shadow-only rule changed a live score"


# ---------------------------------------------------------------- the fairness / small-sample fix
def test_state_is_excluded_from_mining_fields():
    """Regression guard: a 19-way state field with 1-2 cases each produced spurious 'state==WA'
    rules on first implementation (near-zero background rate purely from tiny category size, not
    signal). state is now excluded entirely; care_type (5 values, well-populated) is kept."""
    assert "state" not in rule_mining.CATEGORY_FIELDS
    assert rule_mining.CATEGORY_FIELDS == ["care_type"]


def test_no_mined_candidate_ever_keys_on_raw_state():
    victim = a_low_score_case()
    service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    for r in rulestore.suggested():
        assert "state" not in customrules.referenced_fields(r["logic_tree"])


# ---------------------------------------------------------------- backtested against real outcomes
def test_mined_candidates_carry_a_real_outcomes_backtest():
    victim = a_low_score_case()
    service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    for r in rulestore.suggested():
        bt = r["mined_stats"]["backtest"]
        assert bt is not None
        assert bt["positives"] >= 1
        assert bt["recall"] == 1.0  # by construction it fires on every known fraud case it was built from


# ---------------------------------------------------------------- novelty / dedup against an active rule
def test_does_not_resuggest_a_pattern_an_active_rule_already_covers():
    victim = a_low_score_case()
    service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)  # records the outcome mine() needs to see
    candidates = rule_mining.mine(victim["case_id"])
    assert candidates, "test needs at least one real candidate to try to duplicate"
    top = candidates[0]
    # promote an ACTIVE custom rule with exactly that logic, then mine again -- it must not be re-suggested
    rulestore.create({"name": "Pre-existing", "description": "d", "level": top["level"], "domain": top["domain"],
                      "status": "active", "logic": top["logic"]}, *ME)
    saved = rule_mining.mine_and_save(victim["case_id"])
    saved_logics = {customrules.describe(r["logic"]) for r in saved}
    assert customrules.describe(top["logic"]) not in saved_logics


# ---------------------------------------------------------------- LLM naming: optional, cosmetic, bounded
def test_llm_unavailable_leaves_the_deterministic_name_and_description(monkeypatch):
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    before = {r["rule_id"]: (r["name"], r["description"]) for r in rulestore.suggested()}

    async def boom(*a, **k):
        raise client.LLMError("no key configured")
    monkeypatch.setattr(client, "chat", boom)
    asyncio.run(rule_mining.polish_names(res["suggested_rules"]))

    after = {r["rule_id"]: (r["name"], r["description"]) for r in rulestore.suggested()}
    assert before == after
    for name, desc in after.values():
        assert 3 <= len(name) <= 80 and desc  # still a fully usable, non-empty suggestion


def test_llm_polish_updates_name_when_available(monkeypatch):
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    rid = res["suggested_rules"][0]
    stub_llm(monkeypatch, [json.dumps({"name": "Low-distance low-utilization pattern", "description": "Clear one-liner."})])
    asyncio.run(rule_mining.polish_names([rid]))
    updated = rulestore.get(rid)
    assert updated["name"] == "Low-distance low-utilization pattern"
    assert updated["description"] == "Clear one-liner."


def test_llm_cannot_alter_the_mined_logic_even_if_it_tries(monkeypatch):
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    rid = res["suggested_rules"][0]
    original_logic = rulestore.get(rid)["logic"]
    evil = json.dumps({"name": "Renamed", "description": "d",
                       "logic": {"op": "AND", "conds": [{"field": "claim_amount_usd", "cmp": ">=", "value": 999999}]}})
    stub_llm(monkeypatch, [evil])
    asyncio.run(rule_mining.polish_names([rid]))
    assert rulestore.get(rid)["logic"] == original_logic  # only name/description are ever read off the reply


def test_llm_naming_ignores_rules_it_did_not_author(monkeypatch):
    r = rulestore.create({"name": "Human rule", "description": "d", "level": "HIGH", "domain": "Billing",
                          "status": "shadow", "logic": {"op": "AND", "conds": [{"field": "duplicate_service_billed", "cmp": "==", "value": 1}]}}, *ME)
    calls = []

    async def fake(*a, **k):
        calls.append(1)
        return {"content": "{}"}, "m"
    monkeypatch.setattr(client, "chat", fake)
    asyncio.run(rule_mining.polish_names([r["id"]]))
    assert not calls  # origin is None (human-authored) -- polish_names must skip it


# ---------------------------------------------------------------- retracting a fraud outcome
def test_retracting_fraud_discards_unpromoted_suggestions():
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    assert res["suggested_rules"]
    service.retract_outcome(victim["case_id"], *ME)
    for rid in res["suggested_rules"]:
        assert rulestore.get(rid) is None


def test_retracting_fraud_leaves_a_promoted_suggestion_alone():
    victim = a_low_score_case()
    res = service.mark_outcome(victim["case_id"], "FRAUD", "miss", *ME)
    promoted = res["suggested_rules"][0]
    rulestore.update(promoted, {"status": "active"}, *SUP)
    service.retract_outcome(victim["case_id"], *ME)
    assert rulestore.get(promoted) is not None
    assert rulestore.get(promoted)["status"] == "active"  # a human's decision stands


# ---------------------------------------------------------------- HTTP surface
def test_api_outcome_to_suggested_to_activate():
    c = TestClient(main.app)
    victim = a_low_score_case()
    h = {"X-Actor": "Alex Rivera", "X-Role": "investigator"}
    r = c.post(f"/api/cases/{victim['case_id']}/outcome", json={"outcome": "FRAUD", "reason": "miss"}, headers=h)
    assert r.status_code == 200 and r.json()["suggested_rules"]

    sugg = c.get("/api/rules/suggested").json()
    ids = {x["rule_id"] for x in sugg}
    assert ids == set(r.json()["suggested_rules"])
    assert all(x["status"] == "shadow" and x["origin"] == "ai_outcome_mining" for x in sugg)

    rid = sugg[0]["rule_id"]
    hs = {"X-Actor": "Sam Okafor", "X-Role": "supervisor"}
    up = c.put(f"/api/rules/{rid}", json={"status": "active"}, headers=hs)
    assert up.status_code == 200

    remaining = {x["rule_id"] for x in c.get("/api/rules/suggested").json()}
    assert rid not in remaining  # promoted rules drop off the pending-review list
    assert rid in {x["rule_id"] for x in c.get("/api/rules").json()}
