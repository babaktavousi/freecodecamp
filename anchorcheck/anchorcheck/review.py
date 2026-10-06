"""Rule-based engineering review: findings, verdict and fix suggestions.

All findings are deterministic and traceable to a check result, a drawing omission, or a product-data gap.
(An LLM may *phrase* an executive summary afterwards, but it never creates findings or numbers.)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .design import DesignBasis, check_case
from .models import AdhesiveSpec, CaseResult, DesignCase
from .products.compare import SpecRow
from .standards.identify import StandardsDecision

SEV_ORDER = {"Critical": 0, "Major": 1, "Minor": 2, "Info": 3}


@dataclass
class Finding:
    severity: str          # Critical | Major | Minor | Info
    category: str          # Strength | Detailing | Product | Drawing | Standards | Scope | Assumption
    title: str
    detail: str
    case_id: str = ""
    action: str = ""


@dataclass
class FixSuggestion:
    description: str
    new_max_util: Optional[float]
    changed: Dict[str, float] = field(default_factory=dict)
    passes: bool = True


def verdict(results: List[CaseResult], specs: Dict[str, Optional[AdhesiveSpec]]) -> tuple:
    """Return (label, explanation)."""
    if not results:
        return "NO DESIGN CASES", "No anchor or dowel groups were identified or entered."
    failing = [r for r in results if r.status == "FAIL"]
    if failing:
        prov = [r.case.id for r in failing if specs.get(r.case.id) is None or
                any("DEFAULT installation factor" in n for cr in r.combos for ck in cr.checks for n in ck.notes)]
        if prov and len(prov) == len(failing):
            return ("NOT ADEQUATE - PROVISIONAL",
                    "Checks exceed capacity on conservative DEFAULT factors because the product data is missing "
                    f"({', '.join(prov)}). Obtain the product assessment data and re-run before drawing conclusions.")
        return "NOT ADEQUATE", "One or more checks exceed the design capacity (utilisation > 1.0)."
    if any(r.status == "INCOMPLETE" for r in results):
        return "INCOMPLETE", "Required data is missing, so adequacy cannot be confirmed."
    provisional = []
    for r in results:
        sp = specs.get(r.case.id)
        if sp is None:
            provisional.append(f"{r.case.id}: no product data")
        elif sp.is_demo:
            provisional.append(f"{r.case.id}: demonstration product data")
        elif not sp.verified:
            provisional.append(f"{r.case.id}: product data not yet confirmed by the engineer")
        if not r.case.concrete.stated_on_drawing:
            provisional.append(f"{r.case.id}: concrete strength assumed")
    if provisional:
        return "ADEQUATE - PROVISIONAL", "All checks pass, but: " + "; ".join(dict.fromkeys(provisional)) + "."
    return "ADEQUATE", "All checks pass on confirmed data."


def _max_embed(case: DesignCase) -> float:
    d = case.fastener_d
    hmax = 20 * d if case.kind != "rebar" else 40 * d
    if case.concrete.thickness:
        d0 = case.d0 or (d + 4 if d <= 12 else d + 6)
        hmax = min(hmax, case.concrete.thickness - (30.0 if d <= 12 else 2.0 * d0))
    return hmax


def suggest_fixes(case: DesignCase, spec: Optional[AdhesiveSpec], basis: Optional[DesignBasis] = None,
                  max_suggestions: int = 3) -> List[FixSuggestion]:
    """Sensitivity study: the smallest change(s) that make a failing case pass.

    Tries single changes (embedment, diameter, grade, more anchors, edge distance), then combinations; if nothing
    passes, returns the best improvements marked `passes=False`.
    """
    base = check_case(case, spec, basis)
    if base.status != "FAIL" or spec is None:
        return []
    d = case.fastener_d
    cands: List[tuple] = []          # (description, key, modifier)

    def deeper(c: DesignCase) -> None:
        c.h_ef = _max_embed(c) if _max_embed(c) > c.h_ef else c.h_ef

    hmax = _max_embed(case)
    if hmax > case.h_ef:
        h = case.h_ef
        while h < hmax:
            h = min(h + 10, hmax)
            c2 = copy.deepcopy(case)
            c2.h_ef = h
            if check_case(c2, spec, basis).status == "PASS":
                cands.append((f"Increase embedment from {case.h_ef:g} to {h:g} mm (member thickness permitting).", {"h_ef": h}, c2))
                break
        else:
            c2 = copy.deepcopy(case)
            c2.h_ef = hmax
            cands.append((f"Increase embedment to the maximum permitted by the member thickness / range ({hmax:g} mm).", {"h_ef": hmax}, c2))
    if case.kind != "rebar":
        sizes = sorted({e.d for e in spec.bond if e.fastener != "rebar"})
        for nd in [x for x in sizes if x > d][:2]:
            c2 = copy.deepcopy(case)
            c2.fastener_d, c2.d0 = nd, None
            cands.append((f"Use M{nd:g} rods (same embedment, grade {case.grade}).", {"d": nd}, c2))
        if case.grade in ("4.6", "5.8"):
            c2 = copy.deepcopy(case)
            c2.grade = "8.8"
            cands.append(("Upgrade the rod to Grade 8.8.", {"grade": 8.8}, c2))
        c2 = copy.deepcopy(case)
        if c2.layout.n_y == 1:
            c2.layout.n_y, c2.layout.s_y = 2, (c2.layout.s_x or 5 * d)
        else:
            c2.layout.n_x += 1
            c2.layout.s_x = c2.layout.s_x or 5 * d
        cands.append((f"Add anchors ({c2.layout.n_x} x {c2.layout.n_y} at the same spacing).", {"n": c2.layout.n}, c2))
    edges = [(a, getattr(case.layout, a)) for a in ("c_xneg", "c_xpos", "c_yneg", "c_ypos") if getattr(case.layout, a) is not None]
    if edges:
        c2 = copy.deepcopy(case)
        for a, v in edges:
            setattr(c2.layout, a, max(v * 2.0, 1.5 * case.h_ef))
        cands.append((f"Increase the edge distance(s) to about {max(v * 2.0 for _, v in edges):.0f} mm "
                      "(or provide a larger concrete section).", {"edge": 2.0}, c2))
    # evaluate singles
    evaluated = []
    for desc, ch, c2 in cands:
        r = check_case(c2, spec, basis)
        evaluated.append((desc, ch, c2, r))
    passing = [FixSuggestion(desc, r.max_utilisation, ch, True) for desc, ch, c2, r in evaluated if r.status == "PASS"]
    if passing:
        return passing[:max_suggestions]
    # combinations of two single changes
    combos: List[FixSuggestion] = []
    for i in range(len(evaluated)):
        for j in range(i + 1, len(evaluated)):
            di, chi, ci, _ = evaluated[i]
            dj, chj, cj, _ = evaluated[j]
            cm = copy.deepcopy(ci)
            for k, v in chj.items():             # apply j's change on top of i
                if k == "h_ef":
                    cm.h_ef = v
                elif k == "d":
                    cm.fastener_d, cm.d0 = v, None
                elif k == "grade":
                    cm.grade = "8.8"
                elif k == "n":
                    cm.layout = copy.deepcopy(cj.layout)
                elif k == "edge":
                    for a in ("c_xneg", "c_xpos", "c_yneg", "c_ypos"):
                        setattr(cm.layout, a, getattr(cj.layout, a))
            r = check_case(cm, spec, basis)
            if r.status == "PASS":
                combos.append(FixSuggestion(f"{di.rstrip('.')} AND {dj[0].lower() + dj[1:]}", r.max_utilisation, {**chi, **chj}, True))
    if combos:
        return sorted(combos, key=lambda f: f.new_max_util)[:max_suggestions]
    best = sorted(((r.max_utilisation or 9e9, desc, ch) for desc, ch, c2, r in evaluated if r.max_utilisation is not None))[:2]
    return [FixSuggestion(f"{desc} - reduces utilisation to {u:.2f} but is NOT sufficient on its own; combine with other changes "
                          "or revise the loads/connection concept.", u, ch, False) for u, desc, ch in best]


def build_findings(results: List[CaseResult], specs: Dict[str, Optional[AdhesiveSpec]],
                   comparisons: Dict[str, List[SpecRow]], standards: Optional[StandardsDecision],
                   rfis: List[str], missing_notes: List[str], warnings: List[str],
                   fixes: Optional[Dict[str, List[FixSuggestion]]] = None) -> List[Finding]:
    F: List[Finding] = []
    fixes = fixes or {}
    used: set = set()
    for r in results:
        cid = r.case.id
        if r.status == "FAIL":
            failing = [c for cr in r.combos for c in cr.checks if c.status == "FAIL"]
            names = "; ".join(dict.fromkeys(f"{c.title} (utilisation {c.utilisation:.2f})" for c in failing[:4] if c.utilisation is not None))
            fx = fixes.get(cid) or []
            act = "; ".join(f.description for f in fx) if fx else "Revise the connection (embedment, size, grade, layout) and re-check."
            if not failing:
                names = "; ".join(d.title for d in r.detailing if d.status == "FAIL")
            F.append(Finding("Critical", "Strength" if failing else "Detailing",
                             f"{cid}: design resistance exceeded", names, cid, act))
        elif r.status == "INCOMPLETE":
            used.update(r.warnings[:3])
            F.append(Finding("Major", "Product", f"{cid}: adequacy cannot be confirmed",
                             " ".join(w for w in r.warnings[:3] if not w.startswith("Status downgraded")) or "Required inputs are missing.", cid,
                             "Attach the adhesive TDS/ETA, run the web lookup, or enter the product values; provide the missing design inputs."))
        elif r.max_utilisation is not None and r.max_utilisation > 0.9:
            F.append(Finding("Info", "Strength", f"{cid}: little reserve capacity",
                             f"Governing utilisation {r.max_utilisation:.2f} ({r.governing}).", cid,
                             "Confirm load basis and installation quality; consider a larger margin."))
        for d in r.detailing:
            if d.status == "FAIL" and r.status != "FAIL":
                F.append(Finding("Critical", "Detailing", f"{cid}: {d.title} not satisfied", "; ".join(
                    f"{s.description}: {s.value} {s.unit}" for s in d.steps), cid, "Revise the detail."))
            if d.status == "WARN":
                F.append(Finding("Minor", "Detailing", f"{cid}: {d.title}", "; ".join(d.notes), cid, "Confirm."))
        for w in r.warnings:
            if w in used or w.startswith("Status downgraded"):
                continue
            sev = "Major" if any(k in w for k in ("Appendix F", "Seismic", "Fire", "not covered", "outside the scope", "not implemented")) else "Minor"
            title = w if len(w) <= 80 else w[:80].rsplit(" ", 1)[0] + "…"
            F.append(Finding(sev, "Scope" if sev == "Major" else "Assumption", f"{cid}: {title}", w, cid, ""))
        if r.assumptions:
            F.append(Finding("Info", "Assumption", f"{cid}: assumptions adopted in the calculation",
                             " • ".join(r.assumptions), cid, "Confirm or correct on the drawing / by RFI."))
        if r.alt_method is not None and r.alt_method.status in ("PASS", "FAIL"):
            a = r.alt_method
            why = ""
            if a.status == "FAIL":
                bad = [d.title for d in a.detailing if d.status == "FAIL"]
                bad += [c.title for cr in a.combos for c in cr.checks if c.status == "FAIL"]
                if (a.max_utilisation or 0) <= 1.0 and bad:
                    why = " Strength utilisation is within limits but the anchor-method range/detailing rule fails: " + \
                          "; ".join(dict.fromkeys(bad)) + " (e.g. embedment beyond the anchor-method range - the Appendix D method applies)."
                elif bad:
                    why = " Governing failure: " + "; ".join(dict.fromkeys(bad[:2])) + "."
            F.append(Finding("Info", "Strength", f"{cid}: cross-check by anchor method",
                             f"Treating the bar as an adhesive anchor (AS 5216 / EAD 330499 basis) gives max utilisation "
                             f"{a.max_utilisation:.2f} ({a.status}).{why}", cid, ""))
    for cid, rows in comparisons.items():
        for row in rows:
            if row.status == "GAP":
                F.append(Finding("Major", "Product", f"{cid}: product gap - {row.topic}",
                                 f"Required: {row.required}. Product documents: {row.provided}. {row.note}", cid,
                                 "Choose a product that is qualified for this use or obtain the missing data from the manufacturer."))
    if standards:
        for iss in standards.issues:
            F.append(Finding("Major" if "superseded" in iss else "Minor", "Standards", "Standards referencing", iss, "",
                             "Update the drawing notes."))
    for m in missing_notes:
        F.append(Finding("Minor", "Drawing", "Drawing note missing", m, "", "Add the note or confirm it is covered in the specification."))
    for x in rfis:
        F.append(Finding("Major" if any(k in x for k in ("not stated", "no design loads", "no adhesive", "SLS")) else "Minor",
                         "Drawing", "Information not on the drawing (RFI)", x, "", "Issue an RFI to the designer."))
    for w in warnings:
        F.append(Finding("Minor", "Drawing", "Extraction warning", w, "", ""))
    seen, uniq = set(), []
    for f in F:
        k = (f.severity, f.title, f.detail)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return sorted(uniq, key=lambda f: (SEV_ORDER[f.severity], f.case_id, f.category))
