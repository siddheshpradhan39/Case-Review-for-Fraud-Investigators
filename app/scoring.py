"""Case score 0-100 from fired rules.

Signals in this data co-move (all severe cases light up everything), so plain addition would double-count.
Score = noisy-OR across DOMAINS, taking only the strongest rule within each domain, plus a small 0-10
"baseline anomaly" term (mean percentile rank of the continuous signals) so clean cases still spread
0-10 and the queue has a stable order and a meaningful "score < 10" bulk filter.
"""
from .features import CONTINUOUS

# CRITICAL cut at 80: the scored portfolio has an empty gap between 77.1 and 84.8 (docs/RULES.md)
BANDS = [(80, "CRITICAL"), (50, "HIGH"), (25, "MEDIUM"), (0, "LOW")]
BAND_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ANOM_SIGNALS = CONTINUOUS + ["implied_weekly_travel_miles"]


def level_for(score):
    for cut, name in BANDS:
        if score >= cut:
            return name


def baseline_anomaly(case, portfolio):
    """0-10: mean percentile rank of the continuous signals within the portfolio."""
    ranks = []
    for s in ANOM_SIGNALS:
        vals = [r[s] for r in portfolio]
        below = sum(1 for v in vals if v < case[s])
        ties = sum(1 for v in vals if v == case[s])
        ranks.append((below + 0.5 * ties) / len(vals))
    return round(10 * sum(ranks) / len(ranks), 1)


def score_case(fired, anomaly):
    by_domain = {}
    for r in fired:
        by_domain[r["domain"]] = max(by_domain.get(r["domain"], 0), r["weight"])
    surv = 1.0
    for w in by_domain.values():
        surv *= 1 - w / 100
    rule_score = (1 - surv) * 100
    score = min(100.0, rule_score * (1 - anomaly / 100) + anomaly)  # anomaly adds headroom-safe 0-10
    return round(score, 1), round(rule_score, 1), by_domain
