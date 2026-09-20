from app.llm.guardrails import apply_guardrails, ground_indicator, ungrounded_figures
from app.llm.client import extract_json

CASE = {"weekly_visit_frequency": 12, "claim_amount_usd": 32373, "linked_case_ids": ["C1031"]}
FIRED = [{"rule_id": "R05", "level": "HIGH"}, {"rule_id": "R01", "level": "HIGH"}]
BASE = {"summary": "12 visits per week on a $32,373 claim.", "score_adjustment": 0, "recommended_action": "ROUTINE_REVIEW",
        "key_indicators": [{"rule_id": "R05", "field": "weekly_visit_frequency", "value": 12, "why": "x"}]}


def run(raw, fired=FIRED, score=70.0, notes=()):
    return apply_guardrails(raw, CASE, fired, score, {"c": CASE}, list(notes))


def test_grounded_indicator_kept():
    res, s, lvl, v, w = run(BASE)
    assert len(res["key_indicators"]) == 1 and not w


def test_wrong_value_dropped():
    bad = {**BASE, "key_indicators": [{"rule_id": "R05", "field": "weekly_visit_frequency", "value": 3, "why": "x"}]}
    res, *_, w = run(bad)
    assert res["key_indicators"] == [] and any("record says 12" in x for x in w)


def test_rule_that_did_not_fire_dropped():
    ok, why = ground_indicator({"rule_id": "R09", "field": "weekly_visit_frequency", "value": 12}, CASE, {"R05"}, "")
    assert not ok and "did not fire" in why


def test_note_quote_must_exist():
    assert ground_indicator({"field": "note", "value": "member relocated"}, CASE, set(), "Provider said member relocated in March")[0]
    assert not ground_indicator({"field": "note", "value": "confessed to fraud"}, CASE, set(), "Provider said member relocated")[0]


def test_adjustment_clamped_and_level_derived():
    res, score, level, verdict, w = run({**BASE, "score_adjustment": -80}, score=70.0)
    assert res["score_adjustment"] == -20 and score == 50.0 and level == "HIGH" and verdict.startswith("DOWNGRADE")
    assert any("clamped" in x for x in w)


def test_cannot_clear_case_with_high_rule():
    res, *_, w = run({**BASE, "recommended_action": "CLEAR_FALSE_POSITIVE", "score_adjustment": -20})
    assert res["recommended_action"] == "ROUTINE_REVIEW" and any("refused" in x for x in w)


def test_clear_allowed_on_clean_case():
    res, score, level, *_ = run({**BASE, "recommended_action": "CLEAR_FALSE_POSITIVE", "key_indicators": []}, fired=[], score=3.0)
    assert res["recommended_action"] == "CLEAR_FALSE_POSITIVE" and level == "LOW"


def test_invented_figures_flagged():
    assert ungrounded_figures("Claim of $99,000", "amount 32373") == [99000.0]
    assert ungrounded_figures("Claim of $32.4k", "amount 32373") == []


def test_extract_json_handles_fences_and_chatter():
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert extract_json('blah {"x": 1} trailing') == {"x": 1}


def test_linked_case_floor_blocks_downgrade():
    res, score, level, verdict, w = apply_guardrails({**BASE, "score_adjustment": -15}, CASE, FIRED, 30.0, {"c": CASE}, [], linked_levels=("CRITICAL",))
    assert res["score_adjustment"] == 0 and score == 30.0 and any("linked-case floor" in x for x in w)


def test_linked_floor_does_not_block_upgrade():
    res, score, *_ = apply_guardrails({**BASE, "score_adjustment": 10}, CASE, FIRED, 30.0, {"c": CASE}, [], linked_levels=("CRITICAL",))
    assert score == 40.0
