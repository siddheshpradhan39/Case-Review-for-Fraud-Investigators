"""Note deletion (full purge), cross-case knowledge, and confirmed-outcome feedback. Runs on a throwaway DB."""
import json

import pytest

from app import db, related, service
from app.agents import evidence
from app.llm import tools
from app.llm.guardrails import apply_guardrails

NOTE = "Provider confirmed by phone that the member relocated to Tampa in March; visit logs requested."


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    related.reset()
    service.ingest()
    yield
    related.reset()


def fake_assessment(case_id, text):
    db.run("""INSERT INTO assessments(case_id,created_at,input_hash,status,model,rule_score,rule_level,ai_score,ai_level,verdict,action,result,warnings,trace)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (case_id, db.now(), "x", "OK", "m", 50, "HIGH", 50, "HIGH", "CONFIRM", "ROUTINE_REVIEW",
                                                     json.dumps({"summary": text, "key_indicators": [], "mitigating_factors": []}), "[]", "[]"))


def test_delete_note_purges_every_trace():
    h0 = service.input_hash("C1011")
    nid = service.add_note("C1011", NOTE, "alex", "investigator")
    assert service.input_hash("C1011") != h0
    fake_assessment("C1011", "Per the note: " + NOTE.lower())
    fake_assessment("C1015", "Another case quoted: " + NOTE)          # a different case that quoted it
    db.run("INSERT INTO chat(case_id,role,content,ts) VALUES('C1011','assistant',?,?)", ("The note says " + NOTE, db.now()))
    purged = service.delete_note("C1011", nid, "alex", "investigator")
    assert purged["assessments"] == 2 and purged["chat_messages"] == 1 and purged["audit_redacted"] >= 1
    assert service.notes_for("C1011") == []
    assert tools.get_case_notes("C1011")["notes"] == []
    assert tools.search_notes("relocated Tampa")["matches"] == []
    everything = json.dumps(db.rows("SELECT detail FROM audit")) + json.dumps(db.rows("SELECT content FROM chat")) + json.dumps(db.rows("SELECT result FROM assessments"))
    assert "Tampa" not in everything
    assert any(a["action"] == "NOTE_DELETED" for a in service.audit_log(50, "C1011"))   # the deletion itself stays audited
    assert service.input_hash("C1011") == h0                                              # assessment inputs are back to the original


def test_delete_note_wrong_case_rejected():
    nid = service.add_note("C1011", NOTE, "alex", "investigator")
    with pytest.raises(ValueError):
        service.delete_note("C1015", nid, "alex", "investigator")


def test_notes_on_similar_cases_reach_the_agents():
    nb = related.similar("C1011", 1)[0][1]
    h0 = service.input_hash("C1011")
    nid = service.add_note(nb, "Home health provider billed identical weekend visits for three members; SIU already reviewing.", "alex", "investigator")
    ctx = related.context("C1011")
    assert any(c["case_id"] == nb and c["notes"] for c in ctx)
    b = evidence.bundle(service.get_case("C1011"))
    assert any(c["case_id"] == nb for c in b["related_case_notes"])
    assert "SIU already reviewing" in evidence.related_text(service.get_case("C1011"))
    assert service.input_hash("C1011") != h0            # a note on a related case makes this assessment stale
    assert "SIU already reviewing" in json.dumps(tools.search_notes("identical weekend visits"))
    service.delete_note(nb, nid, "alex", "investigator")
    assert not any(c["notes"] for c in related.context("C1011") if c["case_id"] == nb)
    assert service.input_hash("C1011") == h0


def test_related_note_citation_is_grounded():
    case = {"claim_amount_usd": 1}
    raw = {"summary": "s", "score_adjustment": 0, "recommended_action": "ROUTINE_REVIEW",
           "key_indicators": [{"field": "related_note", "value": "SIU already reviewing", "why": "precedent"},
                              {"field": "related_note", "value": "invented precedent text", "why": "x"}]}
    res, *_, w = apply_guardrails(raw, case, [], 30.0, {}, [], related_text="Provider billed identical visits; SIU already reviewing.")
    assert len(res["key_indicators"]) == 1 and any("related-case note" in x for x in w)


def _closest_pair():
    v = related._vectors()
    import math
    return min(((math.dist(v[a][0], v[b][0]), a, b) for a in v for b in v if a < b))[1:]


def test_marking_fraud_flags_lookalikes_and_blocks_clearing():
    a, b = _closest_pair()
    before = db.one("SELECT score FROM cases WHERE case_id=?", (b,))["score"]
    service.set_status(a, "CLEARED", "looked clean", "alex", "investigator")
    res = service.mark_outcome(a, "FRAUD", "Provider later indicted; billed phantom visits.", "alex", "investigator")
    assert res["missed_by_system"] is True                                    # it had been cleared
    assert db.one("SELECT status FROM cases WHERE case_id=?", (a,))["status"] == "FRAUD_CONFIRMED"
    c = service.get_case(b)
    assert "R17" in c["fired_ids"] and c["score"] > before                   # the lookalike is flagged and re-scored
    ctx = [x for x in c["related_context"] if x["case_id"] == a][0]
    assert ctx["confirmed_outcome"]["outcome"] == "FRAUD" and ctx["confirmed_outcome"]["missed_by_system"]
    assert "phantom visits" in evidence.related_text(c)
    ok, blocked = service.bulk_evaluate([service.get_case(b)], allow_without_ai=True)
    assert not ok and any("R17" in r for r in blocked[0]["reasons"])         # bulk clear refuses lookalikes
    res2, *_, w = apply_guardrails({"summary": "s", "recommended_action": "CLEAR_FALSE_POSITIVE"}, c["data"], c["rules"], c["score"], {}, [])
    assert res2["recommended_action"] == "ROUTINE_REVIEW" and any("R17" in x for x in w)   # the AI cannot clear it either


def test_retract_outcome_restores_everything():
    a, b = _closest_pair()
    score0 = db.one("SELECT score FROM cases WHERE case_id=?", (b,))["score"]
    service.mark_outcome(a, "FRAUD", "Confirmed by SIU audit of unique-marker-xyz billing.", "alex", "investigator")
    fake_assessment(b, "Resembles a confirmed fraud after a SIU audit of unique-marker-xyz billing")
    service.retract_outcome(a, "alex", "investigator")
    assert db.one("SELECT score FROM cases WHERE case_id=?", (b,))["score"] == score0
    assert "R17" not in service.get_case(b)["fired_ids"]
    assert db.one("SELECT COUNT(*) n FROM assessments WHERE result LIKE '%unique-marker-xyz%'")["n"] == 0
    assert service.get_case(a)["status"] != "FRAUD_CONFIRMED"


def test_outcome_requires_reason_and_valid_value():
    with pytest.raises(ValueError):
        service.mark_outcome("C1004", "FRAUD", "  ", "alex", "investigator")
    with pytest.raises(ValueError):
        service.mark_outcome("C1004", "MAYBE", "x", "alex", "investigator")


def test_endpoints_do_not_crash_when_scheduling_regeneration(monkeypatch):
    """Regression: sync endpoints called asyncio.create_task with no running loop -> 500 after the deletion had already happened."""
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.client, "configured", lambda: True)
    monkeypatch.setattr(main.judge, "assess_all", lambda *a, **k: _noop())
    c = TestClient(main.app)
    nid = service.add_note("C1011", "temporary note for endpoint test", "alex", "investigator")
    r = c.delete(f"/api/cases/C1011/notes/{nid}", headers={"X-Actor": "alex", "X-Role": "investigator"})
    assert r.status_code == 200 and service.notes_for("C1011") == []
    r = c.post("/api/cases/C1004/outcome", json={"outcome": "FRAUD", "reason": "found later"}, headers={"X-Actor": "alex", "X-Role": "investigator"})
    assert r.status_code == 200 and r.json()["missed_by_system"] is True
    assert c.delete("/api/cases/C1004/outcome", headers={"X-Actor": "alex", "X-Role": "investigator"}).status_code == 200


async def _noop():
    return None
