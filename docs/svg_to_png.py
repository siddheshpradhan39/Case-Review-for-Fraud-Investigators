"""Minimal SVG -> PNG renderer for the subset used by the architecture diagrams (rect, text, line, path M/L/V/H, circle, arrow markers,
dashes). Uses Pillow + Arial so no Cairo/Inkscape is needed.   Usage: python docs/svg_to_png.py in.svg out.png [scale]"""
import math
import re
import sys

from lxml import etree
from PIL import Image, ImageDraw, ImageFont

F = {False: "/System/Library/Fonts/Supplemental/Arial.ttf", True: "/System/Library/Fonts/Supplemental/Arial Bold.ttf"}
NS = "{http://www.w3.org/2000/svg}"


def col(c, default=None):
    if c in (None, "none"):
        return default
    return c


def polyline_pts(d):
    pts, x, y = [], 0.0, 0.0
    for cmd, args in re.findall(r"([MLVHZ])([^MLVHZ]*)", d):
        nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", args)]
        if cmd in "ML":
            x, y = nums[0], nums[1]
        elif cmd == "V":
            y = nums[0]
        elif cmd == "H":
            x = nums[0]
        if cmd != "Z":
            pts.append((x, y))
    return pts


def render(src, dst, S=2):
    root = etree.parse(src).getroot()
    w, h = [int(float(v)) for v in root.get("viewBox").split()[2:]]
    im = Image.new("RGB", (int(w * S), int(h * S)), "white")
    dr = ImageDraw.Draw(im)
    fonts = {}

    def font(size, bold):
        k = (size, bold)
        if k not in fonts:
            fonts[k] = ImageFont.truetype(F[bold], max(1, round(size * S)))
        return fonts[k]

    def dashed(pts, color, sw, dash):
        on, off = [float(v) * S for v in re.split(r"[ ,]+", dash)][:2]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            L = math.hypot(x2 - x1, y2 - y1) * S
            ux, uy = (x2 - x1) * S / L, (y2 - y1) * S / L
            t = 0.0
            while t < L:
                e = min(t + on, L)
                dr.line([(x1 * S + ux * t, y1 * S + uy * t), (x1 * S + ux * e, y1 * S + uy * e)], fill=color, width=max(1, round(sw * S)))
                t += on + off

    def arrowhead(p_from, p_to, color, sw):
        (x1, y1), (x2, y2) = p_from, p_to
        a = math.atan2(y2 - y1, x2 - x1)
        Ln, Wd = 9 * sw * S, 4.5 * sw * S
        tx, ty = x2 * S + math.cos(a) * sw * S, y2 * S + math.sin(a) * sw * S
        bx, by = tx - math.cos(a) * Ln, ty - math.sin(a) * Ln
        dr.polygon([(tx, ty), (bx - math.sin(a) * Wd, by + math.cos(a) * Wd), (bx + math.sin(a) * Wd, by - math.cos(a) * Wd)], fill=color)

    def stroke_path(pts, color, sw, dash, marker):
        if dash:
            dashed(pts, color, sw, dash)
        else:
            dr.line([(x * S, y * S) for x, y in pts], fill=color, width=max(1, round(sw * S)), joint="curve")
        if marker:
            arrowhead(pts[-2], pts[-1], "#3b4fd8" if "ah)" in marker and "ahg" not in marker else "#5b6478", sw)

    for el in root.iter():
        t = el.tag.replace(NS, "")
        g = el.get
        if t == "rect":
            x, y, ww, hh = [float(g(k)) * S for k in ("x", "y", "width", "height")] if g("x") else (0, 0, w * S, h * S)
            if g("x") is None:
                x, y, ww, hh = 0, 0, float(g("width")) * S if g("width") and "%" not in g("width") else w * S, h * S
            rx = float(g("rx", 0)) * S
            fill, stroke = col(g("fill")), col(g("stroke"))
            sw = max(1, round(float(g("stroke-width", 1)) * S))
            if g("stroke-dasharray") and stroke:
                if fill:
                    dr.rounded_rectangle([x, y, x + ww, y + hh], rx, fill=fill)
                r = float(g("rx", 0))
                x0, y0, x1, y1 = x / S, y / S, (x + ww) / S, (y + hh) / S
                dashed([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], stroke, float(g("stroke-width", 1)), g("stroke-dasharray"))
            else:
                dr.rounded_rectangle([x, y, x + ww, y + hh], rx, fill=fill, outline=stroke, width=sw if stroke else 0)
        elif t == "text":
            size, bold = float(g("font-size", 12)), g("font-weight") == "700"
            anchor = {"middle": "ms", "end": "rs"}.get(g("text-anchor"), "ls")
            dr.text((float(g("x")) * S, float(g("y")) * S), el.text or "", font=font(size, bold), fill=g("fill", "#000"), anchor=anchor)
        elif t == "line":
            pts = [(float(g("x1")), float(g("y1"))), (float(g("x2")), float(g("y2")))]
            stroke_path(pts, g("stroke"), float(g("stroke-width", 1)), g("stroke-dasharray"), g("marker-end"))
        elif t == "path" and g("d") and g("stroke") and g("fill") in (None, "none") and el.getparent().tag.replace(NS, "") != "marker":
            stroke_path(polyline_pts(g("d")), g("stroke"), float(g("stroke-width", 1)), g("stroke-dasharray"), g("marker-end"))
        elif t == "circle":
            cx, cy, r = float(g("cx")) * S, float(g("cy")) * S, float(g("r")) * S
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=g("fill"))
    im.save(dst, dpi=(192, 192))
    print("wrote", dst, im.size)


if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 2)
