"""top_by_level: the Morning briefing's 'Top cases to open first' table fills one risk level at a
time (CRITICAL, then HIGH, then MEDIUM, then LOW), only spilling into the next level once the one
above it is fully exhausted -- never a forced even mix, and it adapts to whatever today's data is."""
from app.briefing import top_by_level


def case(cid, level, score, amount=1000):
    return {"case_id": cid, "level": level, "score": score, "amount": amount, "ai": None}


def ids(cases):
    return [c["case_id"] for c in cases]


def test_more_critical_than_the_limit_shows_critical_only():
    cases = [case(f"C{i}", "CRITICAL", 100 - i) for i in range(30)]
    out = top_by_level(cases, 25)
    assert len(out) == 25
    assert all(c["level"] == "CRITICAL" for c in out)
    assert ids(out) == [f"C{i}" for i in range(25)]  # highest score first


def test_critical_exhausted_spills_into_high_only():
    critical = [case(f"C{i}", "CRITICAL", 90 - i) for i in range(8)]
    high = [case(f"H{i}", "HIGH", 70 - i) for i in range(20)]
    out = top_by_level(critical + high, 25)
    assert len(out) == 25
    levels = [c["level"] for c in out]
    assert levels.count("CRITICAL") == 8 and levels.count("HIGH") == 17
    assert "MEDIUM" not in levels and "LOW" not in levels
    assert ids(out)[:8] == [f"C{i}" for i in range(8)]  # every critical case, none dropped
    assert ids(out)[8:] == [f"H{i}" for i in range(17)]  # highest-scoring 17 of the 20 high cases


def test_critical_and_high_exhausted_spills_into_medium():
    critical = [case(f"C{i}", "CRITICAL", 90 - i) for i in range(3)]
    high = [case(f"H{i}", "HIGH", 70 - i) for i in range(4)]
    medium = [case(f"M{i}", "MEDIUM", 40 - i) for i in range(10)]
    out = top_by_level(critical + high + medium, 25)
    assert len(out) == 17  # everything: nothing left to cut, all levels short of the limit combined
    levels = [c["level"] for c in out]
    assert levels.count("CRITICAL") == 3 and levels.count("HIGH") == 4 and levels.count("MEDIUM") == 10


def test_only_low_available():
    low = [case(f"L{i}", "LOW", 5 - i * 0.1) for i in range(29)]
    out = top_by_level(low, 25)
    assert len(out) == 25
    assert all(c["level"] == "LOW" for c in out)
    assert ids(out) == [f"L{i}" for i in range(25)]


def test_exact_fit_at_a_level_boundary_does_not_spill_over():
    critical = [case(f"C{i}", "CRITICAL", 90 - i) for i in range(25)]
    high = [case("H0", "HIGH", 50)]
    out = top_by_level(critical + high, 25)
    assert len(out) == 25
    assert all(c["level"] == "CRITICAL" for c in out)  # the 25th critical case wins the last slot, not HIGH


def test_fewer_cases_than_the_limit_returns_everything():
    cases = [case("C1", "CRITICAL", 90), case("H1", "HIGH", 60), case("L1", "LOW", 2)]
    out = top_by_level(cases, 25)
    assert len(out) == 3
    assert [c["level"] for c in out] == ["CRITICAL", "HIGH", "LOW"]
