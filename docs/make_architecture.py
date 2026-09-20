"""Generates docs/architecture.svg (+ architecture.html): the detailed system architecture diagram.
Run:  python docs/make_architecture.py     Text is wrapped with Arial metrics and any box whose text overflows is reported."""
import html
from pathlib import Path

from PIL import ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1700, 1450
FONTS = {False: "/System/Library/Fonts/Supplemental/Arial.ttf", True: "/System/Library/Fonts/Supplemental/Arial Bold.ttf"}
_cache = {}
INK, MUT, BRAND, WHITE = "#1a1f2b", "#5b6478", "#3b4fd8", "#ffffff"
PAL = {  # (band fill, band stroke, box fill, box stroke, accent)
    "ui": ("#eaf6ef", "#b9e0c9", "#ffffff", "#8fcaa8", "#0e7a4b"),
    "svc": ("#eef1fa", "#c9d2ee", "#ffffff", "#a9b7e6", "#3b4fd8"),
    "core": ("#e6e9fd", "#b5bdf5", "#ffffff", "#8a97ee", "#3b4fd8"),
    "llm": ("#fef3d6", "#f0d894", "#ffffff", "#e3bf62", "#a16207"),
    "gw": ("#ffe9d6", "#f3c39a", "#ffffff", "#e9a86c", "#c4520a"),
    "db": ("#f0f1f4", "#d3d6de", "#ffffff", "#b5bac7", "#5b6478"),
}
out, problems = [], []


def font(size, bold):
    k = (round(size * 10), bold)
    if k not in _cache:
        _cache[k] = ImageFont.truetype(FONTS[bold], k[0])
    return _cache[k]


def width(t, size, bold=False):
    return font(size, bold).getlength(t) / 10


def wrap(t, w, size, bold=False):
    lines, cur = [], ""
    for word in t.split(" "):
        trial = (cur + " " + word).strip()
        if width(trial, size, bold) <= w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def add(s):
    out.append(s)


def rect(x, y, w, h, fill, stroke, rx=10, sw=1.2, dash=None):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"' + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")


def label(x, y, t, size=12, bold=False, color=INK, anchor="start"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}" text-anchor="{anchor}">{html.escape(t)}</text>')


def box(x, y, w, h, title, body=(), kind="svc", size=11.5, tsize=12.5, bullets=True, fill=None, tcolor=None, center=False):
    _, _, bf, bs, ac = PAL[kind]
    rect(x, y, w, h, fill or bf, bs)
    pad, cy = 10, y + 8 + tsize
    tl = wrap(title, w - 2 * pad, tsize, True)
    for ln in tl:
        label(x + (w / 2 if center else pad), cy, ln, tsize, True, tcolor or ac, "middle" if center else "start")
        cy += tsize + 3
    for item in body:
        ls = wrap(item, w - 2 * pad - (10 if bullets else 0), size)
        for j, ln in enumerate(ls):
            if bullets and j == 0:
                label(x + pad, cy + 3, "•", size, False, MUT)
            label(x + pad + (10 if bullets else 0) if not center else x + w / 2, cy + 3, ln, size, False, INK, "middle" if center else "start")
            cy += size + 2.5
        cy += 2
    if cy - 4 > y + h:
        problems.append(f"overflow: '{title}' needs {cy - y + 2:.0f}px but box is {h}px")


def band(y, h, title, kind, note=""):
    bf, bs, _, _, ac = PAL[kind]
    rect(30, y, W - 60, h, bf, bs, rx=14, sw=1.4)
    label(48, y + 22, title, 13.5, True, ac)
    if note:
        label(48 + width(title, 13.5, True) + 10, y + 22, note, 11.5, False, MUT)


def arrow(x1, y1, x2, y2, color=BRAND, dash=None, sw=2, head=True):
    m = "url(#ah)" if color == BRAND else "url(#ahg)"
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"' + (f' stroke-dasharray="{dash}"' if dash else "") + (f' marker-end="{m}"' if head else "") + "/>")


def num(x, y, n, color=BRAND):
    add(f'<circle cx="{x}" cy="{y}" r="11" fill="{color}"/><text x="{x}" y="{y + 4.5}" font-size="12" font-weight="700" fill="#fff" text-anchor="middle">{n}</text>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Arial, Helvetica, sans-serif">')
add('<defs><marker id="ah" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#3b4fd8"/></marker>'
    '<marker id="ahg" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#5b6478"/></marker></defs>')
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
label(30, 40, "Junior AI Investigator — System Architecture", 26, True, INK)
label(30, 64, "Rules score every case first; a deterministic router decides how much AI reasoning each case gets; guardrails and humans have the last word.", 13.5, False, MUT)
lx = 1180
for i, (k, t) in enumerate((("ui", "UI / API"), ("svc", "Services"), ("core", "Deterministic core"), ("llm", "LLM agents"), ("gw", "LLM gateway"), ("db", "Storage"))):
    add(f'<rect x="{lx + (i % 3) * 165}" y="{22 + (i // 3) * 24}" width="14" height="14" rx="3" fill="{PAL[k][0]}" stroke="{PAL[k][3]}"/>')
    label(lx + (i % 3) * 165 + 20, 34 + (i // 3) * 24, t, 12, False, INK)
num(1183, 82, "n")
label(1201, 86, "= one case's path through the system", 12, False, INK)

# ---------------------------------------------------------------- 1 PRESENTATION
band(95, 128, "PRESENTATION", "ui", "vanilla JS single-page app (web/) · no build step · hash routing")
box(50, 128, 130, 84, "Investigator", ["works the queue,", "proposes rules"], "ui", size=10.5, bullets=False)
box(188, 128, 130, 84, "Supervisor", ["approves, overrides,", "activates hard rules"], "ui", size=10.5, bullets=False)
tabs = [("Morning briefing", "risk counts, top cases, clusters, abnormal firing, crew cost"), ("Queue", "filters, presets, clear/escalate selected, bulk clear"),
        ("Case page", "AI assessment, investigation trace, notes, chat, outcome"), ("Rules", "leaderboard, builder, backtest, AI draft"),
        ("Blocklist", "hard rules, declined cases, override"), ("Supervisor", "escalation decisions"), ("Audit log", "every AI + human action, CSV export")]
for i, (t, d) in enumerate(tabs):
    box(335 + i * 190, 128, 182, 84, t, [d], "ui", size=10.5, bullets=False)
for x in (700, 1300):
    arrow(x, 223, x, 243)

# ---------------------------------------------------------------- 2 API
band(243, 106, "API — FastAPI (app/main.py)", "ui", "identity via X-Actor / X-Role headers · UI assets served no-cache with versioned URLs")
routes = [("/cases", "search · detail · status · notes (+delete) · feedback · outcome"), ("/assess · /chat", "per-case + queue AI; batch triage in background"),
          ("/bulk", "preview + clear (filter or selection) · escalate"), ("/escalations", "list · decide (supervisor)"), ("/rules", "CRUD · backtest · leaderboard · AI draft"),
          ("/blocklist · /declined", "CRUD · preview · override decline"), ("/briefing · /clusters · /runs", "queue analytics, narrative, trace + cost"), ("/audit", "log + CSV")]
for i, (t, d) in enumerate(routes):
    box(50 + i * 200, 280, 192, 60, t, [d], "ui", size=10, tsize=11.5, bullets=False)
for x in (700, 1300):
    arrow(x, 349, x, 369)

# ---------------------------------------------------------------- 3 SERVICES
band(369, 196, "DOMAIN SERVICES", "svc", "Python, in-process")
box(50, 402, 525, 152, "service.py — workflow & governance", [
    "ingest + re-score every case; blocklist enforcement (DECLINED, auto-release)", "notes (hard delete + purge of assessments/chat/audit text), feedback, dispositions",
    "bulk-clear guardrails; escalation → supervisor; outcomes (fraud/legit) + R17 re-score", "input_hash = rules + score + notes + feedback + related-case notes → staleness"], "svc")
box(587, 402, 525, 152, "Rule management (Rules tab + Blocklist)", [
    "rulestore: custom rules (validated DSL, no code exec), built-in overrides, versions, audit", "backtest: precision/recall/F1 + Wilson CI; labels = leave-one-out | AI | outcomes | blend",
    "blocklist: claim/provider/member lists, segments, hard conditions, fraud-history rules", "governance: investigators propose, supervisors activate; >25% of queue needs confirmation"], "svc")
box(1124, 402, 516, 152, "Knowledge & analytics", [
    "related.py: linked cases + nearest-by-signal + confirmed outcomes → context for agents", "clusters: co-firing rule groups; abnormal fire-rates by care type/state/month (exact binomial)",
    "briefing: headlines (no LLM), stats, optional grounded narrative", "evals: invariants, counterfactual/injection tests, shadow A/B vs single judge"], "svc")
for x in (700, 1300):
    arrow(x, 565, x, 585)

# ---------------------------------------------------------------- 4 CORE
band(585, 190, "DETERMINISTIC DECISION CORE", "core", "no LLM · every case is scored here first · re-runs on every ingest / rule / outcome change")
core = [("CSV + features", ["data/sample_cases.csv", "derived: travel miles, log-z, amount vs care-type median, links"]),
        ("Calibration", ["Jenks natural breaks → MEDIUM / HIGH thresholds per signal", "data/calibration.json"]),
        ("Rules R01–R17", ["10 signal · 2 derived · convergence · silent drift · claim reuse · policy×amount · lookalike of confirmed fraud", "+ custom X01… and tuned thresholds"]),
        ("Score 0–100", ["strongest rule per domain, noisy-OR (signals co-move)", "+ 0–10 baseline anomaly → bands LOW/MED/HIGH/CRIT"]),
        ("Blocklist", ["hard rules → status DECLINED", "locked, scoring + AI bypassed, supervisor override"]),
        ("Router", ["FAST: no rule, no link", "OBVIOUS: CRITICAL", "DEEP: everything else"])]
cw, gap = 258, 18
for i, (t, b) in enumerate(core):
    x = 50 + i * (cw + gap)
    box(x, 620, cw, 118, t, b, "core", size=10.8)
    if i < len(core) - 1:
        arrow(x + cw + 1, 684, x + cw + gap - 1, 684)
for i, n in enumerate((1, 2, 2, 2, 3, 4)):
    num(50 + i * (cw + gap) + 14, 620, n)
label(600, 756, "Router sends each case to exactly one lane", 11.5, True, BRAND, "middle")
add(f'<path d="M{50 + 5 * (cw + gap) + cw / 2},738 V762 H150" fill="none" stroke="{BRAND}" stroke-width="2"/>')
for x in (150, 360, 800):
    arrow(x, 762, x, 800)

# ---------------------------------------------------------------- 5 AGENTS
band(775, 355, "AGENT LAYER — LLM reasoning under a bounded, traced harness", "llm", "app/agents + app/llm · pure Python, no agent framework")
box(50, 805, 200, 190, "FAST lane · 29 cases", ["no rule fired, no linked case", "one batched cheap call reviews 5 cases", "no tools; note raising concern → routine review"], "llm", size=10.8)
box(262, 805, 200, 190, "OBVIOUS lane · 8 cases", ["rule level CRITICAL", "verifier only (cheap tier): facts are unambiguous", "escalates to panel only if confidence is low"], "llm", size=10.8)
rect(474, 805, 656, 190, "#fffaf0", PAL["llm"][3])
label(486, 826, "DEEP lane · 13 cases (ambiguous or linked)", 12.5, True, PAL["llm"][4])
box(486, 838, 230, 74, "Specialists (parallel)", ["only where a domain fired: billing · collusion+linkage · geo/util; ≤1 tool each"], "llm", size=10.3, tsize=11.5)
box(742, 838, 150, 74, "Challenger", ["argues the benign case"], "llm", size=10.3, tsize=11.5)
box(918, 838, 200, 74, "Verifier", ["synthesis + self-confidence; strong tier"], "llm", size=10.3, tsize=11.5)
arrow(717, 875, 741, 875, PAL["llm"][4])
arrow(893, 875, 917, 875, PAL["llm"][4])
box(486, 924, 330, 62, "Judge panel ×3 (only if contested)", ["conservative · neutral · aggressive"], "llm", size=10.3, tsize=11.5)
box(838, 924, 280, 62, "Meta-judge (deterministic)", ["median adjustment, majority action, calibrated confidence"], "llm", size=10.3, tsize=11.5)
arrow(1018, 913, 1018, 919, PAL["llm"][4], head=False)
add(f'<path d="M1018,913 V918 H650 V922" fill="none" stroke="{PAL["llm"][4]}" stroke-width="2" marker-end="url(#ah)"/>')
arrow(817, 955, 837, 955, PAL["llm"][4])
label(500, 1003, "contested = low confidence · challenger 'high' · specialists disagree · AI band ≠ rule band · score within 5 of a band cut", 10, False, MUT)
box(50, 1006, 1080, 52, "GUARDRAILS (deterministic, on every output)", ["±20 clamp, level derived · citations re-verified vs record · no CLEAR on HIGH/CRITICAL or R17 · linked-CRITICAL floor · figure check"], "llm", size=10.5, tsize=11.5, bullets=False, fill="#fff3c4")
box(50, 1062, 1080, 58, "HARNESS (app/agents/runtime.py)", ["budgets (steps · tools · tokens · time) · tool allowlists · typed output + repair (2×) · fast→strong escalation · no-progress stop · spans, tokens, reference cost per call · fail-closed (LLM unavailable, never invented)"], "llm", size=10.3, tsize=11.5, bullets=False)
num(50, 805, 5)
num(50, 1006, 6)
label(1150, 800, "Interactive & assistive agents", 12.5, True, PAL["llm"][4])
box(1150, 810, 490, 60, "Chatbot — case or queue scope", ["ReAct loop over the tools below; sees case packet, AI assessment, all notes, audit tail"], "llm", size=10.3, tsize=11.5, bullets=False)
box(1150, 878, 490, 52, "Rule drafting", ["English → validated DSL rule; never auto-saved"], "llm", size=10.3, tsize=11.5, bullets=False)
box(1150, 936, 490, 52, "Briefing narrative · Escalation handoff", ["restricted to computed stats / case file; figures verified"], "llm", size=10.3, tsize=11.5, bullets=False)
box(1150, 996, 490, 124, "9 read-only tools (allowlisted per agent)", [
    "get_case · get_peer_stats · find_similar_cases", "get_linked_cases · get_case_notes · search_notes", "get_rule_catalog · get_cluster_context · query_cases",
    "specialists get ≤1 tool; verifier, challenger, panel none"], "llm", size=10.3, tsize=11.5)
for x in (700, 1300):
    arrow(x, 1130, x, 1148)

# ---------------------------------------------------------------- 6 GATEWAY
band(1148, 92, "LLM GATEWAY — app/llm/client.py", "gw", "OpenRouter chat-completions · reasoning off (measured 3× faster, avoids empty replies)")
box(50, 1178, 1030, 52, "Reliability", ["model tiers (fast / strong) · rate limiter · per-model circuit breaker · multi-model fallback chain · token + latency accounting · record/replay cassette (offline, deterministic evals)"], "gw", size=10.5, tsize=11.5, bullets=False)
arrow(1082, 1204, 1108, 1204, PAL["gw"][4])
box(1110, 1178, 530, 52, "OpenRouter → free-tier models, tried in order", ["deepseek-v4-flash → qwen3.8-27b → gemma-4-31b → nemotron-3-super"], "gw", size=10.5, tsize=11.5, bullets=False)
arrow(700, 1240, 700, 1258)

# ---------------------------------------------------------------- 7 STORAGE
band(1258, 112, "STORAGE — SQLite (data/cases.db) + data/*.json", "db", "single process, WAL")
chips = [("cases", "features, fired rules, score, status"), ("assessments", "versioned, input_hash, trace"), ("notes · feedback", "human input the AI reads"), ("audit", "every action, redacted on purge"),
         ("outcomes", "confirmed fraud/legit + snapshot"), ("runs · spans", "calls, tokens, cost, agent trace"), ("rules", "custom_rules · overrides · versions"), ("blocklist", "entries · declines · events")]
for i, (t, d) in enumerate(chips):
    box(50 + i * 200, 1290, 192, 66, t, [d], "db", size=10, tsize=11.5, bullets=False)
label(50, 1366, "also: chat · escalations · kv (briefing cache) · calibration.json · cassette.json", 10.5, False, MUT)
num(250, 1290, 7)

# ---------------------------------------------------------------- feedback loops
rect(30, 1384, W - 60, 56, "#ffffff", "#c9d2ee", rx=10, dash="5 4")
label(48, 1404, "Feedback loops that close the circle (dashed)", 12, True, BRAND)
loops = ["note / feedback / outcome / rule change → input_hash changes → assessment stale → crew re-assesses only what changed (specialists reused by evidence-slice hash)",
         "Mark as FRAUD → outcome row → lookalikes fire R17 and re-score → clearing them is blocked; supervisor override of a decline → false-positive signal on that blocklist entry"]
for i, t in enumerate(loops):
    label(48, 1422 + i * 14, t, 10.5, False, INK)
# loop arrows on the right edge: storage -> core/agents
add(f'<path d="M1668,1300 H1682 V690 H1668" fill="none" stroke="#5b6478" stroke-width="1.6" stroke-dasharray="5 4" marker-end="url(#ahg)"/>')
add(f'<path d="M1682,690 V930 H1668" fill="none" stroke="#5b6478" stroke-width="1.6" stroke-dasharray="5 4" marker-end="url(#ahg)"/>')
add("</svg>")
svg = "\n".join(out)
(OUT / "architecture.svg").write_text(svg)
(OUT / "architecture.html").write_text(f"<!doctype html><meta charset='utf-8'><title>System architecture</title><body style='margin:0;background:#fff'>{svg}</body>")
print("wrote docs/architecture.svg and .html;", "no overflow" if not problems else "")
for p in problems:
    print("  PROBLEM", p)
