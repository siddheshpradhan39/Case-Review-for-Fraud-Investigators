"""Rule/score behaviour on the real CSV. These pin the *data-derived* conclusions in docs/RULES.md."""
import pytest
from app.calibration import calibrate, jenks3
from app.features import add_derived, load_raw
from app.rules import evaluate
from app.scoring import baseline_anomaly, level_for, score_case


@pytest.fixture(scope="module")
def scored():
    cal = calibrate()[0]
    rows = add_derived(load_raw())
    out = {}
    for r in rows:
        fired = evaluate(r, cal)
        s, rs, _ = score_case(fired, baseline_anomaly(r, rows))
        out[r["case_id"]] = {"score": s, "level": level_for(s), "ids": {x["rule_id"] for x in fired}}
    return out


def test_jenks_splits_three_groups():
    lo, mid, hi = jenks3([1, 1, 2, 2, 10, 11, 12, 50, 55])
    assert lo == [1, 1, 2, 2] and hi == [50, 55]


def test_fifty_cases(scored):
    assert len(scored) == 50


def test_all_flag_cases_are_critical(scored):
    for cid in ["C1006", "C1021", "C1024", "C1030", "C1031", "C1034", "C1036", "C1037"]:
        assert scored[cid]["level"] == "CRITICAL", cid


def test_clean_cases_score_under_10(scored):
    for cid in ["C1002", "C1004", "C1008", "C1022", "C1044"]:
        assert scored[cid]["score"] < 10 and not scored[cid]["ids"], cid


def test_silent_drift_cases_flagged(scored):
    for cid in ["C1026", "C1040", "C1042"]:
        assert "R14" in scored[cid]["ids"], cid


def test_claim_number_reuse(scored):
    assert "R15" in scored["C1001"]["ids"] and "R15" in scored["C1031"]["ids"]
    assert scored["C1001"]["level"] == "MEDIUM"


def test_ambiguous_cases_sit_between_tiers(scored):
    for cid in ["C1011", "C1015", "C1019", "C1050"]:
        assert scored[cid]["level"] in ("MEDIUM", "HIGH"), cid


def test_domain_cap_prevents_double_counting():
    same_domain = [{"domain": "Billing", "weight": 25}] * 5
    assert score_case(same_domain, 0)[1] == 25.0
    two_domains = [{"domain": "Billing", "weight": 25}, {"domain": "Geography", "weight": 25}]
    assert score_case(two_domains, 0)[1] == 43.8
