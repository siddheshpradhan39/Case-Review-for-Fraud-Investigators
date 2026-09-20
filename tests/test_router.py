"""Deterministic router on the real 50 cases (no LLM involved)."""
import collections

from app import service
from app.agents import router
from app.agents.domains import specialists_for, slice_for, slice_hash


def plans():
    service.ingest()
    return {r["case_id"]: router.plan(service.get_case(r["case_id"])) for r in service.list_cases({})}


def test_lane_counts():
    c = collections.Counter(p.lane for p in plans().values())
    assert c == {"FAST": 29, "OBVIOUS": 8, "DEEP": 13}


def test_obvious_are_the_all_flag_cases():
    p = plans()
    assert {k for k, v in p.items() if v.lane == "OBVIOUS"} == {"C1006", "C1021", "C1024", "C1030", "C1031", "C1034", "C1036", "C1037"}


def test_linked_clean_case_spawns_only_collusion():
    p = plans()
    assert p["C1001"].lane == "DEEP" and p["C1001"].specialists == ["collusion"]


def test_no_collusion_specialist_without_collusion_signal():
    p = plans()
    assert "collusion" not in p["C1011"].specialists and set(p["C1011"].specialists) == {"billing", "geo_util"}


def test_silent_drift_cases_are_deep_not_fast():
    p = plans()
    for cid in ("C1026", "C1040", "C1042"):
        assert p[cid].lane == "DEEP"


def test_slice_hash_ignores_other_domains():
    service.ingest()
    c = service.get_case("C1011")
    before = slice_hash(slice_for(c, "geo_util"))
    c["data"]["weekend_billing_ratio"] = 0.99  # billing-domain field
    c["notes"] = [{"text": "new note"}]
    assert slice_hash(slice_for(c, "geo_util")) == before
    c["data"]["weekly_visit_frequency"] = 3    # geo_util field
    assert slice_hash(slice_for(c, "geo_util")) != before
