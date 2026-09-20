"""Generates docs/architecture.svg — the system architecture diagram.

The diagram is organised by TRUST rather than by module: a deterministic decision spine that is fully
reproducible, a probabilistic reasoning layer that is bounded and may only propose, a verification boundary
between them, and the human who decides.   Run:  python docs/make_architecture.py

Text is measured with real Arial metrics; any card whose content would overflow fails the build."""
import html
from pathlib import Path

from PIL import ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1860, 1372
FONTS = {False: "/System/Library/Fonts/Supplemental/Arial.ttf", True: "/System/Library/Fonts/Supplemental/Arial Bold.ttf"}
_c = {}

INK, MUT, FAINT, LINE = "#0f172a", "#64748b", "#94a3b8", "#cbd5e1"
DET, DET_BG, DET_LN = "#1d4ed8", "#eff6ff", "#bfdbfe"          # deterministic
PRB, PRB_BG, PRB_LN = "#b45309", "#fffbef", "#fcd9a0"          # probabilistic
HUM, HUM_BG, HUM_LN = "#15803d", "#f2fdf5", "#bbf7d0"          # human
RED, RED_BG, RED_LN = "#b91c1c", "#fef2f2", "#fecaca"          # refusal / decline
out, problems = [], []
MARK = {DET: "aD", PRB: "aP", HUM: "aH", MUT: "aM", RED: "aR"}


def font(size, bold):
    k = (round(size * 10), bold)
    if k not in _c:
        _c[k] = ImageFont.truetype(FONTS[bold], k[0])
    return _c[k]


def tw(t, size, bold=False):
    return font(size, bold).getlength(t) / 10


def wrap(t, w, size, bold=False):
    lines, cur = [], ""
    for word in t.split(" "):
        trial = (cur + " " + word).strip()
        if tw(trial, size, bold) <= w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def add(x):
    out.append(x)


def rect(x, y, w, h, fill="none", stroke="none", rx=6, sw=1.4, dash=None):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"'
        + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")


def txt(x, y, t, size=12, bold=False, color=INK, anchor="start"):
    add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}" '
        f'text-anchor="{anchor}">{html.escape(t)}</text>')


def para(x, y, t, w, size, color=INK, lh=1.34):
    lines = wrap(t, w, size)
    for i, ln in enumerate(lines):
        txt(x, y + i * size * lh, ln, size, False, color)
    return y + len(lines) * size * lh


def card(x, y, w, h, title, body=(), accent=DET, bg="#ffffff", ln=LINE, tsize=13, bsize=10.8, tag=None, stat=None):
    rect(x, y, w, h, bg, ln, 8)
    pad = 13
    cy = y + pad + tsize
    for line in wrap(title, w - 2 * pad - (tw(tag, 10, True) + 12 if tag else 0), tsize, True):
        txt(x + pad, cy, line, tsize, True, accent)
        cy += tsize * 1.25
    if tag:
        txt(x + w - pad, y + pad + tsize - 1, tag, 10, True, FAINT, "end")
    cy += 2
    for item in ([body] if isinstance(body, str) else list(body)):
        for line in wrap(item, w - 2 * pad, bsize):
            txt(x + pad, cy, line, bsize)
            cy += bsize * 1.36
        cy += 3
    limit = y + h - (34 if stat else 0)
    if cy - bsize > limit + 1:
        problems.append(f"'{title}' needs {cy - y:.0f}px, card allows {limit - y:.0f}px")
    if stat:
        sy_ = y + h - 30
        add(f'<path d="M{x + 13},{sy_} L{x + w - 13},{sy_}" stroke="{ln}" stroke-width="1"/>')
        txt(x + 13, sy_ + 19, stat, 9.8, True, accent)


def zone(y, h, n, title, sub, accent, bg, ln):
    rect(46, y, W - 92, h, bg, ln, 12, 1.6)
    add(f'<circle cx="74" cy="{y + 26}" r="12.5" fill="{accent}"/>')
    txt(74, y + 30.5, str(n), 13, True, "#ffffff", "middle")
    txt(95, y + 31, title, 14.5, True, accent)
    txt(95 + tw(title, 14.5, True) + 14, y + 31, sub, 11.5, False, MUT)


def arrow(pts, color=DET, sw=2, dash=None):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"'
        + (f' stroke-dasharray="{dash}"' if dash else "") + f' marker-end="url(#{MARK[color]})"/>')


add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
    f'font-family="Arial, Helvetica, sans-serif">')
add("<defs>" + "".join(
    f'<marker id="{i}" markerWidth="10" markerHeight="10" refX="8.5" refY="5" orient="auto">'
    f'<path d="M0,0.8 L9,5 L0,9.2 z" fill="{c}"/></marker>' for c, i in MARK.items()) + "</defs>")
rect(0, 0, W, H, "#ffffff")

# ── header ────────────────────────────────────────────────────────────────────
txt(46, 54, "Junior AI Investigator — system architecture", 30, True, INK)
para(46, 84, "Organised by trust, not by module: a reproducible decision spine, a bounded reasoning layer that may only "
     "propose, a verification boundary between them, and a human who decides.", 1130, 13.5, MUT)
lx = 1270
txt(lx, 36, "NOTATION", 10, True, FAINT)
for i, (c, lab, dash) in enumerate(((DET, "deterministic control flow", None), (PRB, "LLM call — bounded, traced", None),
                                    (HUM, "human action", None), (MUT, "feedback — output becomes input", "6 4"))):
    y = 57 + i * 21
    add(f'<path d="M{lx},{y} L{lx + 34},{y}" stroke="{c}" stroke-width="2.2"'
        + (f' stroke-dasharray="{dash}"' if dash else "") + f' marker-end="url(#{MARK[c]})"/>')
    txt(lx + 44, y + 4, lab, 11, False, INK)

# ── zone 1 · deterministic spine ──────────────────────────────────────────────
Z1, Z1H = 150, 272
zone(Z1, Z1H, 1, "DETERMINISTIC DECISION SPINE",
     "no LLM · reproducible · re-runs on every ingest, rule or outcome change · pinned by 88 tests", DET, DET_BG, DET_LN)
sy, sh, bw, gap = Z1 + 50, 114, 306, 30
col = [66 + i * (bw + gap) for i in range(5)]
card(col[0], sy, bw, sh, "Ingest + features",
     ["50 claims, 9 signals each", "derived: implied travel miles/wk, log-z amount, care-type ratio, claim links"], DET)
card(col[1], sy, bw, sh, "Calibration",
     ["Jenks natural breaks per signal", "thresholds land in the empty gaps of the real distribution, not on round numbers"], DET)
card(col[2], sy, bw, sh, "Rules R01–R17 + custom",
     ["signal · derived · convergence · silent drift · claim reuse · fraud lookalike", "user rules: validated DSL, never executed code"], DET)
card(col[3], sy, bw, sh, "Score → band",
     ["strongest rule per domain, combined by noisy-OR because signals co-move", "LOW · MEDIUM · HIGH · CRITICAL"], DET)
card(col[4], sy, bw, sh, "Blocklist — hard rules",
     ["claim / provider / member · segment · condition · fraud history", "proposed by investigators, enacted by supervisors"], RED, RED_BG, RED_LN)
for i in range(4):
    arrow([(col[i] + bw + 5, sy + sh / 2), (col[i + 1] - 7, sy + sh / 2)])

ry, rh = sy + sh + 22, 64
card(66, ry, 1150, rh, "ROUTER — allocates the reasoning budget by difficulty, with no LLM in the loop", [], DET, "#dbeafe", DET_LN)
txt(79, ry + 50, "a case the rules find unambiguous must not cost what an ambiguous one costs", 10.8, False, DET)
card(1250, ry, bw + 18, rh, "DECLINED", ["locked · scoring and AI bypassed · released automatically or by supervisor override"], RED, RED_BG, RED_LN, 13, 10.2)
arrow([(col[4] + bw / 2, sy + sh + 3), (col[4] + bw / 2, ry - 7)], RED)

# ── zone 2 · probabilistic reasoning ──────────────────────────────────────────
Z2, Z2H = 500, 398
zone(Z2, Z2H, 2, "PROBABILISTIC REASONING",
     "bounded · traced · may propose, never decide · one harness governs every agent", PRB, PRB_BG, PRB_LN)
ly, lh = Z2 + 78, 214
FASTX, OBVX, DEEPX, FASTW, DEEPW = 66, 402, 738, 320, 706
card(FASTX, ly, FASTW, lh, "FAST — 29 cases",
     ["no rule fired and no linked case",
      "one batched call reviews five cases at once; no tools, no specialists",
      "a note raising concern still forces human review",
      "cheap enough that every clean case keeps an AI second opinion, which the bulk-clear guardrail requires"],
     PRB, "#ffffff", PRB_LN, 13, 10.6, tag="58%", stat="0.7 calls · 1.0k tokens · $0.0008 per case")
card(OBVX, ly, FASTW, lh, "OBVIOUS — 8 cases",
     ["rule band CRITICAL: the facts are unambiguous",
      "verifier alone — specialists would only restate what the rules already establish",
      "panel convenes only if the verifier's own confidence is low",
      "effort goes into explaining the case to a human, not into re-deciding it"],
     PRB, "#ffffff", PRB_LN, 13, 10.6, tag="16%", stat="1.0 call · 3.3k tokens · $0.0016 per case")
card(DEEPX, ly, DEEPW, lh, "DEEP — 13 cases   ·   ambiguous, linked, or newly rule-flagged", [], PRB, "#ffffff", PRB_LN, 13, 10.6, tag="26%", stat="4.8 calls · 14k tokens · $0.031 per case   —   reference paid-model prices; the free tier costs $0")
px, py = DEEPX + 14, ly + 42
card(px, py, 196, 94, "Specialists (parallel)", ["billing · collusion + linkage · geo/util", "spawned only where a domain fired; ≤ 1 tool each"], PRB, "#fffbef", PRB_LN, 11.5, 9.6)
card(px + 214, py, 150, 94, "Challenger", ["argues the benign case, strictly on the evidence"], PRB, "#fffbef", PRB_LN, 11.5, 9.6)
card(px + 382, py, 150, 94, "Verifier", ["synthesis, self-confidence, contested flag"], PRB, "#fffbef", PRB_LN, 11.5, 9.6)
card(px + 550, py, 128, 94, "Panel ×3", ["conservative · neutral · aggressive", "deterministic meta-judge"], PRB, "#fffbef", PRB_LN, 11.5, 9.6)
for dx in (196, 364, 532):
    arrow([(px + dx + 4, py + 47), (px + dx + 14, py + 47)], PRB, 1.8)
txt(px + 550, py + 108, "only if contested", 9.6, True, PRB)
txt(px, py + 122, "contested = low verifier confidence · challenger finds the benign story strong · specialists disagree · "
    "AI band ≠ rule band · score within 5 of a cut", 10, False, MUT)
hy = ly + lh + 16
card(66, hy, 1378, 72, "HARNESS — the same bounded loop for every agent", [], PRB, "#fff6e6", PRB_LN, 12.5)
txt(79, hy + 47, "budgets (steps · tool calls · tokens · wall-clock) force a final answer   ·   per-agent tool allowlist   ·   "
    "typed output with bounded repair   ·   fast→strong escalation   ·   model fallback and circuit breaker   ·   "
    "span, token and cost per call", 10.4, False, INK)
txt(79, hy + 62, "fail-closed: when every model fails the case is marked unavailable and the rules-only result stands — nothing is invented to fill the gap", 10.4, True, PRB)
card(1466, ly, 328, 214, "Shared capability",
     ["9 read-only tools — peer stats, similar and linked cases, notes, note search, rule catalogue, cluster context",
      "Chatbot over the same tools, at case or queue scope, reading notes and the audit tail",
      "Assistive: rule drafting from English, briefing narrative, escalation handoff — each restricted to computed facts"],
     PRB, "#ffffff", PRB_LN, 12.5, 10.4)
for x, lab in ((FASTX + FASTW / 2, "no rule fired"), (OBVX + FASTW / 2, "CRITICAL"), (DEEPX + DEEPW / 2, "everything else")):
    arrow([(x, ry + rh + 4), (x, Z2 - 9)], DET)
    txt(x + 10, ry + rh + 32, lab, 10.4, True, DET)

# ── verification boundary ─────────────────────────────────────────────────────
VY, VH = 922, 108
rect(46, VY, W - 92, VH, "#0f172a", "#0f172a", 12, 1.6)
txt(70, VY + 32, "VERIFICATION BOUNDARY", 14.5, True, "#ffffff")
txt(70 + tw("VERIFICATION BOUNDARY", 14.5, True) + 14, VY + 32, "deterministic — nothing reaches a human unchecked", 11.5, False, "#94a3b8")
checks = [("Re-grounded", "every cited field and value is re-checked against the record; ungrounded claims are dropped and shown"),
          ("Clamped", "the model may move the score by ±20; the band is derived from the score, never asserted"),
          ("Refused", "no clear where a HIGH/CRITICAL rule or a fraud lookalike fired; no downgrade when a linked case is CRITICAL"),
          ("Attributed", "every figure traced to the evidence; an unverifiable one is flagged rather than published")]
cw = (W - 140) / 4
for i, (k, v) in enumerate(checks):
    x = 70 + i * cw
    txt(x, VY + 60, k, 11, True, "#facc15")
    end = para(x, VY + 76, v, cw - 30, 9.8, "#cbd5e1")
    if end > VY + VH - 4:
        problems.append(f"boundary column '{k}' overflows by {end - (VY + VH):.0f}px")

# ── zone 3 · human authority + record ─────────────────────────────────────────
Z3, Z3H = 1086, 212
zone(Z3, Z3H, 3, "HUMAN AUTHORITY + SYSTEM OF RECORD",
     "the AI never changes a case; every decision carries a reason and an actor", HUM, HUM_BG, HUM_LN)
ay = Z3 + 52
card(66, ay, 452, 134, "Assessment — a versioned artifact",
     ["summary · indicators that each cite a field and value · mitigating factors · band · recommended action · confidence",
      "investigation trace: which agents ran, which tools, what the boundary dropped, tokens and cost"], HUM, "#ffffff", HUM_LN, 13, 10.6)
card(542, ay, 700, 134, "Human decisions",
     ["Investigator — accept or reject each finding · add a note · clear one case, a selection or a filter (behind guardrails) · escalate · mark confirmed fraud or legitimate",
      "Supervisor — approve or return escalations · enact hard rules · override a decline · refer to SIU",
      "Separation of duty: investigators propose hard rules, only supervisors enact them"], HUM, "#ffffff", HUM_LN, 13, 10.6)
card(1266, ay, 528, 134, "System of record",
     ["cases · assessments · notes · feedback · outcomes · escalations · runs and spans · rule versions · blocklist · full audit log",
      "an assessment is keyed by a hash of everything it reasoned over, so staleness is detected rather than guessed"], HUM, "#ffffff", HUM_LN, 13, 10.6)
arrow([(520, ay + 67), (540, ay + 67)], HUM, 1.8)
arrow([(1244, ay + 67), (1264, ay + 67)], HUM, 1.8)
arrow([(930, VY + VH + 4), (930, ay - 8)], HUM)

# ── feedback ──────────────────────────────────────────────────────────────────
FY = 1336
arrow([(1530, ay + 138), (1530, FY), (1832, FY), (1832, Z1 + Z1H / 2), (W - 92 + 48, Z1 + Z1H / 2)], MUT, 2, "7 5")
add(f'<path d="M66,{FY - 16} L100,{FY - 16}" stroke="{MUT}" stroke-width="2.2" stroke-dasharray="6 4"/>')
txt(110, FY - 12, "FEEDBACK", 11.5, True, MUT)
txt(110 + tw("FEEDBACK", 11.5, True) + 12, FY - 12,
    "a note, a confirmed outcome, a rule change or a released decline alters what the system knows → affected cases are re-scored and re-assessed; "
    "specialist findings are reused by evidence hash, so a new note costs 2 calls, not 5", 10.6, False, MUT)
txt(110, FY + 17, "Confirmed fraud is the only ground truth this system gets: it re-scores lookalikes, blocks them from being cleared, "
    "and is the label a future backtest will be measured against.", 10.6, False, FAINT)

add("</svg>")
svg = "\n".join(out)
(OUT / "architecture.svg").write_text(svg)
(OUT / "architecture.html").write_text(
    f"<!doctype html><meta charset='utf-8'><title>Architecture</title><body style='margin:0;background:#fff'>{svg}</body>")
print("wrote docs/architecture.svg —", "layout OK" if not problems else f"{len(problems)} PROBLEM(S)")
for p in problems:
    print("  ", p)
