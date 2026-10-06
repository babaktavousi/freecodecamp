"""Anchor-group geometry: coordinates, elastic force distribution and projected areas."""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from ..models import Layout

INF = float("inf")


def _c(v: Optional[float]) -> float:
    return INF if v is None else float(v)


def anchor_coords(layout: Layout) -> List[Tuple[float, float]]:
    """Anchor coordinates (mm) with origin at the group centroid."""
    nx, ny = max(1, layout.n_x), max(1, layout.n_y)
    xs = [(i - (nx - 1) / 2.0) * layout.s_x for i in range(nx)]
    ys = [(j - (ny - 1) / 2.0) * layout.s_y for j in range(ny)]
    return [(x, y) for y in ys for x in xs]


def anchor_tensions(layout: Layout, N: float, Mx: float, My: float) -> Tuple[List[float], List[str]]:
    """Elastic distribution of N (kN, +tension) and moments (kN.m) to anchors.

    Rigid plate, anchors only (plate bearing neglected) - conservative because
    the lever arm is measured from the anchor centroid.  Mx: tension on +y side.
    My: tension on +x side.  Returns per-anchor force (kN, +tension) and notes.
    """
    pts = anchor_coords(layout)
    n = len(pts)
    sx2 = sum(x * x for x, _ in pts)
    sy2 = sum(y * y for _, y in pts)
    notes: List[str] = []
    forces = []
    for x, y in pts:
        f = N / n
        if abs(Mx) > 1e-9:
            if sy2 > 0:
                f += Mx * 1000.0 * y / sy2
            else:
                notes.append("Moment Mx cannot be resisted by a single row of anchors - plate/bearing "
                             "action must be verified separately.")
        if abs(My) > 1e-9:
            if sx2 > 0:
                f += My * 1000.0 * x / sx2
            else:
                notes.append("Moment My cannot be resisted by a single column of anchors - plate/bearing "
                             "action must be verified separately.")
        forces.append(f)
    return forces, sorted(set(notes))


def _axis_extent(values: Sequence[float], c_neg: float, c_pos: float, s_cr: float, c_cr: float) -> float:
    """Projected length along one axis for a row of anchors at `values` (sorted unique)."""
    vals = sorted(set(round(v, 6) for v in values))
    length = min(c_neg, c_cr) + min(c_pos, c_cr)
    for a, b in zip(vals[:-1], vals[1:]):
        length += min(b - a, s_cr)
    return length


def projected_area_tension(layout: Layout, idx: Sequence[int], s_cr: float, c_cr: float) -> Tuple[float, float, dict]:
    """A_c,N (or A_p,N) for the subset `idx` of anchors loaded in tension.

    Returns (A, A0, info) with A0 = s_cr^2.  Edge distances are those of the
    subset (outer-layout edge distance + offset of the subset from the layout edge).
    """
    pts = anchor_coords(layout)
    sub = [pts[i] for i in idx]
    all_x = [p[0] for p in pts]
    all_y = [p[1] for p in pts]
    sx = [p[0] for p in sub]
    sy = [p[1] for p in sub]
    c_xn = _c(layout.c_xneg) + (min(sx) - min(all_x))
    c_xp = _c(layout.c_xpos) + (max(all_x) - max(sx))
    c_yn = _c(layout.c_yneg) + (min(sy) - min(all_y))
    c_yp = _c(layout.c_ypos) + (max(all_y) - max(sy))
    Lx = _axis_extent(sx, c_xn, c_xp, s_cr, c_cr)
    Ly = _axis_extent(sy, c_yn, c_yp, s_cr, c_cr)
    A = Lx * Ly
    A0 = s_cr ** 2
    info = {"c_xneg": c_xn, "c_xpos": c_xp, "c_yneg": c_yn, "c_ypos": c_yp, "Lx": Lx, "Ly": Ly}
    return A, A0, info


def min_edge(layout: Layout, idx: Optional[Sequence[int]] = None) -> Optional[float]:
    """Smallest finite edge distance of the group (or None if remote on all sides)."""
    if idx is None:
        vals = [layout.c_xneg, layout.c_xpos, layout.c_yneg, layout.c_ypos]
    else:
        vals = list(projected_info_edges(layout, idx).values())
    fin = [v for v in vals if v is not None and v != INF]
    return min(fin) if fin else None


def projected_info_edges(layout: Layout, idx: Sequence[int]) -> dict:
    pts = anchor_coords(layout)
    sub = [pts[i] for i in idx]
    all_x = [p[0] for p in pts]
    all_y = [p[1] for p in pts]
    sx = [p[0] for p in sub]
    sy = [p[1] for p in sub]
    out = {}
    out["c_xneg"] = None if layout.c_xneg is None else layout.c_xneg + (min(sx) - min(all_x))
    out["c_xpos"] = None if layout.c_xpos is None else layout.c_xpos + (max(all_x) - max(sx))
    out["c_yneg"] = None if layout.c_yneg is None else layout.c_yneg + (min(sy) - min(all_y))
    out["c_ypos"] = None if layout.c_ypos is None else layout.c_ypos + (max(all_y) - max(sy))
    return out


def eccentricity(layout: Layout, idx: Sequence[int], forces: Sequence[float]) -> Tuple[float, float]:
    """Eccentricity (mm) of the resultant tension force from the centroid of the loaded anchors."""
    pts = anchor_coords(layout)
    tot = sum(forces[i] for i in idx)
    if tot <= 0:
        return 0.0, 0.0
    cx = sum(pts[i][0] for i in idx) / len(idx)
    cy = sum(pts[i][1] for i in idx) / len(idx)
    rx = sum(forces[i] * pts[i][0] for i in idx) / tot
    ry = sum(forces[i] * pts[i][1] for i in idx) / tot
    return abs(rx - cx), abs(ry - cy)


def effective_hef(layout: Layout, h_ef: float, c_cr_n: float) -> Tuple[float, bool]:
    """EN 1992-4 / ETAG rule for anchors influenced by three or more edges.

    If >= 3 in-plane edges are closer than c_cr,N, the calculation is made with
    h'_ef = max(c_max/1.5, s_max/3) (never larger than h_ef).
    """
    cs = [layout.c_xneg, layout.c_xpos, layout.c_yneg, layout.c_ypos]
    close = [c for c in cs if c is not None and c < c_cr_n]
    if len(close) < 3:
        return h_ef, False
    s_max = max((layout.n_x - 1) * layout.s_x, (layout.n_y - 1) * layout.s_y)
    c_max = max(close)
    h_new = max(c_max / 1.5, s_max / 3.0)
    return min(h_ef, h_new), h_new < h_ef


# ------------------------------------------------------------------ shear
EDGES = ("xpos", "xneg", "ypos", "yneg")


def edge_geometry(layout: Layout, edge: str) -> Optional[dict]:
    """Geometry of the front row of anchors relative to one free edge.

    Returns dict(c1, c2a, c2b, positions) where positions are the coordinates of
    front-row anchors along the edge axis, or None when the edge is remote.
    """
    if edge == "xpos":
        c1, side_a, side_b = layout.c_xpos, layout.c_yneg, layout.c_ypos
        n_along, s_along = layout.n_y, layout.s_y
    elif edge == "xneg":
        c1, side_a, side_b = layout.c_xneg, layout.c_yneg, layout.c_ypos
        n_along, s_along = layout.n_y, layout.s_y
    elif edge == "ypos":
        c1, side_a, side_b = layout.c_ypos, layout.c_xneg, layout.c_xpos
        n_along, s_along = layout.n_x, layout.s_x
    else:
        c1, side_a, side_b = layout.c_yneg, layout.c_xneg, layout.c_xpos
        n_along, s_along = layout.n_x, layout.s_x
    if c1 is None:
        return None
    n_along = max(1, n_along)
    positions = [(i - (n_along - 1) / 2.0) * s_along for i in range(n_along)]
    return {"c1": float(c1), "c2a": _c(side_a), "c2b": _c(side_b), "positions": positions}


def projected_area_shear(c1: float, c2a: float, c2b: float, positions: Sequence[float],
                         h: Optional[float]) -> Tuple[float, float, float]:
    """A_c,V and A0_c,V for a row of anchors near a free edge (EN 1992-4 / ETAG).

    Returns (A_cV, A0_cV, b_eff).
    """
    cap = 1.5 * c1
    b = min(c2a, cap) + min(c2b, cap)
    pos = sorted(positions)
    for a, bb in zip(pos[:-1], pos[1:]):
        b += min(bb - a, 3.0 * c1)
    h_eff = min(h, cap) if h is not None else cap
    return b * h_eff, 4.5 * c1 * c1, b


def psi_alpha_v(alpha_deg: float) -> float:
    """Shear-direction factor: 1 (<=55 deg), 1/(cos a + 0.5 sin a) (55-90), 2 (>90)."""
    a = abs(alpha_deg)
    if a <= 55.0:
        return 1.0
    if a <= 90.0:
        r = math.radians(a)
        return 1.0 / (math.cos(r) + 0.5 * math.sin(r))
    return 2.0
