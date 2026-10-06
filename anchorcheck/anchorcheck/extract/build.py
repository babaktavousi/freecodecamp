"""Merge regex + vision readings and turn candidates into DesignCase objects for review.

Conflicts between two readings of the same field are NOT silently resolved: the value whose text appears
in the PDF is preferred, and the disagreement is reported as a warning for the engineer.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..design.steel import default_d0, normalise_grade
from ..models import Concrete, DesignCase, Fixture, Layout, LoadCombo, Provenance
from ..products.registry import find_product_by_name, match_products
from .common import Candidate, DrawingExtraction, Evidence


def merge_extractions(primary: DrawingExtraction, secondary: DrawingExtraction) -> DrawingExtraction:
    """`primary` = rule-based (verifiable against text), `secondary` = vision."""
    out = primary
    out.methods = list(dict.fromkeys(primary.methods + secondary.methods))
    out.warnings += secondary.warnings
    out.general_notes = list(dict.fromkeys(out.general_notes + secondary.general_notes))
    for sc in secondary.candidates:
        mc = next((c for c in out.candidates if c.kind == sc.kind and c.page == sc.page and c.get("size") == sc.get("size")), None)
        if mc is None:
            out.candidates.append(sc)
            continue
        for k, e in sc.fields.items():
            if k not in mc.fields:
                mc.fields[k] = e
            else:
                cur = mc.fields[k]
                if cur.value != e.value and k not in ("adhesive",):
                    out.warnings.append(
                        f"{mc.label}: '{k}' differs - notes say {cur.value!r} ({cur.method}), "
                        f"image reading says {e.value!r} ({e.method}). Verify against the drawing.")
        known = {(l.kind, l.value) for l in mc.loads}
        mc.loads += [l for l in sc.loads if (l.kind, l.value) not in known]
        mc.shape = mc.shape or sc.shape
        mc.notes += [n for n in sc.notes if n not in mc.notes]
    return out


def _prov(e: Optional[Evidence], default_note: str = "", doc: str = "drawing") -> Provenance:
    if e is None:
        return Provenance(source="assumed", note=default_note, confidence=0.2)
    return Provenance(source="drawing" if e.method in ("regex", "vision") else e.method, document=doc,
                      page=e.page, quote=e.quote, confidence=e.confidence, note=f"{e.method}")


def candidate_to_case(c: Candidate, idx: int, ex: DrawingExtraction, defaults: Optional[dict] = None) -> Tuple[DesignCase, List[str]]:
    """Return (case, rfis) - RFIs are things the drawing does not say that the engineer must resolve."""
    dflt = {"fc": 32.0, "cracked": True, "grade_rod": "4.6", "hole_condition": "dry_wet", "drilling": "hammer"}
    dflt.update(defaults or {})
    rfi: List[str] = []
    case = DesignCase(id=f"{'R' if c.kind == 'rebar' else 'A'}{idx}", title=c.label, kind=c.kind, page=c.page,
                      source=c.source)
    d = float(c.get("size", 16))
    case.fastener_d = d

    def rec(field: str, e: Optional[Evidence], note: str = ""):
        case.prov[field] = _prov(e, note, c.source or "drawing")

    # ---- steel grade
    ge = c.fields.get("grade")
    if c.kind == "rebar":
        g = normalise_grade(str(ge.value)) if ge else "500N"
        case.grade = g if g.startswith(("500", "250")) else "500N"
        rec("grade", ge, "assumed 500N")
        if ge is None or ge.confidence <= 0.5:
            rfi.append(f"{c.label}: reinforcement grade not stated - D500N assumed (AS/NZS 4671).")
    else:
        if ge:
            case.grade = normalise_grade(str(ge.value))
        else:
            case.grade = dflt["grade_rod"]
            rfi.append(f"{c.label}: steel grade of the threaded rod is not stated - Grade {case.grade} assumed (lowest "
                       "common property class, conservative). Specify the grade on the drawing.")
        rec("grade", ge, f"assumed grade {case.grade}")
    # ---- embedment
    he = c.fields.get("h_ef")
    if he:
        case.h_ef = float(he.value)
    else:
        case.h_ef = max(60.0, 8.0 * d)
        rfi.append(f"{c.label}: embedment depth not stated - {case.h_ef:g} mm (8d) assumed. The drawing must state the "
                   "effective embedment / hole depth.")
    rec("h_ef", he, "assumed 8d")
    d0 = c.fields.get("d0")
    case.d0 = float(d0.value) if d0 else default_d0(d, case.grade)
    rec("d0", d0, "typical value for the size")
    # ---- concrete
    fe = c.fields.get("fc")
    cr_flag = ex.flags.get("uncracked") and not ex.flags.get("cracked")
    case.concrete = Concrete(fc=float(fe.value) if fe else dflt["fc"], cracked=False if cr_flag else dflt["cracked"],
                             thickness=float(c.get("thickness")) if c.get("thickness") else None,
                             stated_on_drawing=fe is not None)
    rec("fc", fe, f"assumed f'c = {dflt['fc']:g} MPa")
    if fe is None:
        rfi.append(f"{c.label}: concrete strength is not stated - f'c = {dflt['fc']:g} MPa assumed.")
    if case.concrete.thickness is None:
        rfi.append(f"{c.label}: member thickness not stated - needed for minimum-thickness and edge/splitting checks.")
    if not ex.flags.get("cracked") and not ex.flags.get("uncracked"):
        rfi.append(f"{c.label}: cracked/uncracked state of the concrete is not stated - CRACKED assumed (conservative).")
    # ---- layout
    nx = int(c.get("n_x", 0) or 0)
    ny = int(c.get("n_y", 0) or 0)
    cnt = int(c.get("count", 0) or 0)
    s = c.get("s_anchor") or c.get("spacing")
    if c.kind == "rebar":
        case.layout = Layout(n_x=1, n_y=1, s_x=float(s or 0), s_y=0)
        bar_s = float(s) if s else None
        case.rebar_clear_spacing = (bar_s - d) if bar_s else None
    else:
        if not (nx and ny):
            if cnt in (1, 2):
                nx, ny = cnt, 1
            elif cnt == 4:
                nx, ny = 2, 2
            elif cnt == 6:
                nx, ny = 3, 2
            elif cnt:
                nx, ny = cnt, 1
                rfi.append(f"{c.label}: layout of {cnt} anchors not given - arranged in a single row; edit the layout.")
            else:
                nx, ny = 1, 1
                rfi.append(f"{c.label}: number/arrangement of anchors not stated - single anchor assumed.")
        sx = float(s) if s else 0.0
        if (nx > 1 or ny > 1) and not s:
            sx = 5.0 * d
            rfi.append(f"{c.label}: anchor spacing not stated - 5d assumed.")
        case.layout = Layout(n_x=nx, n_y=ny, s_x=sx if nx > 1 else 0.0, s_y=sx if ny > 1 else 0.0)
    ed = c.fields.get("edge")
    if ed:
        # drawing edge distances are taken as the minimum on the nearest edge (x+ direction); other edges remote
        case.layout.c_xpos = float(ed.value)
        rec("edge", ed)
    elif not c.kind == "rebar":
        rfi.append(f"{c.label}: edge distance not stated - anchors assumed remote from edges (verify).")
    if c.kind == "rebar" and ed:
        case.rebar_clear_cover = float(ed.value) - d / 2.0
    # ---- adhesive
    ae = c.fields.get("adhesive")
    if ae:
        pm = find_product_by_name(str(ae.value))
        case.adhesive_name = pm.name if pm else str(ae.value)
        rec("adhesive", ae)
    else:
        rfi.append(f"{c.label}: no adhesive product named - the design cannot be completed without a pre-qualified product.")
    dr, hc = c.fields.get("drilling"), c.fields.get("hole_condition")
    case.drilling = str(dr.value) if dr else dflt["drilling"]
    case.hole_condition = str(hc.value) if hc else dflt["hole_condition"]
    case.overhead = "overhead" in ex.flags
    case.seismic = "seismic" in ex.flags
    case.fire = "fire" in ex.flags
    case.notes = list(c.notes)
    if c.shape:
        case.notes.append(f"Shape recognised from image: {c.shape.replace('_', ' ')}")
    # ---- loads
    case.loads = loads_from_candidate(c, case, rfi)
    return case, rfi


def loads_from_candidate(c: Candidate, case: DesignCase, rfi: List[str]) -> List[LoadCombo]:
    if not c.loads:
        rfi.append(f"{c.label}: no design loads found on the drawing - strength checks cannot be performed until "
                   "ULS design actions are provided.")
        return []
    N = V = 0.0
    basis = "group"
    ls = "unknown"
    q = ""
    for l in c.loads:
        val = l.value
        if l.basis == "per_metre":
            spacing = c.get("spacing") or case.layout.s_x
            if not spacing:
                rfi.append(f"{c.label}: load given per metre but bar spacing unknown - cannot convert.")
                continue
            val = l.value * float(spacing) / 1000.0           # kN/m * m = kN per bar
            basis = "per_anchor"
        elif l.basis == "per_anchor":
            basis = "per_anchor"
        if l.kind == "tension":
            N = max(N, val)
        else:
            V = max(V, val)
        ls = l.limit_state if l.limit_state != "unknown" else ls
        q = l.quote
    if V > 0:
        rfi.append(f"{c.label}: direction of the shear force is not stated - assumed to act towards the nearest "
                   "free edge (conservative). State the shear direction and whether it can reverse.")
    if ls == "SLS":
        rfi.append(f"{c.label}: loads are labelled SLS/working loads - ULS factored actions (AS/NZS 1170.0) are required.")
    if ls == "unknown":
        rfi.append(f"{c.label}: it is not stated whether the loads are ULS factored - treated as ULS.")
    sus = 0.0
    return [LoadCombo(name="Drawing", N=N, Vx=V, basis=basis, limit_state="ULS" if ls in ("unknown", "ULS") else ls,
                      sustained_fraction=sus, prov=Provenance(source="drawing", page=c.loads[0].page, quote=q, confidence=0.6))]


def build_cases(ex: DrawingExtraction, defaults: Optional[dict] = None) -> Tuple[List[DesignCase], List[str]]:
    cases, rfis = [], []
    ac = rc = 0
    for c in ex.candidates:
        if c.kind == "rebar":
            rc += 1
            case, r = candidate_to_case(c, rc, ex, defaults)
        else:
            ac += 1
            case, r = candidate_to_case(c, ac, ex, defaults)
        cases.append(case)
        rfis += r
    return cases, rfis
