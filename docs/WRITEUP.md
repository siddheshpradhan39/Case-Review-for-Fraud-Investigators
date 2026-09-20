# Write-up — Junior AI Investigator

## 1. Product

**User:** a fraud investigator (and their supervisor) who starts the day with a queue of engine-generated referrals, most of which
turn out benign.
**Job to be done:** *decide quickly and defensibly which referrals deserve human time* — and on the ones that do, get to the evidence,
the reasoning and the next action without hunting through dozens of signals.

**What I built (a narrow slice, done end-to-end):**
1. **Morning briefing** — the queue as a triage, not a table: risk counts and dollar exposure, top cases, *rule clusters* (cases sharing a
   pattern), *abnormal firing* (a rule firing far more often in a care type / state / month than elsewhere) and reused claim numbers.
2. **Rules → score → LLM judge** per case: numbered rules with risk levels, a 0–100 score, then an agentic reviewer that confirms or adjusts
   with cited evidence and recommends a next action.
3. **Drill-in with human control:** accept/reject each AI finding, add notes (which the AI and the chatbot read), ask questions, disposition
   the case, escalate to a supervisor with an AI-drafted handoff.
4. **Bulk disposition by filter** (e.g. `score < 10`) — because the burn is on benign cases — with guardrails that refuse to bulk-clear anything risky.

**Deliberately left out:** auth/multi-tenancy, external data enrichment, a real supervisor workflow engine, training an ML model (no labels), and
UI polish beyond a scannable layout.

## 2. Architecture — an agentic Investigation Crew, not a single prompt

```
CSV -> features -> natural-break calibration -> rules R01-R16 -> domain-capped score/level
                                                      |
                     Router (deterministic, NO LLM): lane + spawn plan
      FAST (29)                OBVIOUS (8)                     DEEP (13)
  1 batched cheap call     Verifier only (fast tier)   spawn ONLY the specialists whose domain fired (parallel):
  (5 cases/call, no tools)                              Billing | Collusion(+linkage) | Geography-Utilization
                                                          -> Challenger (argues the benign case)
                                                          -> Verifier (strong tier: draft + self-confidence + contested?)
                                                          -> if CONTESTED: 3-persona panel -> deterministic meta-judge
                     \____________________ grounding gate (guardrails.py) ____________________/
                                    -> assessment + spans (trace) + cost accounting -> DB / UI
```
- **Depth scales with difficulty.** 29 cases get one shared cheap call, 8 get a single verifier, 13 get the full crew; 5 of those were contested and convened the panel.
- **Harness (`app/agents/runtime.py`), guarantees independent of prompt quality:** budgets (steps/tool calls/tokens/time) that force a final answer; per-agent tool
  allowlists; no-progress detection; pydantic-typed outputs with a bounded repair loop; an escalation ladder (fast tier fails -> one retry on the strong tier);
  fail-closed (`LLM_UNAVAILABLE`, never invented text); token/latency accounting and reference pricing; every LLM/tool call is a persisted span shown in the UI.
- **Client (`app/llm/client.py`):** model tiers, rate limiter, per-model circuit breaker, record/replay cassette. Measured: free reasoning models burn the token budget and
  return empty content (65-130 s) - reasoning off gives ~20 s.
- **Grounding (deterministic, unit-tested):** specialists/challenger/verifier can cite only real field=value pairs (re-verified by code; 286 stored indicators re-check clean);
  figures are checked against the evidence; CLEAR is refused when a HIGH/CRITICAL rule fired; score moves <= +-20 and the level is *derived*; **new linked-case floor**
  (found by our own test: the single judge downgraded C1001 although it shares a claim number with CRITICAL C1031 - the floor now blocks that, and a panel that voted -10 was overruled by it).
- **Incremental re-assessment.** Specialists see only their evidence slice (hashed); notes and feedback go to challenger+verifier only. Adding a note re-ran 2 calls instead of 5, specialists reused.
- **Cross-case knowledge + learning from misses.** Agents see notes/dispositions/outcomes of linked and most-similar cases (`related.py`) and can `search_notes` across the queue; a note on a similar case makes an assessment stale. An investigator can later mark a cleared/low case as confirmed fraud: it is stored as a labelled outcome (flagged as a *miss* if the system had cleared or low-rated it), lookalikes fire rule **R17** and are re-scored, and neither the AI nor bulk clear may clear a lookalike. Notes and outcomes are fully deletable/retractable with a purge of every derived trace.
- **Human in the loop is unchanged**: accept/reject findings, notes, bulk clear with guardrails, supervisor escalation, audit log.

### Measured results (`docs/EVAL.md`, regenerate with `python -m app.evals.run --perturb --shadow`)
| | |
|---|---|
| Invariants (never clear the 8 all-signal cases, no clean case escalated, silent-drift not cleared, linked-case floor, +-20 clamp, all indicators re-verify) | **10 / 10** |
| Counterfactual + prompt-injection tests (relocation note lowers score; injection notes not obeyed; domain removal stops specialist spawn; incremental re-run) | **5 / 5** |
| Crew vs single judge, same 50 cases | band agreement **100%**, action agreement **84%** (8 differences, all listed in EVAL.md: 5 clean cases the crew recommends clearing where the single judge said routine review; C1023/C1033 where the single judge said ESCALATE_SIU and the crew REQUEST_RECORDS; C1001 - a human should look at these) |
| Tokens | crew 238k vs single 328k (**-27%**); calls 89 vs 92 |
| Reference $ (paid-price table; free tier is $0) | crew $0.44 vs single $0.12 - **the crew is not cheaper in dollars**: the strong-tier verifier on ambiguous cases is the price of specialist grounding, a challenger and a panel |
| Incremental note re-run | 5 calls -> 2 |

**Honest weaknesses found by the evals:** (1) the challenger rated all 13 deep cases "medium" plausibility - it does not yet discriminate; (2) calibrated confidence is only weakly
different for contested vs uncontested cases (0.77 vs 0.74) and cannot be validated without labels; (3) the panel almost always agrees (1 split in 5), so its value at n=5 is unproven;
(4) with 50 near-separable synthetic cases none of this can prove real-world uplift. The eval also caught a real bug (a FAST-lane JSON failure), fixed by the escalation ladder.

### Is it scalable?
The *architecture* scales; this *implementation* is prototype-scale. What scales: the router is O(1) per case with no LLM, so cost is linear in queue size and concentrated on the hard tail
(real portfolios have a far larger FAST share than 58%); FAST batches amortise per-call overhead; specialists/panel run in parallel so latency is the slowest hop, not the sum;
incremental re-runs make note-driven re-assessment cheap; budgets and the breaker bound cost under provider failure.
What does not yet: SQLite (one writer) and in-process asyncio workers -> Postgres + a queue (SQS/Redis + workers) with per-tenant rate limits; `find_similar_cases` and clustering are O(n)/O(n^2)
-> ANN index (pgvector) and incremental clustering; `list_cases` does per-row DB lookups (N+1) -> denormalise; the hop chain (specialists -> challenger -> verifier -> panel) is ~4 sequential
LLM latencies (~65 s on free models, ~10-15 s on paid) - fine for an overnight/morning triage batch, not for interactive per-click use; abnormal-firing needs multiple-testing control at scale;
lane thresholds and contested triggers were tuned on 50 rows and must be re-fit on labelled outcomes. Throughput is ultimately bounded by provider RPM, so tiers and batching matter more than cores.

## 3. Trade-offs, risks, what I'd do with more time

| Area | Choice / risk | Next step |
|---|---|---|
| Accuracy | No labels → thresholds fit to structure, **no precision/recall claim**. The clean 3-tier structure is a property of synthetic data. | Backtest on confirmed outcomes; learn weights (logistic/GBM) and use the LLM only as a reviewer of the ML+rules result; calibrate confidence. |
| Cost / latency | Free models: ~20 s/case, rate-limited, non-deterministic. Zero-rule cases skip tools. | Batch API, smaller model for clean cases, larger model for ambiguous; streaming UI. |
| Trust | LLM can still be subtly wrong in prose even when cited fields are correct. | Sentence-level claim verification; agreement tracking between reviewers and AI (feedback is already captured). |
| Abuse / safety | Notes and chat are prompt-injection vectors; bulk clear is the sharp edge. | Notes are treated as data; bulk clear is blocked for HIGH/CRITICAL rules, AI ≥ MEDIUM, stale review, or no AI opinion, and requires a reason; everything audited. Add role-based limits and 4-eyes on bulk clears above N. |
| Fairness | Distance/round-dollar/weekend signals can proxy for rural or shift-work providers. | Segment-level false-positive review; never auto-deny — humans decide. |
| Scope | No enrichment (provider registries), no auth, single process. | Provider-level entity graph (shared contact is a graph edge, not a flag). |

## 4. How a human stays in the loop

- The AI **advises**; every status change is a human action with a mandatory reason, written to an audit trail (who, what, when, AI vs human).
- **Trust:** rule score and AI score are shown side by side; each indicator links to the exact field/value; guardrail removals and the agent's tool trace are visible.
- **Validate / override:** accept ✓ or reject ✗ each AI finding (rejected findings aren't re-asserted); add notes that change the next assessment; re-run on demand.
- **Bulk power with brakes:** bulk clear shows exactly what will be cleared and what is blocked and why.
- **Escalation:** investigator → supervisor with a drafted, editable handoff; supervisor approves (SIU) or returns with a note that flows back into the AI's context.

## 5. Assumptions

Synthetic, unlabelled data; each row is one referral; `amount_vs_peer_avg_pct` is vs same-care-type peers; a 0/1 `recent_policy_change_flag`
carries no timing; the queue belongs to a single team; supervisors are reachable through the same app.


## 6. Since the first draft (kept in sync with `docs/Writeup.pptx`)
- **Rules tab:** create, tune, shadow-test and delete rules in the dashboard (validated declarative DSL, no code execution), applied to every case immediately with an impact preview, versioned and audited; precision/recall backtests use **synthetic labels** (leave-one-out so a rule never grades itself), AI-judged levels, or real confirmed outcomes.
- **Blocklist (hard rules):** claim numbers, provider/member IDs (when the data has them), segments, hard conditions and fraud-history rules auto-decline a case; investigators propose, supervisors activate and can override a decline with a written reason; entries that would decline more than 25% of the queue need explicit confirmation.
- **Cross-case knowledge and feedback:** agents read notes, dispositions and confirmed outcomes from related cases; deleting a note purges it from assessments, chat and audit text; "Mark as FRAUD" records a labelled outcome and cases resembling it fire R17 and cannot be cleared quietly.
- **Selection-based clear** alongside filter-based bulk clear, behind the same guardrails.
- **Biggest risk: false negatives.** Only *discovered* misses can be measured, so a miss-rate figure is a lower bound. Planned safety net, in priority order: miss-rate tracking from confirmed outcomes, a retroactive look-back that reopens cleared lookalikes, an independent adversarial reviewer, and audited sampling with a statistical bound. A robust-Mahalanobis / isolation-forest anomaly detector was prototyped and **deliberately deferred**: on 50 cases the set of clean cases it flags changes with the random seed (0-3 of 29), so no individual flag is trustworthy yet.
- **Verification:** 88 automated tests; 10/10 behavioural invariants; 5/5 counterfactual and prompt-injection tests; shadow comparison with the single-judge baseline (100% band agreement, 27% fewer tokens).
