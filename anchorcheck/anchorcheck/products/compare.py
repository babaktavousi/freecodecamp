"""Compare what the design NEEDS from the adhesive with what the product documents PROVIDE,
and screen alternative adhesives by re-running the design with each product's data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..design import DesignBasis, check_case
from ..models import AdhesiveSpec, DesignCase


@dataclass
class SpecRow:
    topic: str
    required: str
    provided: str
    status: str            # OK | GAP | UNKNOWN | INFO
    source: str = ""
    note: str = ""


def _entries(spec: AdhesiveSpec, case: DesignCase):
    kind = "rebar" if case.kind == "rebar" else "rod"
    es = [e for e in spec.bond if abs(e.d - case.fastener_d) < 0.51 and e.fastener == kind]
    return es or [e for e in spec.bond if abs(e.d - case.fastener_d) < 0.51]


def compare_spec(case: DesignCase, spec: Optional[AdhesiveSpec]) -> List[SpecRow]:
    rows: List[SpecRow] = []
    if spec is None:
        return [SpecRow("Product specification", "TDS / assessment for the nominated adhesive", "none found",
                        "GAP", note="Attach the TDS/ETA or run the web lookup; design cannot be completed without it.")]
    src = lambda k: spec.provenance[k].short() if k in spec.provenance else ""
    appr = ", ".join(spec.eta + spec.eads) or "not found"
    need = "EAD 330087 (post-installed rebar) and/or EAD 330499" if case.kind == "rebar" else \
        "Assessment / ETA to EAD 330499 (bonded anchors) - AS 5216:2021 pre-qualification"
    ok = ("EAD 330499" in spec.eads) or (case.kind == "rebar" and "EAD 330087" in spec.eads)
    rows.append(SpecRow("Pre-qualification (AS 5216)", need, appr, "OK" if ok else ("UNKNOWN" if appr == "not found" else "GAP"),
                        src("approvals"), "AS 5216:2021 requires the product to be pre-qualified; installers per Appendix B."))
    es = _entries(spec, case)
    kindname = "bar" if case.kind == "rebar" else "rod"
    rows.append(SpecRow(f"Size coverage (d = {case.fastener_d:g} mm {kindname})", "Bond data for this diameter",
                        "yes" if es else "no entry", "OK" if es else "GAP",
                        es[0].prov.short() if es else ""))
    if case.concrete.cracked:
        has = any(e.tau_cr is not None for e in es)
        rows.append(SpecRow("Cracked concrete", "Cracked-concrete bond resistance tau_Rk,cr",
                            "present" if has else "absent", "OK" if has else "GAP",
                            es[0].prov.short() if es else "", "Design assumes cracked concrete (conservative)."))
    cond = case.hole_condition
    has_c = any(e.condition == cond for e in es)
    rows.append(SpecRow(f"Hole condition ({cond.replace('_', '/')})", f"Data for {cond.replace('_', '/')} holes",
                        "present" if has_c else "absent", "OK" if has_c else "GAP"))
    want_d = "diamond" if case.drilling.startswith("diamond") else "hammer"
    has_d = any(e.drilling.startswith(want_d) for e in es if e.condition != "flooded") or (cond == "flooded" and has_c)
    rows.append(SpecRow(f"Drilling method ({case.drilling})", "Qualified for the specified method",
                        "present" if has_d else "absent", "OK" if has_d else "GAP"))
    hmin = min([e.hef_min for e in es if e.hef_min] or [None], default=None) if es else None
    hmax = max([e.hef_max for e in es if e.hef_max] or [None], default=None) if es else None
    if hmin and hmax:
        ok = hmin <= case.h_ef <= hmax
        rows.append(SpecRow("Embedment range", f"hef = {case.h_ef:g} mm", f"{hmin:g} - {hmax:g} mm",
                            "OK" if ok else "GAP", es[0].prov.short()))
    else:
        rows.append(SpecRow("Embedment range", f"hef = {case.h_ef:g} mm", "not extracted", "UNKNOWN"))
    if case.kind == "rebar":
        rows.append(SpecRow("Post-installed rebar qualification", "EAD 330087 / ETA-Rebar with f_bd",
                            "yes" if spec.rebar_qualified else "not confirmed",
                            "OK" if spec.rebar_qualified else "GAP",
                            src("f_bd"), "Rebar-as-anchor design (EAD 330499) is an alternative if bond data exist."))
    if case.overhead:
        rows.append(SpecRow("Overhead installation", "Qualified for upward installation",
                            {True: "yes", None: "not stated"}.get(spec.overhead_ok, "no"),
                            "OK" if spec.overhead_ok else "UNKNOWN"))
    if case.seismic:
        sc = ", ".join(spec.seismic) or "none"
        rows.append(SpecRow("Seismic (AS 5216 App. F)", "C1 or C2 per crack width at the anchor", sc,
                            "OK" if spec.seismic else "GAP", note="Static check only in this tool; seismic design is separate."))
    if case.fire:
        rows.append(SpecRow("Fire resistance", "Manufacturer fire data for the required FRL",
                            "mentioned" if spec.fire_data else "not found", "UNKNOWN" if spec.fire_data else "GAP"))
    if any(c.sustained_fraction > 0 for c in case.loads):
        rows.append(SpecRow("Sustained load", "psi_sus0 factor", "present" if spec.psi_sus0 else "absent",
                            "OK" if spec.psi_sus0 else "GAP", src("psi_sus0")))
    rows.append(SpecRow("Installation safety factor", "gamma_inst from the assessment",
                        "present" if (spec.gamma_inst or spec.gamma_inst_map) else "absent",
                        "OK" if (spec.gamma_inst or spec.gamma_inst_map) else "GAP", src("gamma_inst_hammer"),
                        "" if (spec.gamma_inst or spec.gamma_inst_map) else "Conservative default 1.4 used by the engine."))
    rows.append(SpecRow("Concrete-strength factor", f"psi_c for f'c = {case.concrete.fc:g} MPa",
                        "present" if spec.psi_c else "absent", "OK" if spec.psi_c else "INFO", src("psi_c"),
                        "" if spec.psi_c else "No enhancement taken above C20/25."))
    if spec.is_demo:
        rows.append(SpecRow("Data status", "Verified manufacturer data", "DEMONSTRATION VALUES - NOT A REAL PRODUCT", "GAP"))
    else:
        rows.append(SpecRow("Data status", "Values confirmed against the source by the engineer",
                            "confirmed" if spec.verified else "machine-read, NOT yet confirmed",
                            "OK" if spec.verified else "UNKNOWN",
                            note="" if spec.verified else "Confirm the extracted values against the source document before issuing."))
    return rows


@dataclass
class AlternativeRow:
    name: str
    status: str
    max_util: Optional[float]
    governing: str
    data_gaps: int
    verified: bool
    notes: str = ""


def screen_alternatives(case: DesignCase, specs: Dict[str, AdhesiveSpec],
                        basis: Optional[DesignBasis] = None) -> List[AlternativeRow]:
    out: List[AlternativeRow] = []
    for name, spec in specs.items():
        try:
            res = check_case(case, spec, basis)
        except Exception as e:  # pragma: no cover
            out.append(AlternativeRow(name, "ERROR", None, "", 0, False, str(e)))
            continue
        gaps = sum(1 for r in compare_spec(case, spec) if r.status == "GAP")
        status, util, gov = res.status, res.max_utilisation, res.governing
        if not _entries(spec, case) and case.kind != "rebar" or (case.kind == "rebar" and spec.rebar_qualified is not True
                                                                   and not _entries(spec, case)):
            status, util, gov = "INCOMPLETE", None, "no bond data for this size - cannot be ranked"
        out.append(AlternativeRow(name, status, util, gov, gaps, spec.verified, "; ".join(res.warnings[:2])))
    order = {"PASS": 0, "FAIL": 1, "INCOMPLETE": 2, "ERROR": 3}
    return sorted(out, key=lambda r: (order.get(r.status, 9), r.max_util if r.max_util is not None else 9e9))
