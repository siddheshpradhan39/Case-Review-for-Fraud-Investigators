"""Rules tab: custom rules, built-in tuning, shadow mode, rescoring, backtest math, leave-one-out labels, API, AI drafting."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import backtest, customrules, db, main, related, rulestore, service
from app.customrules import RuleError
from app.llm import client, rule_draft

WKND = {"op": "AND", "conds": [{"field": "weekend_billing_ratio", "cmp": ">=", "value": 0.4},
                               {"field": "round_dollar_billing_ratio", "cmp": ">=", "value": 0.4}]}
ME = ("Alex Rivera", "investigator")


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    related.reset()
    rulestore.invalidate()
    service.ingest()
    yield
    related.reset()
    rulestore.invalidate()


def cases():
    return {r["case_id"]: r for r in service.list_cases({})}


def expected_ids(pred):
    return {cid for cid, r in cases().items() if pred(r["data"])}


def new_rule(**kw):
    return rulestore.create({"name": "Weekend and round-dollar", "description": "d", "level": "HIGH", "domain": "Billing", "logic": WKND, **kw}, *ME)


# ---------------------------------------------------------------- DSL
def test_dsl_rejects_bad_rules():
    bad = [{"op": "AND", "conds": [{"field": "nope", "cmp": ">=", "value": 1}]},
           {"op": "AND", "conds": [{"field": "care_type", "cmp": ">=", "value": "x"}]},
           {"op": "AND", "conds": [{"field": "duplicate_service_billed", "cmp": "==", "value": 7}]},
           {"op": "AND", "conds": [{"field": "state", "cmp": "in", "value": []}]},
           {"op": "AND", "conds": [{"field": "claim_amount_usd", "cmp": ">", "value": float("inf")}]},
           {"op": "AND", "conds": []}, {"op": "XOR", "conds": [{"field": "state", "cmp": "==", "value": "FL"}]},
           {"op": "AND", "conds": [{"field": "state", "cmp": "==", "value": "FL"}] * 13}]
    for b in bad:
        with pytest.raises(RuleError):
            customrules.validate_logic(b)
    deep = {"op": "AND", "conds": [{"field": "state", "cmp": "==", "value": "FL"}]}
    for _ in range(4):
        deep = {"op": "OR", "conds": [deep]}
    with pytest.raises(RuleError):
        customrules.validate_logic(deep)


def test_dsl_nested_evaluation():
    logic = {"op": "AND", "conds": [{"field": "weekend_billing_ratio", "cmp": ">=", "value": 0.4},
                                    {"op": "OR", "conds": [{"field": "care_type", "cmp": "in", "value": ["Adult Day Care"]},
                                                           {"field": "duplicate_service_billed", "cmp": "==", "value": 1}]}]}
    assert customrules.evaluate_logic(logic, {"weekend_billing_ratio": .5, "care_type": "Adult Day Care", "duplicate_service_billed": 0})[0]
    assert customrules.evaluate_logic(logic, {"weekend_billing_ratio": .5, "care_type": "Skilled Nursing", "duplicate_service_billed": 1})[0]
    assert not customrules.evaluate_logic(logic, {"weekend_billing_ratio": .5, "care_type": "Skilled Nursing", "duplicate_service_billed": 0})[0]
    assert not customrules.evaluate_logic(logic, {"weekend_billing_ratio": .1, "care_type": "Adult Day Care", "duplicate_service_billed": 1})[0]


def test_validation_of_name_level_domain():
    for bad in ({"name": "ab"}, {"level": "SEVERE"}, {"domain": "Voodoo"}, {"status": "maybe"}, {"name": "Duplicate service billed"}):
        with pytest.raises(RuleError):
            new_rule(**bad)
    new_rule()
    with pytest.raises(RuleError):
        new_rule()  # duplicate name


# ---------------------------------------------------------------- engine effects
def test_new_rule_fires_on_all_matching_cases_and_rescores():
    want = expected_ids(lambda d: d["weekend_billing_ratio"] >= .4 and d["round_dollar_billing_ratio"] >= .4)
    assert want
    before = {c: r["score"] for c, r in cases().items()}
    rid = new_rule(domain="Custom")["id"]      # its own domain, so it adds independent evidence
    res = service.rescore()
    after = cases()
    assert {c for c, r in after.items() if rid in r["fired_ids"]} == want
    assert res["rules_changed"] == len(want) and res["score_changes"] >= 1
    for c in want:
        r = [x for x in after[c]["rules"] if x["rule_id"] == rid][0]
        assert r["level"] == "HIGH" and "weekend_billing_ratio" in r["fields"] and "custom rule" in r["evidence"]
    assert all(after[c]["score"] >= before[c] for c in want)
    assert any(after[c]["score"] != before[c] for c in want)


def test_shadow_rule_does_not_score_but_is_backtested_then_promoted():
    rid = new_rule(status="shadow")["id"]
    before = {c: r["score"] for c, r in cases().items()}
    service.rescore()
    assert {c: r["score"] for c, r in cases().items()} == before
    assert all(rid not in r["fired_ids"] for r in cases().values())
    row = [r for r in backtest.leaderboard()["rows"] if r["rule_id"] == rid][0]
    assert row["fires"] > 0 and row["status"] == "shadow"
    rulestore.update(rid, {"status": "active"}, *ME)
    service.rescore()
    assert any(rid in r["fired_ids"] for r in cases().values())


def test_disable_builtin_recomputes_composites_and_reset_restores():
    base = cases()
    r05 = {c for c, r in base.items() if "R05" in r["fired_ids"]}
    r13 = {c for c, r in base.items() if "R13" in r["fired_ids"]}
    assert r05
    rulestore.set_override("R05", {"enabled": False}, *ME)
    res = service.rescore()
    after = cases()
    assert not any("R05" in r["fired_ids"] for r in after.values())
    assert {c for c, r in after.items() if "R13" in r["fired_ids"]} <= r13 and res["rules_changed"] >= len(r05)
    rulestore.reset("R05", *ME)
    service.rescore()
    assert {c for c, r in cases().items() if "R05" in r["fired_ids"]} == r05


def test_threshold_override_changes_fire_counts():
    base = {c for c, r in cases().items() if "R05" in r["fired_ids"]}
    rulestore.set_override("R05", {"elevated": 9}, *ME)          # was 5.5
    service.rescore()
    tuned = {c for c, r in cases().items() if "R05" in r["fired_ids"]}
    assert tuned < base and all(cases()[c]["data"]["weekly_visit_frequency"] >= 9 for c in tuned)
    with pytest.raises(RuleError):
        rulestore.set_override("R05", {"elevated": 20, "extreme": 10}, *ME)
    with pytest.raises(RuleError):
        rulestore.set_override("R01", {"elevated": 3}, *ME)    # no thresholds on a binary rule


def test_level_override_on_single_level_rule():
    rulestore.set_override("R01", {"level": "LOW"}, *ME)
    service.rescore()
    lv = {x["level"] for r in cases().values() for x in r["rules"] if x["rule_id"] == "R01"}
    assert lv == {"LOW"}
    with pytest.raises(RuleError):
        rulestore.set_override("R05", {"level": "LOW"}, *ME)    # tiered rule: tune thresholds instead


def test_custom_rule_domain_routes_to_specialist():
    from app.agents.domains import specialists_for
    assert specialists_for([], [], [{"rule_id": "X01", "domain": "Billing"}]) == ["billing"]
    assert specialists_for([], [], [{"rule_id": "X01", "domain": "Custom"}]) == []


# ---------------------------------------------------------------- backtest math + labels
def test_metrics_math_hand_built():
    pred = {"a": True, "b": True, "c": True, "d": False, "e": False, "f": False}
    lab = {"a": True, "b": True, "c": False, "d": True, "e": False, "f": False}
    m, cells = backtest._metrics(pred, lab)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 2) and cells["fp"] == ["c"]
    assert m["precision"] == pytest.approx(2 / 3, abs=1e-3) and m["recall"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["f1"] == pytest.approx(2 / 3, abs=1e-3) and m["base_rate"] == pytest.approx(0.5) and m["lift"] == pytest.approx(1.33, abs=0.01)


def test_wilson_interval():
    assert backtest.wilson(0, 0) == [None, None]
    lo, hi = backtest.wilson(8, 10)
    assert 0 <= lo < 0.8 < hi <= 1
    assert backtest.wilson(10, 10)[1] == 1.0 and backtest.wilson(0, 10)[0] == 0.0


def test_leave_one_out_label_does_not_score_a_rule_against_itself():
    """A CRITICAL rule that fires on EVERY case lifts every score. Against labels that included it, it would look perfect
    (recall 1 AND precision 1); under leave-one-out its precision must equal the base rate."""
    rid = rulestore.create({"name": "Always on", "description": "", "level": "CRITICAL", "domain": "Custom",
                            "logic": {"op": "AND", "conds": [{"field": "binary_flag_count", "cmp": ">=", "value": 0}]}}, *ME)["id"]
    service.rescore()
    assert all(s["score"] >= 40 for s in cases().values())              # it lifted everything
    r = backtest.backtest_rule(rid, None, "loo", "HIGH", with_impact=False)
    assert r["recall"] == 1.0 and r["precision"] == r["base_rate"] and r["precision"] < 0.5


def test_outcomes_and_blend_label_modes():
    service.mark_outcome("C1006", "FRAUD", "confirmed", *ME)
    service.mark_outcome("C1004", "LEGITIMATE", "checked", *ME)
    r = backtest.backtest_rule("R07", None, "outcomes", "HIGH", with_impact=False)
    assert r["n"] == 2 and r["excluded"] == 48 and r["positives"] == 1
    assert "C1006" in r["cases"]["tp"] and "C1004" in r["cases"]["tn"]
    assert any("Small sample" in w for w in r["warnings"])
    b = backtest.backtest_rule("R07", None, "blend", "HIGH", with_impact=False)
    assert b["n"] == 50 and "C1004" in (b["cases"]["tn"] + b["cases"]["fn"])


def test_draft_backtest_reports_impact_without_saving():
    r = backtest.backtest_rule(None, {"name": "x rule", "description": "", "level": "HIGH", "domain": "Custom", "logic": WKND}, "loo", "HIGH")
    assert r["fires_on"] and r["impact"]["score_changes"] >= 1 and not r["impact"]["no_effect_on"]
    assert rulestore.all_rules() and not [x for x in rulestore.all_rules() if x["kind"] == "custom"]   # nothing persisted


def test_rule_in_an_already_covered_domain_fires_but_changes_nothing():
    """Domain-capped scoring: the 8 worst cases already have a HIGH Billing rule, so another HIGH Billing rule adds no score.
    The impact preview must say so instead of implying the rule matters."""
    r = backtest.backtest_rule(None, {"name": "x rule", "description": "", "level": "HIGH", "domain": "Billing", "logic": WKND}, "loo", "HIGH")
    assert r["fires_on"] and set(r["impact"]["no_effect_on"]) == set(r["fires_on"]) and r["impact"]["score_changes"] == 0


def test_draft_edit_of_builtin_threshold_backtest():
    base = backtest.backtest_rule("R05", None, "loo", "HIGH", with_impact=False)
    tuned = backtest.backtest_rule("R05", {"params": {"elevated": 9}}, "loo", "HIGH")
    assert len(tuned["fires_on"]) < len(base["fires_on"]) and tuned["impact"]["score_changes"] >= 1


def test_leaderboard_covers_all_rules():
    rows = backtest.leaderboard()["rows"]
    assert len(rows) >= 17 and all("precision" in r for r in rows)


# ---------------------------------------------------------------- versioning, audit, API
def test_versions_and_audit():
    rid = new_rule()["id"]
    rulestore.update(rid, {"level": "MEDIUM"}, *ME)
    rulestore.set_override("R06", {"enabled": False}, *ME)
    h = rulestore.history(rid)
    assert [x["version"] for x in h] == [2, 1] and h[0]["definition"]["level"] == "MEDIUM"
    acts = {a["action"] for a in service.audit_log(50)}
    assert {"RULE_CREATED", "RULE_UPDATED", "RULE_TUNED"} <= acts


def test_api_roundtrip():
    c = TestClient(main.app)
    H = {"X-Actor": "Alex Rivera", "X-Role": "investigator"}
    r = c.post("/api/rules", json={"name": "Weekend and round-dollar", "level": "HIGH", "domain": "Billing", "logic": WKND}, headers=H)
    assert r.status_code == 200 and r.json()["rule"]["id"] == "X01" and r.json()["rescore"]["rules_changed"] >= 1
    assert c.post("/api/rules", json={"name": "Weekend and round-dollar", "level": "HIGH", "domain": "Billing", "logic": WKND}, headers=H).status_code == 400
    assert c.post("/api/rules", json={"name": "Bad one", "level": "HIGH", "domain": "Billing", "logic": {"op": "AND", "conds": [{"field": "zzz", "cmp": ">", "value": 1}]}}, headers=H).status_code == 400
    assert c.put("/api/rules/X01", json={"level": "MEDIUM"}, headers=H).json()["rule"]["level"] == "MEDIUM"
    assert c.put("/api/rules/R05", json={"enabled": False}, headers=H).status_code == 200
    bt = c.post("/api/rules/backtest", json={"rule_id": "X01", "label_mode": "loo"}).json()
    assert bt["n"] == 50 and "impact" in bt
    assert c.post("/api/rules/backtest", json={"rule_id": None, "draft": {"name": "q", "level": "LOW", "domain": "Billing", "logic": WKND}}).status_code == 400  # name too short
    lb = c.get("/api/rules/leaderboard?label_mode=loo&positive_at=HIGH").json()
    assert any(x["rule_id"] == "X01" for x in lb["rows"]) and [x for x in lb["rows"] if x["rule_id"] == "R05"][0]["status"] == "disabled"
    assert c.get("/api/rules/leaderboard?label_mode=bogus").status_code == 400
    assert c.delete("/api/rules/R05", headers=H).status_code == 400
    assert c.post("/api/rules/R05/reset", headers=H).status_code == 200
    assert c.delete("/api/rules/X01", headers=H).status_code == 200
    assert not [r for r in c.get("/api/rules").json() if r["kind"] == "custom"]
    assert any(r["rule_id"] == "R05" and r["status"] == "active" for r in c.get("/api/rules").json())


# ---------------------------------------------------------------- AI drafting (LLM stubbed)
def stub_llm(monkeypatch, replies):
    it = iter(replies)

    async def fake(messages, tools=None, tier="fast", sink=None, **kw):
        return {"content": next(it)}, "fake/model"
    monkeypatch.setattr(client, "chat", fake)


def test_ai_draft_valid(monkeypatch):
    stub_llm(monkeypatch, [json.dumps({"name": "Weekend heavy", "description": "d", "level": "MEDIUM", "domain": "Billing", "logic": WKND})])
    d = asyncio.run(rule_draft.draft("flag claims with heavy weekend billing and lots of round dollar charges"))
    assert d["logic"] == WKND and d["level"] == "MEDIUM"
    assert not [x for x in rulestore.all_rules() if x["kind"] == "custom"]   # a draft is never saved


def test_ai_draft_hallucinated_field_is_repaired_once_then_rejected(monkeypatch):
    bad = json.dumps({"name": "Provider fraud", "level": "HIGH", "domain": "Billing", "logic": {"op": "AND", "conds": [{"field": "provider_license_status", "cmp": "==", "value": 1}]}})
    stub_llm(monkeypatch, [bad, bad])
    with pytest.raises(RuleError):
        asyncio.run(rule_draft.draft("providers with revoked licenses billing us"))
    good = json.dumps({"name": "Fixed rule", "level": "LOW", "domain": "Billing", "logic": WKND})
    stub_llm(monkeypatch, [bad, good])
    assert asyncio.run(rule_draft.draft("providers with revoked licenses billing us"))["name"] == "Fixed rule"


def test_ai_draft_can_decline(monkeypatch):
    stub_llm(monkeypatch, [json.dumps({"error": "there is no provider data"})])
    with pytest.raises(RuleError, match="could not express"):
        asyncio.run(rule_draft.draft("providers with a suspended license"))


def test_reenabling_a_rule_leaves_no_tuned_override():
    rulestore.set_override("R05", {"enabled": False}, *ME)
    assert [r for r in rulestore.all_rules() if r["rule_id"] == "R05"][0]["tuned"]
    rulestore.set_override("R05", {"enabled": True}, *ME)
    assert not [r for r in rulestore.all_rules() if r["rule_id"] == "R05"][0]["tuned"]
    assert db.one("SELECT COUNT(*) n FROM rule_overrides")["n"] == 0
