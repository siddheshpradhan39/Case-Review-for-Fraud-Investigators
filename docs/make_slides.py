"""Builds docs/Writeup.pptx: the 3-slide write-up (deliverable 4.2).   Run:  python docs/make_slides.py
Also runs an automated fit check (Arial metrics via PIL) so text that would overflow its shape fails the build."""
import sys
from pathlib import Path

from lxml import etree
from PIL import ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor as C
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches as I, Pt

OUT = Path(__file__).resolve().parent / "Writeup.pptx"
FONT = "Arial"
INK, MUT, BRAND, SOFT, WHITE, LINE = C(0x1A, 0x1F, 0x2B), C(0x5B, 0x64, 0x78), C(0x3B, 0x4F, 0xD8), C(0xEE, 0xF0, 0xFD), C(255, 255, 255), C(0xD9, 0xDD, 0xE7)
RED, ORANGE, AMBER, GREEN = C(0xB4, 0x23, 0x18), C(0xC4, 0x52, 0x0A), C(0xA1, 0x62, 0x07), C(0x0E, 0x7A, 0x4B)
RED_BG, ORANGE_BG, AMBER_BG, GREEN_BG = C(0xFE, 0xE4, 0xE2), C(0xFF, 0xEA, 0xD5), C(0xFE, 0xF3, 0xC7), C(0xD9, 0xF5, 0xE6)

prs = Presentation()
prs.slide_width, prs.slide_height = I(13.333), I(7.5)
TOTAL = 3


def fmt_run(r, size, bold=False, color=INK, italic=False):
    r.font.size, r.font.bold, r.font.italic, r.font.name = Pt(size), bold, italic, FONT
    r.font.color.rgb = color


def set_bullet(p, indent=0.15):
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(int(I(indent))))
    pPr.set("indent", str(-int(I(indent))))
    bu = etree.SubElement(pPr, qn("a:buChar"))
    bu.set("char", "•")


def fill_text(tf, paras, anchor=MSO_ANCHOR.TOP, margins=(0.14, 0.1, 0.14, 0.08)):
    """paras: list of dicts {t, size, bold, color, bullet, after, align, runs:[(text,bold,color)]}"""
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left, tf.margin_top, tf.margin_right, tf.margin_bottom = (I(m) for m in margins)
    for i, d in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = d.get("align", PP_ALIGN.LEFT)
        p.space_after = Pt(d.get("after", 3))
        p.line_spacing = 1.0
        for text, bold, color in d.get("runs") or [(d["t"], d.get("bold", False), d.get("color", INK))]:
            fmt_run(p.add_run(), d.get("size", 11), bold, color)
            p.runs[-1].text = text
        if d.get("bullet"):
            set_bullet(p)


def box(slide, x, y, w, h, paras, fill=SOFT, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, anchor=MSO_ANCHOR.TOP, radius=0.05, margins=(0.14, 0.1, 0.14, 0.08)):
    s = slide.shapes.add_shape(shape, I(x), I(y), I(w), I(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    fill_text(s.text_frame, paras, anchor, margins)
    return s


def text(slide, x, y, w, h, paras, anchor=MSO_ANCHOR.TOP):
    b = slide.shapes.add_textbox(I(x), I(y), I(w), I(h))
    fill_text(b.text_frame, paras, anchor, margins=(0, 0, 0, 0))
    return b


def head(t, size=13):
    return {"t": t, "size": size, "bold": True, "color": BRAND, "after": 5}


def bl(t, size=11, after=4, runs=None):
    return {"t": t, "size": size, "bullet": True, "after": after, **({"runs": runs} if runs else {})}


def slide_frame(title, sub, n, notes):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    text(s, 0.5, 0.3, 12.3, 0.62, [{"t": title, "size": 25, "bold": True, "after": 0}])
    text(s, 0.5, 0.93, 12.3, 0.32, [{"t": sub, "size": 13, "color": MUT, "after": 0}])
    text(s, 0.5, 7.08, 9, 0.25, [{"t": "Junior AI Investigator  ·  Take-home write-up", "size": 9, "color": MUT, "after": 0}])
    text(s, 11.8, 7.08, 1.0, 0.25, [{"t": f"{n} / {TOTAL}", "size": 9, "color": MUT, "after": 0, "align": PP_ALIGN.RIGHT}])
    s.notes_slide.notes_text_frame.text = notes
    return s


def arrow(slide, x, y, w=0.28, h=0.26):
    a = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, I(x), I(y), I(w), I(h))
    a.fill.solid()
    a.fill.fore_color.rgb = BRAND
    a.line.fill.background()
    return a


def tile(slide, x, y, w, h, big, label, color=BRAND, bg=SOFT):
    box(slide, x, y, w, h, [{"t": big, "size": 22, "bold": True, "color": color, "after": 0, "align": PP_ALIGN.CENTER},
                            {"t": label, "size": 10, "color": MUT, "after": 0, "align": PP_ALIGN.CENTER}], fill=bg, anchor=MSO_ANCHOR.MIDDLE)


# ------------------------------------------------------------------ slide 1
s = slide_frame("A Junior AI Investigator that triages the queue before a human opens it",
                "Product: who it is for, the job to be done, and what I chose to build versus leave out", 1,
                "PRODUCT. The user is a fraud investigator (and their supervisor) who starts the day with a queue of engine-referred claims, most of which turn out benign. "
                "The job to be done is to decide quickly and defensibly which referrals deserve human time, and on those, to get the evidence, the reasoning and the next step without hunting through dozens of signals.\n\n"
                "The data shaped the design: three tiers (8 extreme, 9 ambiguous, 33 clean), three 'silent' cases that a flag-counting model would call clean, and one claim number reused across a clean and a critical case. "
                "So rules handle the obvious, and AI plus humans spend their time on the ambiguous middle and on cross-case links.\n\n"
                "DEMO PATH: open the Morning briefing, then a critical case, then C1001 (the linked-claim story).")
box(s, 0.5, 1.45, 5.95, 1.95, [head("Who it is for, and the job to be done"),
    bl("User: a fraud investigator, and their supervisor, who starts each day with engine-referred claims. Most turn out benign."),
    bl("Job: decide quickly and defensibly which referrals deserve human time, and for those, get the evidence, reasoning and next step without hunting through dozens of signals."),
    bl("The human stays in control; the AI does the first pass.")])
box(s, 0.5, 3.53, 5.95, 2.2, [head("What the data told me (it shaped the design)"),
    bl("50 cases fall into three tiers: 8 extreme on every signal, 9 ambiguous, 33 clean."),
    bl("3 'silent' cases trip no flag but have five elevated signals. A flag-count model scores them clean."),
    bl("One claim number sits on both a clean and a critical case. No per-row rule can see that."),
    bl("So: rules for the obvious; AI and humans for the ambiguous middle and cross-case links.")])
tile(s, 0.5, 5.86, 2.45, 1.04, "8 · 9 · 33", "case tiers in the data")
tile(s, 3.08, 5.86, 1.7, 1.04, "17", "rules, thresholds from the data")
tile(s, 4.91, 5.86, 1.54, 1.04, "88", "tests passing", color=GREEN, bg=GREEN_BG)
box(s, 6.7, 1.45, 6.13, 3.5, [head("What I built"),
    bl("Morning briefing: risk counts and exposure, top cases, rule clusters, abnormal firing", 11, 6),
    bl("Per-case AI assessment: summary, grounded indicators, risk level, recommended action, full trace", 11, 6),
    bl("Chatbot with tools; notes it reads (deletable everywhere) plus notes and outcomes from related cases", 11, 6),
    bl("Bulk clear by filter or by selection, behind guardrails", 11, 6),
    bl("Escalation to a supervisor with an AI-drafted handoff", 11, 6),
    bl("Rules tab: create or tune rules live and backtest precision and recall", 11, 6),
    bl("Blocklist: hard rules that auto-decline, with supervisor override", 11, 6),
    bl("'Mark as FRAUD' feedback: lookalikes get flagged (rule R17)", 11, 6),
    bl("Audit log of every AI action and human decision", 11, 6)])
box(s, 6.7, 5.08, 6.13, 1.82, [head("Left out on purpose"),
    bl("Auth, multi-tenancy, deployment: not the AI experience."),
    bl("Training an ML model: there are no labels, so an accuracy claim would be made up."),
    bl("Provider or member graph, external enrichment: the CSV has no IDs (the code supports them if a dataset has them)."),
    bl("Assumptions: synthetic, unlabelled data; one row per referral; peer % is vs the same care type.", after=0)], fill=C(0xF4, 0xF5, 0xF8), line=LINE)

# ------------------------------------------------------------------ slide 2
s = slide_frame("Rules first; agents reason only as deep as each case needs",
                "Architecture: how the system reasons, stays grounded, and scales", 2,
                "ARCHITECTURE. Not a single LLM call. Deterministic rules score every case first (thresholds come from natural breaks in the data, not guesses). "
                "A router with no LLM then sends each case down one of three lanes: 29 clean cases share one cheap batched call, 8 critical cases get a verifier only, and 13 ambiguous cases get the full crew.\n\n"
                "On the DEEP lane, specialists run in parallel only where their domain fired; an adversarial challenger argues the benign case; a verifier synthesises; a three-persona panel convenes only if the case is contested. "
                "Every output then goes through deterministic guardrails.\n\n"
                "GROUNDING: the model can only move the score by 20 points, the level is derived, every citation is re-checked against the record, and failures are shown as 'LLM unavailable', never invented.\n\n"
                "MEASURED: 10/10 invariants, 5/5 counterfactual and prompt-injection tests; versus the single-judge baseline, 100% band agreement and 27% fewer tokens. "
                "The strong-tier verifier makes reference dollars higher (0.44 vs 0.12), so I do not claim it is cheaper in dollars.\n\n"
                "DEMO PATH: Investigation trace on C1001 (reused specialist, challenger, panel, and the linked-case floor overruling a -10 vote).")
y0 = 1.42
box(s, 0.5, y0, 2.15, 0.78, [{"t": "CSV + derived features", "size": 10.5, "bold": True, "after": 1, "align": PP_ALIGN.CENTER}, {"t": "travel miles, log-z, links", "size": 9, "color": MUT, "after": 0, "align": PP_ALIGN.CENTER}], anchor=MSO_ANCHOR.MIDDLE)
arrow(s, 2.7, y0 + 0.26)
box(s, 3.05, y0, 2.85, 0.78, [{"t": "17 rules (R01–R17)", "size": 10.5, "bold": True, "after": 1, "align": PP_ALIGN.CENTER}, {"t": "thresholds from natural breaks in the data", "size": 9, "color": MUT, "after": 0, "align": PP_ALIGN.CENTER}], anchor=MSO_ANCHOR.MIDDLE)
arrow(s, 5.95, y0 + 0.26)
box(s, 6.3, y0, 2.75, 0.78, [{"t": "Score 0–100", "size": 10.5, "bold": True, "after": 1, "align": PP_ALIGN.CENTER}, {"t": "strongest rule per domain, noisy-OR", "size": 9, "color": MUT, "after": 0, "align": PP_ALIGN.CENTER}], anchor=MSO_ANCHOR.MIDDLE)
arrow(s, 9.1, y0 + 0.26)
box(s, 9.45, y0, 3.38, 0.78, [{"t": "Router: deterministic, no LLM", "size": 10.5, "bold": True, "color": WHITE, "after": 1, "align": PP_ALIGN.CENTER}, {"t": "picks depth from the fired rules", "size": 9, "color": WHITE, "after": 0, "align": PP_ALIGN.CENTER}], fill=BRAND, anchor=MSO_ANCHOR.MIDDLE)
ly = 2.34
box(s, 0.5, ly, 2.6, 1.25, [{"t": "FAST  ·  29 cases", "size": 12, "bold": True, "color": GREEN, "after": 3}, {"t": "No rule fired. One cheap call reviews 5 cases at once. No tools.", "size": 11, "after": 0}], fill=GREEN_BG)
box(s, 3.25, ly, 2.6, 1.25, [{"t": "OBVIOUS  ·  8 cases", "size": 12, "bold": True, "color": RED, "after": 3}, {"t": "Rule level CRITICAL. Verifier only, since the facts are unambiguous.", "size": 11, "after": 0}], fill=RED_BG)
box(s, 6.0, ly, 6.83, 1.25, [{"t": "DEEP  ·  13 cases (ambiguous or linked)", "size": 12, "bold": True, "color": AMBER, "after": 3},
    {"t": "Specialists run in parallel, only where their domain fired: billing, collusion and linkage, geography and utilization.  →  Adversarial challenger argues the benign case.  →  Verifier synthesises.  →  3-persona judge panel only if the case is contested.", "size": 11, "after": 0}], fill=AMBER_BG)
box(s, 0.5, 3.72, 12.33, 0.42, [{"runs": [("Grounding gate ", True, WHITE), ("on every output  →  assessment + investigation trace + cost.   Chat and agents share 9 read-only tools (peer stats, similar and linked cases, notes, search).", False, WHITE)], "size": 10, "after": 0}], fill=INK, anchor=MSO_ANCHOR.MIDDLE, radius=0.12)
cy, ch, cw = 4.3, 2.65, 4.03
box(s, 0.5, cy, cw, ch, [head("Grounded, not just prompted", 13),
    bl("Rules are deterministic. The model moves the score by at most ±20; the level is derived from the score.", 11, 5),
    bl("Every cited field=value is re-checked against the record (286 of 286 stored indicators pass). Invented figures are flagged.", 11, 5),
    bl("It cannot recommend clearing a HIGH/CRITICAL case or a lookalike of a confirmed fraud, or downgrade a case linked to a CRITICAL one.", 11, 5),
    bl("Notes are untrusted input. Failures show as 'LLM unavailable', never invented text.", 11, after=0)])
box(s, 0.5 + cw + 0.12, cy, cw, ch, [head("Harness and measurements", 13),
    bl("Every agent has budgets, a tool allowlist, typed output with repair, fast→strong escalation, model fallback and traced calls.", 11, 5),
    bl("10/10 behavioural invariants; 5/5 counterfactual and prompt-injection tests.", 11, 5),
    bl("Versus a single-judge baseline: 100% band agreement, 27% fewer tokens (238k vs 328k). A new note re-runs 2 calls, not 5.", 11, after=0)])
box(s, 0.5 + 2 * (cw + 0.12), cy, cw, ch, [head("Beyond 50 cases", 13),
    bl("Router cost is O(1) per case, so effort lands on the hard tail; real queues have a larger FAST share.", 11, 5),
    bl("Batching, parallel subagents, reuse by evidence hash, model tiers and budgets keep cost bounded.", 11, 5),
    bl("Prototype limits: SQLite, in-process workers, O(n) similarity. Next: Postgres, queue workers, a vector index.", 11, 5),
    bl("About 4 sequential LLM hops (≈65 s on free models): fine for a morning batch, not per click.", 11, after=0)])

# ------------------------------------------------------------------ slide 3
s = slide_frame("The AI advises, humans decide, and the risks are named",
                "Human in the loop, trade-offs, and what I would do next", 3,
                "HUMAN IN THE LOOP. The AI never changes a case's status: every clear, escalation, override and outcome is a human action with a reason, in an audit log. "
                "Investigators can see the rule score next to the AI score, check every indicator against its field and value, accept or reject it (rejections are respected next time), and read the agent trace and the guardrail removals.\n\n"
                "Notes are the investigator's steering wheel: a note changes the next assessment, and deleting a note removes it from the AI's knowledge, the chat context, related-case context and audit text.\n\n"
                "TRADE-OFFS. No labels means no accuracy claim; backtests use synthetic labels and say so. False negatives are the costliest risk and only discovered misses can be measured, "
                "so the next step is a safety net: miss-rate tracking, look-back that reopens cleared lookalikes, an independent adversarial reviewer and audited sampling. "
                "I prototyped an anomaly detector and deliberately deferred it because it was unstable at 50 cases.\n\n"
                "DEMO PATH: add a note, watch the assessment go stale, ask the chatbot about it, delete it; then a Blocklist decline and supervisor override; then the Rules tab backtest.")
cw3, cy3, ch3 = 4.03, 1.42, 5.53
box(s, 0.5, cy3, cw3, ch3, [head("How investigators trust, check and override it", 14),
    bl("Rule score and AI score are shown side by side.", 11.5, 7),
    bl("Every indicator cites a field and value. Accept ✓ or reject ✗; rejected findings are not re-asserted.", 11.5, 7),
    bl("Guardrail removals and the full agent trace are visible.", 11.5, 7),
    bl("A note changes the next assessment. Deleting it removes it from the AI, the chat and the audit text.", 11.5, 7),
    bl("Every clear, escalation and override needs a reason and is audited. The AI never changes a status.", 11.5, 7),
    bl("Bulk clear previews what is blocked, and why.", 11.5, 7),
    bl("Supervisors approve or return escalations, override blocklist declines and activate hard rules; investigators can only propose.", 11.5, 7),
    bl("Rules tab: draft, backtest with impact preview, then save or run in shadow.", 11.5, 7),
    bl("'Mark as FRAUD' teaches the system: lookalikes are flagged and clearing them is blocked.", 11.5, after=0)])
box(s, 0.5 + cw3 + 0.12, cy3, cw3, ch3, [head("Trade-offs and risks", 14),
    bl("Accuracy: no labels. Backtests use synthetic labels (leave-one-out, so a rule does not grade itself): they show agreement, not fraud.", 11.5, 7),
    bl("Cost: the crew uses fewer tokens but more reference dollars ($0.44 vs $0.12) because of the strong-tier verifier.", 11.5, 7),
    bl("Latency: about 20 s per call on free models.", 11.5, 7),
    bl("Trust: the prose can still be subtly wrong even when every cited field is right.", 11.5, 7),
    bl("Abuse: injection notes, bulk clear, over-broad blocklist entries (confirmation needed above 25% of the queue).", 11.5, 7),
    bl("Fairness: distance and weekend signals can proxy for rural or shift-work providers.", 11.5, 7),
    bl("False negatives are the costliest risk, and only discovered misses can be measured.", 11.5, 7),
    bl("My own evals found: the challenger rates every case 'medium', and confidence barely separates contested cases.", 11.5, after=0)], fill=C(0xF4, 0xF5, 0xF8), line=LINE)
box(s, 0.5 + 2 * (cw3 + 0.12), cy3, cw3, ch3, [head("With more time, in priority order", 14),
    bl("False-negative safety net: miss-rate tracking from confirmed outcomes; a look-back that reopens cleared lookalikes; an independent adversarial reviewer; audited sampling with a statistical bound.", 11.5, 7),
    bl("Anomaly detector: prototyped, deferred because it is unstable at 50 cases.", 11.5, 7),
    bl("Re-fit weights and thresholds from confirmed outcomes.", 11.5, 7),
    bl("A provider and member graph; a labelled backtest.", 11.5, 7),
    bl("Four-eyes on large bulk clears; a streaming UI.", 11.5, 7),
    {"t": "Deliberately cut: auth, deployment, enrichment, model training.", "size": 11.5, "color": MUT, "after": 0}], fill=BRAND_BG if False else C(0xEC, 0xF7, 0xF1))

prs.save(OUT)


# ------------------------------------------------------------------ automated fit check (Arial metrics)
_F = {}


def _font(size, bold):
    key = (round(size * 10), bold)
    if key not in _F:
        _F[key] = ImageFont.truetype(f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf", key[0])
    return _F[key]


def _lines(txt, size, bold, width_pt):
    f, words, n, cur = _font(size, bold), txt.split(" "), 1, ""
    for w in words:
        trial = (cur + " " + w).strip()
        if f.getlength(trial) / 10 <= width_pt or not cur:
            cur = trial
        else:
            n, cur = n + 1, w
    return n


def check(path):
    prs2, bad = Presentation(path), []
    for si, slide in enumerate(prs2.slides, 1):
        for sh in slide.shapes:
            if not sh.has_text_frame:
                continue
            x, y, w, h = (sh.left / 914400, sh.top / 914400, sh.width / 914400, sh.height / 914400)
            if x < 0.45 or y < 0.25 or x + w > 13.333 - 0.45 or y + h > 7.5 - 0.1:
                bad.append(f"slide {si}: shape at ({x:.2f},{y:.2f},{w:.2f},{h:.2f}) is outside the margins")
            tf, total = sh.text_frame, (tf_margin := (sh.text_frame.margin_top + sh.text_frame.margin_bottom) / 12700)
            avail = w * 72 - (tf.margin_left + tf.margin_right) / 12700
            for p in tf.paragraphs:
                if not p.runs:
                    continue
                size = p.runs[0].font.size.pt
                bold = any(r.font.bold for r in p.runs)
                marl = int(p._p.pPr.get("marL", 0)) / 12700 if p._p.pPr is not None else 0
                n = _lines("".join(r.text for r in p.runs), size, bold, avail - marl)
                total += n * size * 1.2 + (p.space_after.pt if p.space_after is not None else 0)
            if total > h * 72 * 0.97:
                bad.append(f"slide {si}: text needs {total / 72:.2f} in but shape is {h:.2f} in: \"{tf.text[:50]}...\"")
    return bad


problems = check(OUT)
print("saved", OUT)
print("FIT CHECK:", "OK — all text fits, all shapes inside margins" if not problems else "")
for p in problems:
    print("  PROBLEM", p)
sys.exit(1 if problems else 0)
