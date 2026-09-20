"""Data-driven threshold calibration.

For each continuous signal we split the sorted values into 3 natural groups
(baseline / elevated / extreme) by minimising within-group variance (Jenks natural breaks, k=3,
exact O(n^2) DP over 50 points). Thresholds are the midpoints between adjacent groups, so they
sit in the empty gaps of the real distribution instead of being guessed.

Run:  python -m app.calibration   -> writes data/calibration.json and docs/RULES.md (evidence tables)
"""
import json
import statistics as st
from pathlib import Path

from .features import CONTINUOUS, add_derived, load_raw

ROOT = Path(__file__).resolve().parent.parent
CAL_JSON = ROOT / "data" / "calibration.json"
DERIVED_CONT = ["implied_weekly_travel_miles", "amount_vs_care_type_median_x"]
TARGETS = CONTINUOUS + ["implied_weekly_travel_miles"]


def jenks3(values):
    """Optimal 3-class split. Returns (lo_group, mid_group, hi_group) as sorted lists."""
    v = sorted(values)
    n = len(v)

    def sse(i, j):  # inclusive-exclusive
        seg = v[i:j]
        m = sum(seg) / len(seg)
        return sum((x - m) ** 2 for x in seg)

    best = None
    for a in range(1, n - 1):
        for b in range(a + 1, n):
            cost = sse(0, a) + sse(a, b) + sse(b, n)
            if best is None or cost < best[0]:
                best = (cost, a, b)
    _, a, b = best
    return v[:a], v[a:b], v[b:]


def percentile(sorted_vals, p):
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def nice(x):
    """Round a midpoint to a readable threshold."""
    if abs(x) >= 100:
        return round(x, -1)
    if abs(x) >= 10:
        return round(x)
    return round(x, 2)


def calibrate():
    rows = add_derived(load_raw())
    n = len(rows)
    out = {"n_cases": n, "signals": {}, "binary": {}}
    for sig in TARGETS:
        vals = [r[sig] for r in rows]
        lo, mid, hi = jenks3(vals)
        srt = sorted(vals)
        out["signals"][sig] = {
            "elevated_threshold": nice((lo[-1] + mid[0]) / 2),
            "extreme_threshold": nice((mid[-1] + hi[0]) / 2),
            "groups": {"baseline": [lo[0], lo[-1], len(lo)], "elevated": [mid[0], mid[-1], len(mid)],
                       "extreme": [hi[0], hi[-1], len(hi)]},
            "median": st.median(vals), "p75": percentile(srt, .75), "p90": percentile(srt, .9),
            "max": srt[-1],
        }
    for b in ("duplicate_service_billed", "shared_contact_with_provider",
              "recent_policy_change_flag", "service_overlap_other_provider"):
        k = sum(int(r[b]) for r in rows)
        out["binary"][b] = {"count": k, "rate": round(k / n, 3)}
    amts = sorted(r["claim_amount_usd"] for r in rows)
    out["amount"] = {"p75": percentile(amts, .75), "p85": percentile(amts, .85), "p90": percentile(amts, .9), "median": st.median(amts)}
    zs = sorted(r["log_amount_robust_z"] for r in rows)
    lo, mid, hi = jenks3(zs)
    out["log_amount_robust_z"] = {"outlier_threshold": nice((mid[-1] + hi[0]) / 2),
                                  "groups": {"baseline": [lo[0], lo[-1], len(lo)],
                                             "elevated": [mid[0], mid[-1], len(mid)],
                                             "extreme": [hi[0], hi[-1], len(hi)]}}
    CAL_JSON.write_text(json.dumps(out, indent=2))
    return out, rows


def load_calibration():
    if not CAL_JSON.exists():
        calibrate()
    return json.loads(CAL_JSON.read_text())


if __name__ == "__main__":
    cal, _ = calibrate()
    print(json.dumps(cal, indent=2))
