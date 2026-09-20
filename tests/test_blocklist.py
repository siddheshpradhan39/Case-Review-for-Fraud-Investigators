"""Blocklist: hard rules that auto-decline. Every kind, enforcement/release, governance, override, alerts, provider IDs."""
import csv

import pytest
from fastapi.testclient import TestClient

from app import blocklist, db, main, related, rulestore, service
from app.customrules import RuleError
from app.features import DATA_CSV, load_raw

SUP = ("Sam Okafor", "supervisor")
INV = ("Alex Rivera", "investigator")


@pytest.fixture(autouse=True)
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    related.reset()
    rulestore.invalidate()
    service.ingest()
    yield
    related.reset()


def case(cid):
    return service.get_case(cid)


def entry(kind="claim_number", spec=None, name="Test entry", who=SUP, confirm_broad=False, **kw):
    return blocklist.create({"name": name, "kind": kind, "spec": spec or {"values": [case("C1004")["claim_number"]]}, "reason": "Known bad claim number", "status": "active", **kw},
                            *who, confirm_broad=confirm_broad)


def rescore():
    return service.rescore()


# ---------------------------------------------------------------- validation
def test_spec_validation():
    bad = [("claim_number", {"values": []}), ("claim_number", {"values": ["x"]}), ("segment", {}), ("condition", {"logic": {"op": "AND", "conds": []}}),
           ("fraud_history", {"metric": "nope", "threshold": 1}), ("fraud_history", {"metric": "segment_fraud_rate", "threshold": 1.5}),
           ("fraud_history", {"metric": "segment_fraud_count", "threshold": 0}), ("fraud_history", {"metric": "segment_fraud_count", "threshold": 2.5}),
           ("fraud_history", {"metric": "segment_fraud_count", "threshold": 2, "dims": ["provider"]}), ("mystery", {})]
    for kind, spec in bad:
        with pytest.raises(RuleError):
            blocklist.validate_spec(kind, spec)
    with pytest.raises(RuleError, match="reason"):
        blocklist.create({"name": "abc", "kind": "claim_number", "spec": {"values": ["LTC-1"]}, "reason": "no", "status": "active"}, *SUP)


# ---------------------------------------------------------------- enforcement
def test_claim_number_entry_declines_and_locks_the_case():
    service.set_status("C1004", "IN_REVIEW", "", *INV)
    e = entry()
    res = rescore()
    assert res["blocklist"]["declined"] == ["C1004"]
    c = case("C1004")
    assert c["status"] == "DECLINED" and c["decline"]["prev_status"] == "IN_REVIEW" and c["decline"]["enforced"] == 1
    assert e["id"] in c["decline"]["entry_ids"] and "on the blocklist" in c["decline"]["details"][0]["detail"]
    assert any(a["action"] == "BLOCKLIST_DECLINED" for a in service.audit_log(20, "C1004"))
    from app import briefing
    s = briefing.stats()
    assert s["declined_by_blocklist"] == 1 and "C1004" not in [t["case_id"] for t in s["top_cases"]] and s["open_cases"] == 49
    with pytest.raises(ValueError, match="Declined by the blocklist"):
        service.set_status("C1004", "CLEARED", "x", *INV)
    with pytest.raises(ValueError, match="Declined by the blocklist"):
        service.escalate("C1004", "x", "", *INV)
    with pytest.raises(ValueError, match="only by the blocklist"):
        service.set_status("C1005", "DECLINED", "x", *SUP)
    ok, blocked = service.bulk_evaluate([case("C1004")], allow_without_ai=True)
    assert not ok and "already DECLINED" in blocked[0]["reasons"][0]


def test_pausing_or_deleting_the_entry_releases_the_case_to_its_prior_status():
    service.set_status("C1004", "IN_REVIEW", "", *INV)
    e = entry()
    rescore()
    blocklist.update(e["id"], {"status": "paused"}, *SUP)
    res = rescore()
    assert res["blocklist"]["released"] == ["C1004"] and case("C1004")["status"] == "IN_REVIEW" and case("C1004")["decline"] is None
    blocklist.update(e["id"], {"status": "active"}, *SUP)
    rescore()
    assert case("C1004")["status"] == "DECLINED"
    blocklist.delete(e["id"], *SUP)
    rescore()
    assert case("C1004")["status"] == "IN_REVIEW"


def test_segment_entry():
    fl_hha = [r["case_id"] for r in service.list_cases({}) if r["state"] == "FL" and r["care_type"] == "Home Health Aide"]
    assert fl_hha
    entry("segment", {"state": "FL", "care_type": "Home Health Aide"}, name="FL HHA")
    rescore()
    declined = {r["case_id"] for r in service.list_cases({}) if r["status"] == "DECLINED"}
    assert declined == set(fl_hha)


def test_condition_entry_uses_the_rule_dsl():
    logic = {"op": "AND", "conds": [{"field": "duplicate_service_billed", "cmp": "==", "value": 1}, {"field": "shared_contact_with_provider", "cmp": "==", "value": 1},
                                    {"field": "service_overlap_other_provider", "cmp": "==", "value": 1}]}
    want = {r["case_id"] for r in service.list_cases({}) if all(r["data"][f] == 1 for f in ("duplicate_service_billed", "shared_contact_with_provider", "service_overlap_other_provider"))}
    assert len(want) >= 8
    entry("condition", {"logic": logic}, name="All three collusion flags")
    rescore()
    assert {r["case_id"] for r in service.list_cases({}) if r["status"] == "DECLINED"} == want


# ---------------------------------------------------------------- fraud-history rules
def test_claim_number_fraud_history():
    entry("fraud_history", {"metric": "claim_number_frauds", "threshold": 1}, name="Claim number has a confirmed fraud")
    assert rescore()["blocklist"]["declined"] == []
    service.mark_outcome("C1031", "FRAUD", "confirmed by audit", *INV)   # C1001 shares its claim number
    assert case("C1001")["status"] == "DECLINED" and "confirmed fraud" in case("C1001")["decline"]["details"][0]["detail"]
    assert case("C1031")["status"] == "FRAUD_CONFIRMED"                  # never declined because of its OWN label
    service.retract_outcome("C1031", *INV)
    assert case("C1001")["status"] == "NEW"                               # released automatically


def test_segment_fraud_rate_needs_enough_labelled_cases():
    hha = [r["case_id"] for r in service.list_cases({}) if r["care_type"] == "Home Health Aide"]
    a, b, c = hha[:3]
    for cid, o in ((a, "FRAUD"), (b, "FRAUD"), (c, "LEGITIMATE")):
        service.mark_outcome(cid, o, "labelled for the test", *INV)
    e = entry("fraud_history", {"metric": "segment_fraud_rate", "threshold": 0.5, "min_cases": 4, "dims": ["care_type"]}, name="HHA fraud rate")
    assert rescore()["blocklist"]["declined"] == []                        # only 3 labelled < min_cases 4: no hit
    blocklist.update(e["id"], {"spec": {"metric": "segment_fraud_rate", "threshold": 0.5, "min_cases": 3, "dims": ["care_type"]}}, *SUP)
    res = rescore()
    others = [x for x in hha if x not in (a, b, c)]
    assert set(res["blocklist"]["declined"]) == set(others)
    assert "67%" in case(others[0])["decline"]["details"][0]["detail"]
    # a labelled case excludes itself: the two frauds see 1/2 labelled cases (< min 3) so they are not declined
    assert case(a)["status"] == "FRAUD_CONFIRMED" and case(a)["decline"] is None


def test_closed_cases_are_alerted_not_reopened():
    service.set_status("C1004", "CLEARED", "clean", *INV)
    entry()
    res = rescore()
    assert res["blocklist"]["alerts"] == ["C1004"] and res["blocklist"]["declined"] == []
    c = case("C1004")
    assert c["status"] == "CLEARED" and c["decline"]["enforced"] == 0
    from app import briefing
    assert briefing.stats()["blocklist_alerts"] == 1


# ---------------------------------------------------------------- governance
def test_investigators_can_only_propose():
    e = blocklist.create({"name": "Proposed", "kind": "claim_number", "spec": {"values": [case("C1004")["claim_number"]]}, "reason": "looks bad", "status": "active"}, *INV)
    assert e["status"] == "paused"
    rescore()
    assert case("C1004")["status"] == "NEW"                                # a proposal changes nothing
    with pytest.raises(RuleError, match="only a supervisor"):
        blocklist.update(e["id"], {"status": "active"}, *INV)
    blocklist.update(e["id"], {"reason": "better wording here"}, *INV)      # editing a paused proposal is fine
    with pytest.raises(RuleError, match="only a supervisor"):
        blocklist.delete(e["id"], *INV)
    blocklist.update(e["id"], {"status": "active"}, *SUP)
    rescore()
    assert case("C1004")["status"] == "DECLINED"
    with pytest.raises(RuleError, match="only a supervisor"):
        blocklist.update(e["id"], {"reason": "sneaky edit of an active entry"}, *INV)


def test_broad_entries_need_confirmation():
    logic = {"op": "AND", "conds": [{"field": "binary_flag_count", "cmp": ">=", "value": 0}]}   # matches every case
    with pytest.raises(RuleError, match="BROAD"):
        entry("condition", {"logic": logic}, name="Everything")
    e = entry("condition", {"logic": logic}, name="Everything", confirm_broad=True)
    assert e["status"] == "active"


def test_preview_writes_nothing():
    p = blocklist.preview({"kind": "claim_number", "spec": {"values": [case("C1004")["claim_number"]]}, "name": "x", "reason": "y"})
    assert p["count"] == 1 and p["cases"][0]["case_id"] == "C1004" and p["cases"][0]["enforceable"]
    assert case("C1004")["status"] == "NEW" and not blocklist.list_entries()


# ---------------------------------------------------------------- override
def test_supervisor_override_and_feedback():
    e = entry()
    rescore()
    with pytest.raises(ValueError, match="Only a supervisor"):
        service.override_decline("C1004", "false positive", *INV)
    with pytest.raises(ValueError, match="written reason"):
        service.override_decline("C1004", "  ", *SUP)
    service.override_decline("C1004", "Claim number was mistyped at intake; verified against the original.", *SUP)
    c = case("C1004")
    assert c["status"] == "IN_REVIEW" and any("OVERRIDDEN" in n["text"] for n in c["notes"])   # agents/colleagues see why
    rescore()
    assert case("C1004")["status"] == "IN_REVIEW"                          # the same entry does not re-decline it
    assert blocklist.stats()[e["id"]]["overridden"] == 1                    # a false-positive signal for the entry
    e2 = entry("segment", {"state": c["state"]}, name="A different entry hitting it")
    rescore()
    assert case("C1004")["status"] == "DECLINED"                            # a NEW entry re-declines an overridden case
    with pytest.raises(ValueError, match="not currently declined"):
        service.override_decline("C1005", "x", *SUP) if case("C1005")["status"] != "DECLINED" else None


def test_stats_show_hits_and_labels():
    e = entry("segment", {"care_type": "Home Health Aide"}, name="HHA")
    rescore()
    service.mark_outcome("C1010", "LEGITIMATE", "checked", *INV)
    st = blocklist.stats()[e["id"]]
    assert st["hits_now"] == 11 and st["ever_declined"] >= 10 and st["hit_confirmed_legit"] == 1


# ---------------------------------------------------------------- provider / member IDs
def test_provider_and_member_id_entries(tmp_path, monkeypatch):
    src = tmp_path / "with_ids.csv"
    rows = list(csv.DictReader(open(DATA_CSV, encoding="utf-8-sig")))
    for r in rows:
        r["provider_id"] = "PRV-BAD" if r["case_id"] in ("C1004", "C1010", "C1044") else "PRV-OK-" + r["case_id"]
        r["member_id"] = "MEM-7" if r["case_id"] in ("C1002", "C1003") else "MEM-" + r["case_id"]
    with open(src, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setattr(service, "load_raw", lambda: load_raw(src))
    service.ingest()
    assert case("C1004")["data"]["provider_id"] == "PRV-BAD"
    entry("provider_id", {"values": ["prv-bad"]}, name="Bad provider")     # matching is case-insensitive
    entry("member_id", {"values": ["MEM-7"]}, name="Bad member")
    rescore()
    assert {r["case_id"] for r in service.list_cases({}) if r["status"] == "DECLINED"} == {"C1004", "C1010", "C1044", "C1002", "C1003"}
    # provider fraud history: one confirmed fraud on a provider blocks its other cases
    entry("fraud_history", {"metric": "provider_frauds", "threshold": 1}, name="Provider has a fraud")
    service.mark_outcome("C1050", "FRAUD", "audit", *INV)
    assert case("C1050")["status"] == "FRAUD_CONFIRMED"
    p = blocklist.preview({"kind": "provider_id", "spec": {"values": ["NOPE-1"]}, "name": "x", "reason": "y"})
    assert p["count"] == 0 and p["has_ids"]["provider_id"] == 50


def test_id_entries_are_inert_when_the_data_has_no_ids():
    entry("provider_id", {"values": ["PRV-1"]}, name="No column anywhere")
    assert rescore()["blocklist"]["declined"] == []
    assert blocklist.preview({"kind": "provider_id", "spec": {"values": ["PRV-1"]}, "name": "x", "reason": "y"})["has_ids"]["provider_id"] == 0


# ---------------------------------------------------------------- API
def test_api_roundtrip_and_roles():
    c = TestClient(main.app)
    H = lambda who: {"X-Actor": who[0], "X-Role": who[1]}
    body = {"name": "Bad claim", "kind": "claim_number", "spec": {"values": [case("C1004")["claim_number"]]}, "reason": "Known bad claim number", "status": "active"}
    r = c.post("/api/blocklist", json=body, headers=H(INV))
    assert r.status_code == 200 and r.json()["entry"]["status"] == "paused" and r.json()["rescore"]["blocklist"]["declined"] == []
    bid = r.json()["entry"]["id"]
    assert c.put(f"/api/blocklist/{bid}", json={"status": "active"}, headers=H(INV)).status_code == 400
    r = c.put(f"/api/blocklist/{bid}", json={"status": "active"}, headers=H(SUP))
    assert r.status_code == 200 and r.json()["rescore"]["blocklist"]["declined"] == ["C1004"]
    assert [d["case_id"] for d in c.get("/api/declined").json()] == ["C1004"]
    assert c.get("/api/cases/C1004").json()["decline"]["details"][0]["kind"] == "claim_number"
    lst = c.get("/api/blocklist").json()
    assert lst[0]["hits_now"] == 1 and lst[0]["summary"]
    assert c.post("/api/cases/C1004/decline/override", json={"reason": "typo at intake"}, headers=H(INV)).status_code == 400
    assert c.post("/api/cases/C1004/decline/override", json={"reason": "typo at intake"}, headers=H(SUP)).status_code == 200
    assert c.post("/api/blocklist/preview", json={"kind": "segment", "spec": {"state": "FL"}, "name": "x", "reason": "yyyyy"}).json()["count"] >= 1
    broad = {"name": "Everything", "kind": "condition", "reason": "too broad on purpose", "status": "active",
             "spec": {"logic": {"op": "AND", "conds": [{"field": "binary_flag_count", "cmp": ">=", "value": 0}]}}}
    assert c.post("/api/blocklist", json=broad, headers=H(SUP)).status_code == 409
    assert c.post("/api/blocklist", json={**broad, "confirm_broad": True}, headers=H(SUP)).status_code == 200
    assert c.post("/api/blocklist", json={**broad, "name": "Bad spec", "spec": {"logic": {"op": "AND", "conds": []}}}, headers=H(SUP)).status_code == 400
    assert c.delete(f"/api/blocklist/{bid}", headers=H(INV)).status_code == 400
    assert c.delete(f"/api/blocklist/{bid}", headers=H(SUP)).status_code == 200


def test_overridden_case_is_not_counted_as_declined():
    from app import briefing
    entry()
    rescore()
    assert briefing.stats()["declined_by_blocklist"] == 1
    service.override_decline("C1004", "mistyped at intake", *SUP)
    assert briefing.stats()["declined_by_blocklist"] == 0 and briefing.stats()["open_cases"] == 50
