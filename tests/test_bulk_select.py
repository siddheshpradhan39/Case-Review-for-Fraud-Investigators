"""Clearing an explicit selection (ticked rows) goes through the same guardrails as a filter-wide bulk clear."""
import pytest
from fastapi.testclient import TestClient

from app import db, main, related, rulestore, service

H = {"X-Actor": "Alex Rivera", "X-Role": "investigator"}


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    related.reset()
    rulestore.invalidate()
    service.ingest()


def test_preview_by_ids_only_evaluates_the_selection():
    c = TestClient(main.app)
    r = c.post("/api/bulk/preview", json={"case_ids": ["C1004", "C1008"], "allow_without_ai": True}).json()
    assert r["matched"] == 2 and {x["case_id"] for x in r["clearable"]} == {"C1004", "C1008"}


def test_selection_is_still_guardrailed_and_partial_selections_work():
    c = TestClient(main.app)
    r = c.post("/api/bulk/preview", json={"case_ids": ["C1004", "C1024"], "allow_without_ai": True}).json()   # C1024 is CRITICAL
    assert [x["case_id"] for x in r["clearable"]] == ["C1004"]
    assert r["blocked"][0]["case_id"] == "C1024" and any("HIGH/CRITICAL" in x for x in r["blocked"][0]["reasons"])
    res = c.post("/api/bulk/clear", json={"case_ids": ["C1004", "C1024"], "reason": "reviewed both; only the clean one is clearable", "allow_without_ai": True}, headers=H).json()
    assert res["cleared"] == ["C1004"] and res["blocked"][0]["case_id"] == "C1024"
    assert service.get_case("C1004")["status"] == "CLEARED" and service.get_case("C1024")["status"] == "NEW"


def test_selection_requires_a_reason_and_filter_mode_still_works():
    c = TestClient(main.app)
    assert c.post("/api/bulk/clear", json={"case_ids": ["C1004"], "reason": " "}, headers=H).status_code == 400
    r = c.post("/api/bulk/preview", json={"filter": {"max_score": 10}, "allow_without_ai": True}).json()
    assert r["matched"] == 29
