"""Hand-drawn style "agentic framework" diagram of the Junior AI Investigator -> docs/architecture_flow.png
Run:  python docs/make_architecture_sketch.py      (Pillow only; wobbly strokes, hatched pastel fills, monospace text)"""
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "architecture_flow.png"
W, H, S = 1420, 985, 2
INK = (30, 30, 30)
C = {"green": (166, 240, 166), "blue": (158, 210, 255), "yellow": (255, 230, 128), "pink": (255, 194, 194), "orange": (255, 216, 168),
     "lav": (222, 212, 255), "band": (226, 245, 223), "hatch_blue": (203, 226, 247), "hatch_pink": (250, 214, 218), "hatch_green": (206, 238, 208), "white": (255, 255, 255)}
MONO, MONOB = "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Menlo.ttc"
rng = random.Random(7)
im = Image.new("RGB", (W * S, H * S), "white")
dr = ImageDraw.Draw(im)
_f = {}


def font(size, bold=False):
    k = (size, bold)
    if k not in _f:
        _f[k] = ImageFont.truetype(MONO, round(size * S), index=1 if bold else 0)
    return _f[k]


def s(v):
    return v * S


def wobble(pts, amt=1.3):
    return [(x + rng.uniform(-amt, amt), y + rng.uniform(-amt, amt)) for x, y in pts]


def resample(pts, step=14):
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
        out += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n)]
    return out + [pts[-1]]


def rr_pts(x0, y0, x1, y1, r):
    def arc(cx, cy, a0, a1):
        n = max(4, int(r / 3))
        return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
    p = arc(x1 - r, y0 + r, -math.pi / 2, 0) + arc(x1 - r, y1 - r, 0, math.pi / 2) + arc(x0 + r, y1 - r, math.pi / 2, math.pi) + arc(x0 + r, y0 + r, math.pi, 1.5 * math.pi)
    return p + [p[0]]


def hatch(x0, y0, x1, y1, r, color, gap=7):
    layer = Image.new("RGB", im.size, "white")
    ld = ImageDraw.Draw(layer)
    k = -int(y1 - y0)
    while k < int(x1 - x0) + int(y1 - y0):
        ld.line([(s(x0 + k), s(y1)), (s(x0 + k + (y1 - y0)), s(y0))], fill=color, width=S)
        k += gap
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([s(x0), s(y0), s(x1), s(y1)], s(r), fill=255)
    im.paste(layer, (0, 0), mask)


def box(x0, y0, x1, y1, fill="white", r=14, hatched=False, lw=2.2, dash=False):
    col = C.get(fill, fill)
    dr.rounded_rectangle([s(x0), s(y0), s(x1), s(y1)], s(r), fill=col)
    if hatched:
        hatch(x0, y0, x1, y1, r, tuple(max(0, c - 28) for c in col))
    for _ in range(2):
        pts = resample(rr_pts(x0, y0, x1, y1, r))
        pts = wobble(pts, 1.2)
        if dash:
            for i in range(0, len(pts) - 1, 2):
                dr.line([(s(pts[i][0]), s(pts[i][1])), (s(pts[i + 1][0]), s(pts[i + 1][1]))], fill=INK, width=round(s(lw)))
        else:
            dr.line([(s(x), s(y)) for x, y in pts], fill=INK, width=round(s(lw)), joint="curve")


def text(x, y, t, size=16, bold=False, color=INK, angle=0):
    if angle:
        f = font(size, bold)
        tw = int(dr.textlength(t, font=f)) + 10
        layer = Image.new("L", (tw, int(s(size * 1.6))), 0)
        ImageDraw.Draw(layer).text((tw // 2, layer.size[1] // 2), t, font=f, fill=255, anchor="mm")
        layer = layer.rotate(angle, expand=True)
        im.paste(Image.new("RGB", layer.size, color), (int(s(x) - layer.size[0] / 2), int(s(y) - layer.size[1] / 2)), layer)
        return
    dr.text((s(x), s(y)), t, font=font(size, bold), fill=color, anchor="mm")


def wrap(t, width_px, size, bold=False):
    f, lines, cur = font(size, bold), [], ""
    for w in t.split(" "):
        trial = (cur + " " + w).strip()
        if dr.textlength(trial, font=f) / S <= width_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def chip(x0, y0, x1, y1, fill, title, sub=None, tsize=16, ssize=12, hatched=False, r=14):
    box(x0, y0, x1, y1, fill, r, hatched)
    lines = wrap(title, x1 - x0 - 16, tsize)
    sl = wrap(sub, x1 - x0 - 16, ssize) if sub else []
    total = len(lines) * (tsize + 4) + len(sl) * (ssize + 3)
    y = (y0 + y1) / 2 - total / 2 + tsize / 2
    for ln in lines:
        text((x0 + x1) / 2, y, ln, tsize)
        y += tsize + 4
    y += -1
    for ln in sl:
        text((x0 + x1) / 2, y, ln, ssize, color=(70, 70, 70))
        y += ssize + 3


def arrow(pts, dashed=False, head=True, lw=2.2):
    pts = resample(pts, 10) if len(pts) > 1 else pts
    pts = wobble(pts, 0.9)
    seg = list(zip(pts, pts[1:]))
    for i, ((x1, y1), (x2, y2)) in enumerate(seg):
        if dashed and (i // 2) % 2:
            continue
        dr.line([(s(x1), s(y1)), (s(x2), s(y2))], fill=INK, width=round(s(lw)))
    if head:
        (x1, y1), (x2, y2) = pts[-6 if len(pts) > 6 else 0], pts[-1]
        a = math.atan2(y2 - y1, x2 - x1)
        for d in (2.55, -2.55):
            dr.line([(s(x2), s(y2)), (s(x2 + 16 * math.cos(a + d)), s(y2 + 16 * math.sin(a + d)))], fill=INK, width=round(s(lw)))


# ------------------------------------------------------------------ frame + title
box(255, 28, 1160, 950, "white", 18, lw=2.6)
text(707, 70, "Agentic framework: Junior AI Investigator", 30, True)
chip(292, 98, 1122, 152, "band", "Large Language Model (LLM)", "OpenRouter · fast + strong tiers · multi-model fallback", 21, 13, hatched=True)

# ------------------------------------------------------------------ user / system
box(40, 320, 205, 730, "white", 16)
# stick figure
dr.ellipse([s(107), s(372), s(137), s(402)], outline=INK, width=round(s(3)))
for a, b in (((122, 402), (122, 445)), ((122, 418), (100, 435)), ((122, 418), (146, 410)), ((122, 445), (104, 470)), ((122, 445), (142, 468))):
    dr.line([(s(a[0]), s(a[1])), (s(b[0]), s(b[1]))], fill=INK, width=round(s(4)))
text(122, 500, "User", 17)
text(122, 520, "investigator", 12, color=(70, 70, 70))
text(122, 535, "supervisor", 12, color=(70, 70, 70))
box(85, 585, 160, 650, "yellow", 4, hatched=True, lw=1.8)
text(122, 685, "System", 17)
text(122, 705, "referred claims", 12, color=(70, 70, 70))
arrow([(210, 455), (288, 455)])

# ------------------------------------------------------------------ planning agent
chip(292, 405, 435, 512, "pink", "Planning agent", "rules · blocklist · router (no LLM)", 17, 12)
arrow([(400, 402), (478, 262)])
arrow([(438, 470), (478, 470)])
box(345, 556, 472, 626, "pink", 12, dash=True, lw=2)
text(408, 578, "Blocklist hit", 13, True)
text(408, 596, "⇒ Declined", 13, True)
text(408, 613, "locked · override", 10.5, color=(70, 70, 70))
arrow([(408, 514), (408, 553)], dashed=True)

# ------------------------------------------------------------------ light lanes
box(480, 188, 1042, 335, "white", 20)
text(760, 210, "Light lanes: one per case", 15)
chip(508, 232, 700, 305, "green", "FAST · batch reviewer", "5 clean cases per call", 15, 11)
text(770, 268, "or", 15)
chip(825, 232, 1015, 305, "blue", "OBVIOUS · verifier", "CRITICAL cases", 15, 11)
arrow([(1045, 262), (1062, 262)])

# ------------------------------------------------------------------ deep lane
box(480, 358, 1042, 632, "white", 20)
text(745, 378, "DEEP lane: parallel specialists, then one decision", 15)
chip(505, 396, 645, 448, "green", "Billing", None, 16)
chip(505, 462, 645, 514, "blue", "Collusion", None, 16)
chip(505, 528, 645, 580, "yellow", "Geo / Util", None, 16)
chip(690, 452, 800, 524, "orange", "Challenger", "argues benign", 15, 11)
chip(846, 446, 978, 530, "pink", "Verifier", "decision agent", 16, 11)
chip(818, 556, 1012, 618, "lav", "Judge panel ×3", "only if contested", 15, 11)
for yc in (422, 488, 554):
    arrow([(648, yc), (688, 488)], head=(yc == 488))
arrow([(803, 488), (843, 488)])
arrow([(912, 532), (912, 553)], dashed=True)
arrow([(981, 488), (1060, 488)])

# ------------------------------------------------------------------ guardrails
box(1064, 188, 1116, 632, "hatch_blue", 10, hatched=True)
text(1090, 410, "Guardrails", 19, True, angle=270)

# ------------------------------------------------------------------ memory
box(295, 690, 1115, 832, "white", 18)
text(705, 716, "Memory", 22)
chip(320, 742, 520, 812, "hatch_pink", "Notes & feedback", "deletable everywhere", 14, 11, hatched=True)
chip(555, 742, 765, 812, "hatch_green", "Related cases & outcomes", "confirmed fraud / legit", 14, 11, hatched=True)
chip(800, 742, 1090, 812, "hatch_blue", "Assessments & audit trail", "versioned, hashed inputs", 14, 11, hatched=True)

# ------------------------------------------------------------------ observability
chip(295, 858, 1115, 928, "band", "Observability & Analytics", "investigation trace · tokens & cost · audit log · evals · rule backtests", 21, 13, hatched=True)

# ------------------------------------------------------------------ outputs + adaptation
chip(1205, 372, 1400, 470, "yellow", "Assessment", "summary · evidence · risk · next step + trace", 17, 12)
arrow([(1118, 488), (1160, 488), (1160, 420), (1202, 420)])
chip(1205, 545, 1400, 650, "green", "Human decision", "accept / reject · clear · escalate · notes · mark fraud", 17, 12)
arrow([(1302, 473), (1302, 542)])
arrow([(1302, 653), (1302, 680), (312, 680), (312, 516)], dashed=True)
text(800, 660, "Adaptation & learning: notes · outcomes · rule changes ⇒ re-score, re-assess only what changed", 13)

im = im.resize((W * 3 // 2, H * 3 // 2), Image.LANCZOS)
im.save(OUT, dpi=(144, 144))
print("wrote", OUT, im.size)
