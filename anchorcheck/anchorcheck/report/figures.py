"""Vector figures for the report: anchor-layout schematic and utilisation chart (ReportLab graphics)."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
from reportlab.lib import colors

from ..design import geometry as geo
from ..models import CaseResult, DesignCase

GREEN, RED, AMBER, GREY = colors.HexColor("#1b7f3b"), colors.HexColor("#b3261e"), colors.HexColor("#b26a00"), colors.HexColor("#6b7280")
NAVY = colors.HexColor("#14365d")


def layout_figure(case: DesignCase, forces: Optional[Sequence[float]] = None, width: float = 250, height: float = 190,
                  font: str = "DejaVuSans") -> Drawing:
    """Plan view: anchors, edges and key dimensions (to scale, fitted to the box)."""
    d = Drawing(width, height)
    L = case.layout
    pts = geo.anchor_coords(L)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = 0.25 * max(case.fastener_d * 6, 120)
    ext_l = (L.c_xneg if L.c_xneg is not None else pad) + (-min(xs))
    ext_r = (L.c_xpos if L.c_xpos is not None else pad) + max(xs)
    ext_b = (L.c_yneg if L.c_yneg is not None else pad) + (-min(ys))
    ext_t = (L.c_ypos if L.c_ypos is not None else pad) + max(ys)
    tw, th = ext_l + ext_r, ext_b + ext_t
    margin = 26
    k = min((width - 2 * margin) / tw, (height - 2 * margin) / th)
    ox = margin + ext_l * k
    oy = margin + ext_b * k
    # concrete outline: free edges solid, remote edges dashed
    x0, x1, y0, y1 = ox - ext_l * k, ox + ext_r * k, oy - ext_b * k, oy + ext_t * k
    d.add(Rect(x0, y0, x1 - x0, y1 - y0, fillColor=colors.HexColor("#f1f3f5"), strokeColor=colors.white, strokeWidth=0))
    for (xa, ya, xb, yb, finite) in ((x0, y0, x0, y1, L.c_xneg is not None), (x1, y0, x1, y1, L.c_xpos is not None),
                                     (x0, y0, x1, y0, L.c_yneg is not None), (x0, y1, x1, y1, L.c_ypos is not None)):
        ln = Line(xa, ya, xb, yb, strokeColor=NAVY if finite else colors.HexColor("#b8bec6"), strokeWidth=2.2 if finite else 0.8)
        if not finite:
            ln.strokeDashArray = [3, 3]
        d.add(ln)
    r = max(3.0, case.fastener_d * k / 2)
    for i, (x, y) in enumerate(pts):
        f = forces[i] if forces else None
        fill = colors.HexColor("#f6c7c3") if (f is not None and f > 1e-9) else colors.white
        d.add(Circle(ox + x * k, oy + y * k, r, fillColor=fill, strokeColor=NAVY, strokeWidth=1.1))
        d.add(Line(ox + x * k - r, oy + y * k, ox + x * k + r, oy + y * k, strokeColor=NAVY, strokeWidth=0.5))
        d.add(Line(ox + x * k, oy + y * k - r, ox + x * k, oy + y * k + r, strokeColor=NAVY, strokeWidth=0.5))
        if f is not None:
            d.add(String(ox + x * k + r + 2, oy + y * k + r, f"{f:+.1f}", fontName=font, fontSize=6.5, fillColor=NAVY))

    def dimline(xa, ya, xb, yb, txt, off=0, vertical=False):
        col = GREY
        if vertical:
            d.add(Line(xa + off, ya, xb + off, yb, strokeColor=col, strokeWidth=0.6))
            d.add(Line(xa, ya, xa + off, ya, strokeColor=col, strokeWidth=0.3))
            d.add(Line(xb, yb, xb + off, yb, strokeColor=col, strokeWidth=0.3))
            d.add(String(xa + off + 2, (ya + yb) / 2 - 2, txt, fontName=font, fontSize=6.5, fillColor=col))
        else:
            d.add(Line(xa, ya + off, xb, yb + off, strokeColor=col, strokeWidth=0.6))
            d.add(Line(xa, ya, xa, ya + off, strokeColor=col, strokeWidth=0.3))
            d.add(Line(xb, yb, xb, yb + off, strokeColor=col, strokeWidth=0.3))
            d.add(String((xa + xb) / 2 - 8, ya + off + 2, txt, fontName=font, fontSize=6.5, fillColor=col))

    if L.n_x > 1 and L.s_x:
        dimline(ox + xs[0] * k, oy + ys[0] * k, ox + xs[1] * k if len(set(xs)) > 1 else ox, oy + ys[0] * k,
                f"{L.s_x:g}", off=-14)
    if L.n_y > 1 and L.s_y:
        yy = sorted(set(ys))
        dimline(ox + max(xs) * k, oy + yy[0] * k, ox + max(xs) * k, oy + yy[1] * k, f"{L.s_y:g}", off=14, vertical=True)
    if L.c_xpos is not None:
        dimline(ox + max(xs) * k, oy + max(ys) * k, x1, oy + max(ys) * k, f"{L.c_xpos:g}", off=10)
    if L.c_xneg is not None:
        dimline(x0, oy + max(ys) * k, ox + min(xs) * k, oy + max(ys) * k, f"{L.c_xneg:g}", off=10)
    if L.c_ypos is not None:
        dimline(ox, oy + max(ys) * k, ox, y1, f"{L.c_ypos:g}", off=14, vertical=True)
    if L.c_yneg is not None:
        dimline(ox, y0, ox, oy + min(ys) * k, f"{L.c_yneg:g}", off=14, vertical=True)
    d.add(String(4, height - 10, "PLAN (mm) - anchor tension kN shown at anchors", fontName=font, fontSize=6.5, fillColor=GREY))
    d.add(String(4, 4, "solid = free edge; dashed = remote / not stated", fontName=font, fontSize=6, fillColor=GREY))
    return d


def utilisation_chart(rows: List[Tuple[str, Optional[float], str]], width: float = 460, font: str = "DejaVuSans",
                      bold: str = "DejaVuSans-Bold") -> Drawing:
    """Horizontal bars: label, utilisation, status."""
    rows = [r for r in rows if r[1] is not None and r[1] != float("inf")]
    bar_h, gap = 11, 5
    h = max(40, len(rows) * (bar_h + gap) + 28)
    d = Drawing(width, h)
    left, right = 190, width - 40
    scale_max = max(1.2, max([r[1] for r in rows] + [1.0]) * 1.08)

    def X(v):
        return left + (right - left) * min(v, scale_max) / scale_max

    y = h - 20
    for label, u, status in rows:
        col = GREEN if status == "PASS" else (RED if status == "FAIL" else AMBER)
        d.add(String(left - 6, y + 2, label[:44], fontName=font, fontSize=6.8, textAnchor="end", fillColor=colors.black))
        d.add(Rect(left, y, X(u) - left, bar_h, fillColor=col, strokeColor=None))
        d.add(String(X(u) + 3, y + 2.5, f"{u:.2f}", fontName=bold, fontSize=6.8, fillColor=col))
        y -= bar_h + gap
    d.add(Line(X(1.0), 8, X(1.0), h - 12, strokeColor=RED, strokeWidth=0.9, strokeDashArray=[3, 2]))
    d.add(String(X(1.0), h - 10, "1.0 limit", fontName=font, fontSize=6.5, fillColor=RED, textAnchor="middle"))
    d.add(Line(left, 8, right, 8, strokeColor=GREY, strokeWidth=0.5))
    return d
