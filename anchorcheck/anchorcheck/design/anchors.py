"""Bonded (chemical) anchor design - EN 1992-4 / EAD 330499 methodology as adopted
by AS 5216:2021, written to produce an auditable step-by-step record.

Every function returns CheckResult objects whose `steps` list the formula,
substituted inputs and results so the reviewer can verify the arithmetic.

Limitations (reported by the engine, never silently ignored): static loading,
solid concrete, rigid base plate, no torsion, no seismic / fire / fatigue
design, splitting and blow-out not verified numerically.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from ..models import (AdhesiveSpec, BondEntry, CaseResult, CheckResult, ComboResult,
                      DesignCase, LoadCombo, Step)
from . import geometry as geo
from .basis import DesignBasis
from .steel import SteelProps, default_d0, fastener_props

PASS, FAIL, NA, INCOMPLETE, WARN = "PASS", "FAIL", "NA", "INCOMPLETE", "WARN"
TOL = 1e-9


# ----------------------------------------------------------------------------
# elementary resistances (pure functions - unit-tested against published tables)
# ----------------------------------------------------------------------------

def n0_rk_c(fc: float, h_ef: float, cracked: bool, basis: DesignBasis,
            k_override: Optional[float] = None) -> float:
    """Characteristic concrete-cone resistance of a single anchor, N."""
    k = k_override if k_override else (basis.k_cr if cracked else basis.k_ucr)
    return k * math.sqrt(fc) * h_ef ** 1.5


def v0_rk_c(fc: float, d: float, h_ef: float, c1: float, cracked: bool, basis: DesignBasis) -> float:
    """Characteristic edge-shear resistance of a single anchor (thick member), N."""
    k9 = basis.k9_cr if cracked else basis.k9_ucr
    l_f = min(h_ef, 8.0 * d)
    alpha = 0.1 * math.sqrt(l_f / c1)
    beta = 0.1 * (d / c1) ** 0.2
    return k9 * d ** alpha * l_f ** beta * math.sqrt(fc) * c1 ** 1.5


def s_cr_np(d: float, tau_ucr: float, h_ef: float) -> float:
    return min(7.3 * d * math.sqrt(max(tau_ucr, 0.0)), 3.0 * h_ef)


def psi_ec(e: float, s_cr: float) -> float:
    return 1.0 / (1.0 + 2.0 * e / s_cr)


def psi_s(c: Optional[float], c_cr: float) -> float:
    if c is None or c == float("inf"):
        return 1.0
    return min(1.0, 0.7 + 0.3 * c / c_cr)


def interp(table: Dict[float, float], x: float) -> Optional[float]:
    if not table:
        return None
    keys = sorted(table)
    if x <= keys[0]:
        return table[keys[0]]
    if x >= keys[-1]:
        return table[keys[-1]]
    for a, b in zip(keys[:-1], keys[1:]):
        if a <= x <= b:
            t = (x - a) / (b - a)
            return table[a] + t * (table[b] - table[a])
    return None


# ----------------------------------------------------------------------------

def _fin(res: CheckResult, E: Optional[float], R_k: Optional[float], phi: Optional[float]) -> CheckResult:
    res.R_k, res.phi, res.E_d = R_k, phi, E
    if R_k is None or phi is None:
        res.status = INCOMPLETE
        return res
    res.R_d = R_k * phi
    res.E_d = E
    if E is None or E <= TOL:
        res.status, res.utilisation = NA, 0.0
        return res
    res.utilisation = E / res.R_d if res.R_d > 0 else float("inf")
    res.status = PASS if res.utilisation <= 1.0 + 1e-9 else FAIL
    return res


class AnchorEngine:
    """Run all AS 5216-type verifications for one bonded-anchor design case."""

    def __init__(self, case: DesignCase, spec: Optional[AdhesiveSpec], basis: Optional[DesignBasis] = None):
        self.case = case
        self.spec = spec
        self.b = basis or DesignBasis()
        self.warnings: List[str] = []
        self.assumptions: List[str] = []
        self.steel: SteelProps = fastener_props(case.fastener_d, case.grade)
        self.d = case.fastener_d
        self.h_ef = case.h_ef
        self.fc = case.concrete.fc
        self.cracked = case.concrete.cracked
        self.h = case.concrete.thickness
        self.entry: Optional[BondEntry] = None
        self.tau: Optional[float] = None        # tau_Rk used (cracked/uncracked per case) incl. psi_c
        self.tau_raw: Optional[float] = None
        self.tau_ucr: Optional[float] = None
        self.psi_c: float = 1.0
        self.gamma_inst: Optional[float] = None
        self.phi_c: float = self.b.phi_concrete(None)
        self.d0 = case.d0 or default_d0(self.d, case.grade)
        self._prepare()

    # ------------------------------------------------------------------ setup
    def _prepare(self) -> None:
        c, b, spec = self.case, self.b, self.spec
        if c.seismic:
            self.warnings.append("Seismic actions flagged: AS 5216:2021 Appendix F (seismic performance "
                                 "categories C1/C2, crack width) is NOT implemented - static design only.")
        if c.fire:
            self.warnings.append("Fire design flagged: not implemented - refer manufacturer fire data.")
        if c.fatigue:
            self.warnings.append("Fatigue loading flagged: not covered by this tool.")
        if c.concrete.fc < 20 or c.concrete.fc > 65:
            self.warnings.append(f"Concrete f'c = {c.concrete.fc:g} MPa is outside the 20-65 MPa range "
                                 "typically covered by AS 5216 / product assessments.")
        elif c.concrete.fc > 50:
            self.warnings.append(f"Concrete f'c = {c.concrete.fc:g} MPa exceeds C50/60 - check the product "
                                 "assessment covers it.")
        if not c.concrete.stated_on_drawing:
            self.assumptions.append(f"Concrete strength f'c = {c.concrete.fc:g} MPa is an assumption "
                                    "(not stated on the drawing).")
        self.assumptions.append("Concrete treated as CRACKED (conservative)." if self.cracked else
                                "Concrete treated as UNCRACKED - drawing/engineer must confirm that the "
                                "member is uncracked at the anchor location in service.")
        self.assumptions.append("Static ULS loads, rigid base plate, anchors only resist tension "
                                "(plate bearing ignored), shear shared equally by anchors with standard "
                                "clearance holes." if c.shear_to_all_anchors else
                                "Static ULS loads; oversize/slotted holes assumed - shear carried by one "
                                "row of anchors only.")
        if spec is None:
            return
        key = "flooded" if c.hole_condition == "flooded" else ("diamond" if c.drilling.startswith("diamond") else "hammer")
        self.gamma_inst = spec.gamma_inst_map.get(key, spec.gamma_inst)
        if self.gamma_inst is None:
            self.warnings.append(f"Installation safety factor gamma_inst not available for "
                                 f"{spec.name} ({key} drilling): conservative default "
                                 f"{b.gamma_inst_default} used.")
        elif spec.gamma_inst_map.get(key) is None and key != "hammer":
            self.warnings.append(f"gamma_inst for '{key}' installation not found - hammer-drilling value used; "
                                 "confirm against the assessment.")
        self.phi_c = b.phi_concrete(self.gamma_inst)
        self.entry = self._select_bond_entry()
        if self.entry is not None:
            self._set_tau()

    def _select_bond_entry(self) -> Optional[BondEntry]:
        spec, c = self.spec, self.case
        want_kind = "rebar" if c.kind == "rebar" else "rod"
        same_d = [e for e in spec.bond if abs(e.d - c.fastener_d) < 0.51]
        cands = [e for e in same_d if e.fastener == want_kind]
        if not cands and same_d:
            cands = same_d
            self.warnings.append(f"No {want_kind}-specific bond table in {spec.name}: the "
                                 f"{'/'.join(sorted({e.fastener for e in same_d}))} values were used - confirm they apply.")
        if not cands:
            self.warnings.append(f"No bond-resistance data for diameter {c.fastener_d:g} mm in the "
                                 f"{spec.name} specification.")
            return None
        cond = [e for e in cands if e.condition == c.hole_condition]
        if not cond:
            self.warnings.append(f"No bond data for hole condition '{c.hole_condition}' at "
                                 f"d = {c.fastener_d:g} mm.")
            return None
        want_drill = "diamond" if c.drilling.startswith("diamond") else "hammer"
        dr = [e for e in cond if e.drilling.startswith(want_drill)] if c.hole_condition != "flooded" else cond
        if not dr:
            self.warnings.append(f"No bond data for {want_drill} drilling at d = {c.fastener_d:g} mm - "
                                 "the product may not be qualified for the specified drilling method.")
            return None
        cond = dr
        life = [e for e in cond if e.service_life == 50]
        if life:
            cond = life
        elif any(e.service_life > 50 for e in cond):
            self.assumptions.append("Only 100-year service-life bond values found (conservative for a 50-year design).")
        if c.temp_range != "default":
            tr = [e for e in cond if e.temp_range == c.temp_range or e.temp_key == c.temp_range]
            if tr:
                cond = tr
        if len({e.temp_range for e in cond}) > 1 and c.temp_range == "default":
            key = (lambda e: (e.tau_cr if self.cracked and e.tau_cr is not None
                              else (e.tau_ucr if e.tau_ucr is not None else 1e9)))
            cond.sort(key=key)
            self.assumptions.append(f"Temperature range not stated: lowest bond resistance "
                                    f"(range '{cond[0].temp_range}') adopted.")
        return cond[0]

    def _set_tau(self) -> None:
        e, spec = self.entry, self.spec
        tau = e.tau_cr if self.cracked else e.tau_ucr
        if tau is None and not self.cracked and e.tau_cr is not None:
            tau = e.tau_cr
            self.warnings.append("Uncracked bond resistance not available - cracked value used (conservative).")
        self.tau_ucr = e.tau_ucr if e.tau_ucr is not None else e.tau_cr
        if tau is None and self.cracked:
            self.warnings.append(f"{spec.name} has no cracked-concrete bond resistance for this size - "
                                 "the product may not be qualified for cracked concrete.")
        self.tau_raw = tau
        psi_c = interp(spec.psi_c, self.fc) if spec.psi_c else None
        if psi_c is None:
            psi_c = 1.0
            if self.fc > 20.5:
                self.assumptions.append("No concrete-strength factor psi_c supplied by the product "
                                        "documents: psi_c = 1.0 (no enhancement for f'c > 20 MPa).")
        self.psi_c = min(psi_c, 1.0) if self.fc < 20 else psi_c
        self.tau = tau * self.psi_c if tau is not None else None

    def psi_sus0(self) -> Tuple[float, bool]:
        """(value, from_product). Lowest product value if the temperature range is unknown."""
        spec, e = self.spec, self.entry
        if spec is not None:
            if e is not None and e.temp_key and e.temp_key in spec.psi_sus0_map:
                return spec.psi_sus0_map[e.temp_key], True
            if spec.psi_sus0 is not None:
                return spec.psi_sus0, True
        return 0.6, False

    @staticmethod
    def c_cr_sp(h: Optional[float], h_ef: float) -> float:
        """EN 1992-4 / ETA splitting edge distance (confirmed in Hilti ETA-16/0143 Table C1)."""
        if h is None:
            return 2.26 * h_ef
        r = h / h_ef
        if r >= 2.0:
            return 1.0 * h_ef
        if r > 1.3:
            return 4.6 * h_ef - 1.8 * h
        return 2.26 * h_ef

    # ------------------------------------------------------------- tension --
    def _tension_pullout(self, idx: Sequence[int], forces: Sequence[float], title: str,
                         key: str, ecc: bool = True, sustained_note: Optional[str] = None) -> Tuple[CheckResult, Optional[float]]:
        """Combined pull-out and concrete failure (bond) for the anchors in `idx`."""
        c, b = self.case, self.b
        res = CheckResult(key=key, title=title, mode="tension",
                          clause="EN 1992-4 7.2.1 / AS 5216:2021 - bonded anchors, combined pull-out & concrete failure")
        if self.tau is None:
            res.status = INCOMPLETE
            res.notes.append("Bond resistance tau_Rk not available - supply the product TDS / assessment.")
            return res, None
        tau_ucr = self.tau_ucr if self.tau_ucr is not None else self.tau_raw
        n = len(idx)
        s_cr = s_cr_np(self.d, tau_ucr, self.h_ef)
        c_cr = s_cr / 2.0
        A, A0, info = geo.projected_area_tension(c.layout, idx, s_cr, c_cr)
        n0p = math.pi * self.d * self.h_ef * self.tau          # N
        # group factor
        k_g = b.k_g_cr if self.cracked else b.k_g_ucr
        if n > 1:
            sqn = math.sqrt(n)
            psi0 = sqn - (sqn - 1.0) * (self.d * self.tau / (k_g * math.sqrt(self.h_ef * self.fc))) ** 1.5
            psi0 = max(psi0, 1.0)
            pts = geo.anchor_coords(c.layout)
            sx = sorted({round(pts[i][0], 6) for i in idx})
            sy = sorted({round(pts[i][1], 6) for i in idx})
            spacings = [bb - a for a, bb in zip(sx[:-1], sx[1:])] + [bb - a for a, bb in zip(sy[:-1], sy[1:])]
            s_min_grp = min(spacings) if spacings else s_cr
            psi_g = max(psi0 - (min(s_min_grp, s_cr) / s_cr) ** 0.5 * (psi0 - 1.0), 1.0)
        else:
            psi0, psi_g, s_min_grp = 1.0, 1.0, float("inf")
        cmin = geo.min_edge(c.layout, idx)
        ps = psi_s(cmin, c_cr)
        ex, ey = geo.eccentricity(c.layout, idx, forces) if ecc else (0.0, 0.0)
        pec = psi_ec(ex, s_cr) * psi_ec(ey, s_cr)
        N_rk = n0p * (A / A0) * psi_g * ps * pec / 1000.0
        E = sum(forces[i] for i in idx)
        res.steps = [
            Step("tau_Rk", f"Characteristic bond resistance ({'cracked' if self.cracked else 'uncracked'}, "
                 f"{self.entry.condition if self.entry else ''}), incl. psi_c={self.psi_c:.2f}",
                 round(self.tau, 2), "MPa"),
            Step("N0_Rk,p", "Single-anchor resistance", round(n0p / 1000.0, 1), "kN",
                 "pi * d * hef * tau_Rk"),
            Step("s_cr,Np", "Critical spacing", round(s_cr, 0), "mm", "7.3 d sqrt(tau_Rk,ucr) <= 3 hef"),
            Step("c_cr,Np", "Critical edge distance", round(c_cr, 0), "mm", "s_cr,Np / 2"),
            Step("A_p,N / A0_p,N", "Projected-area ratio", round(A / A0, 3), "",
                 f"A = {info['Lx']:.0f} x {info['Ly']:.0f} mm; A0 = s_cr,Np^2"),
            Step("psi_g,Np", f"Group factor (n = {n})", round(psi_g, 3), "",
                 "psi0 - (s/s_cr)^0.5 (psi0 - 1) >= 1"),
            Step("psi_s,Np", "Edge factor", round(ps, 3), "", "0.7 + 0.3 c/c_cr,Np <= 1"),
            Step("psi_ec,Np", "Eccentricity factor", round(pec, 3), "", "1/(1+2e/s_cr)"),
            Step("N_Rk,p", "Characteristic resistance", round(N_rk, 1), "kN",
                 "N0 * (A/A0) * psi_g * psi_s * psi_ec"),
        ]
        res = _fin(res, E, N_rk, self.phi_c)
        res.steps.append(Step("phi", "Capacity reduction factor 1/(gamma_c*gamma_inst)",
                              round(self.phi_c, 3), ""))
        if sustained_note:
            res.notes.append(sustained_note)
        return res, N_rk

    def _tension_cone(self, idx: Sequence[int], forces: Sequence[float], title: str, key: str,
                      ecc: bool = True) -> Tuple[CheckResult, float]:
        c, b = self.case, self.b
        res = CheckResult(key=key, title=title, mode="tension",
                          clause="EN 1992-4 7.2.1.4 / AS 5216:2021 - concrete cone failure")
        h_ef_cone, reduced = geo.effective_hef(c.layout, self.h_ef, 1.5 * self.h_ef)
        s_cr, c_cr = 3.0 * h_ef_cone, 1.5 * h_ef_cone
        k_o = (self.spec.k_cr if self.cracked else self.spec.k_ucr) if self.spec else None
        n0c = n0_rk_c(self.fc, h_ef_cone, self.cracked, b, k_o)
        A, A0, info = geo.projected_area_tension(c.layout, idx, s_cr, c_cr)
        cmin = geo.min_edge(c.layout, idx)
        ps = psi_s(cmin, c_cr)
        pre = min(1.0, 0.5 + h_ef_cone / 200.0) if c.concrete.dense_reinforcement else 1.0
        ex, ey = geo.eccentricity(c.layout, idx, forces) if ecc else (0.0, 0.0)
        pec = psi_ec(ex, s_cr) * psi_ec(ey, s_cr)
        N_rk = n0c * (A / A0) * ps * pre * pec / 1000.0
        E = sum(forces[i] for i in idx)
        res.steps = [
            Step("k", f"Cone factor ({'cracked' if self.cracked else 'uncracked'})",
                 k_o or (b.k_cr if self.cracked else b.k_ucr), ""),
            Step("hef'", "Effective embedment used" + (" (reduced - 3+ edges)" if reduced else ""),
                 round(h_ef_cone, 1), "mm"),
            Step("N0_Rk,c", "Single-anchor cone resistance", round(n0c / 1000.0, 1), "kN",
                 "k * sqrt(f'c) * hef^1.5"),
            Step("s_cr,N / c_cr,N", "Critical spacing / edge distance", f"{s_cr:.0f} / {c_cr:.0f}", "mm",
                 "3 hef / 1.5 hef"),
            Step("A_c,N / A0_c,N", "Projected-area ratio", round(A / A0, 3), "",
                 f"A = {info['Lx']:.0f} x {info['Ly']:.0f} mm; A0 = 9 hef^2"),
            Step("psi_s,N", "Edge factor", round(ps, 3), "", "0.7 + 0.3 c/c_cr,N <= 1"),
            Step("psi_re,N", "Shell-spalling (dense reinforcement) factor", round(pre, 3), "",
                 "0.5 + hef/200 <= 1 (only if reinforcement spacing < 150 mm)"),
            Step("psi_ec,N", "Eccentricity factor", round(pec, 3), "", "1/(1+2e/s_cr)"),
            Step("N_Rk,c", "Characteristic resistance", round(N_rk, 1), "kN",
                 "N0 * (A/A0) * psi_s * psi_re * psi_ec"),
        ]
        res = _fin(res, E, N_rk, self.phi_c)
        res.steps.append(Step("phi", "Capacity reduction factor 1/(gamma_c*gamma_inst)",
                              round(self.phi_c, 3), ""))
        return res, N_rk

    def _splitting(self, T, forces, N_rk_p, N_rk_c) -> Optional[CheckResult]:
        """EN 1992-4 7.2.1.7 splitting (uncracked concrete); psi_h,sp = 1.0 (conservative)."""
        c = self.case
        L = c.layout
        res = CheckResult(key="N_split", title="Splitting failure (uncracked concrete)", mode="tension",
                          clause="EN 1992-4 7.2.1.7 / AS 5216:2021 - splitting")
        ccr = self.spec.c_cr_sp if self.spec and self.spec.c_cr_sp else self.c_cr_sp(self.h, self.h_ef)
        scr = 2.0 * ccr
        cmin = geo.min_edge(L, T)
        spacings = []
        if L.n_x > 1 and L.s_x > 0:
            spacings.append(L.s_x)
        if L.n_y > 1 and L.s_y > 0:
            spacings.append(L.s_y)
        smin = min(spacings) if spacings else None
        if (cmin is None or cmin >= ccr) and (smin is None or smin >= scr):
            res.status = NA
            res.notes.append(f"Edge distances >= c_cr,sp = {ccr:.0f} mm and spacing >= s_cr,sp = {scr:.0f} mm: "
                             "splitting verification not required.")
            return res
        if N_rk_p is None:
            res.status = INCOMPLETE
            res.notes.append("Bond resistance unavailable; splitting resistance cannot be evaluated.")
            return res
        n0c = n0_rk_c(self.fc, self.h_ef, False, self.b,
                      self.spec.k_ucr if self.spec else None)
        n0 = min(N_rk_p * 1000.0, n0c)      # conservative: group bond value vs single-anchor cone
        A, A0, info = geo.projected_area_tension(L, T, scr, ccr)
        ps = psi_s(cmin, ccr)
        ex, ey = geo.eccentricity(L, T, forces)
        pec = psi_ec(ex, scr) * psi_ec(ey, scr)
        N_rk = n0 * (A / A0) * ps * pec / 1000.0
        E = sum(forces[i] for i in T)
        res.steps = [
            Step("c_cr,sp / s_cr,sp", "Splitting edge distance / spacing (EN rule from h/hef)",
                 f"{ccr:.0f} / {scr:.0f}", "mm", "1.0hef (h/hef>=2); 4.6hef-1.8h; 2.26hef (h/hef<=1.3)"),
            Step("N0_Rk,sp", "min(N_Rk,p ; N0_Rk,c)", round(n0 / 1000.0, 1), "kN"),
            Step("A_c,N / A0_c,N", "Area ratio with s_cr,sp / c_cr,sp", round(A / A0, 3), ""),
            Step("psi_s,N", "Edge factor", round(ps, 3), ""),
            Step("psi_h,sp", "Member-thickness factor (taken as 1.0, conservative)", 1.0, ""),
            Step("N_Rk,sp", "Characteristic splitting resistance", round(N_rk, 1), "kN"),
        ]
        res = _fin(res, E, N_rk, self.phi_c)
        res.notes.append("Splitting verification may be omitted if suitable splitting reinforcement is detailed "
                         "and the anchor is in cracked concrete (EN 1992-4).")
        return res

    def _steel_tension(self, E: float) -> CheckResult:
        s, b = self.steel, self.b
        res = CheckResult(key="N_steel", title="Steel failure - tension (most loaded anchor)", mode="tension",
                          clause="EN 1992-4 7.2.1.2 / AS 5216:2021 - steel failure")
        n_calc = s.A_s * s.f_uk / 1000.0
        n_rk = min(self.entry.n_rk_s, n_calc) if self.entry and self.entry.n_rk_s else n_calc
        phi = b.phi_steel_tension(s.f_yk, s.f_uk)
        res.steps = [
            Step("As", "Stress area", round(s.A_s, 1), "mm2"),
            Step("fuk / fyk", f"Steel grade {s.grade}", f"{s.f_uk:g} / {s.f_yk:g}", "MPa"),
            Step("N_Rk,s", "Characteristic steel resistance (lesser of assessment value and As*fuk for "
                 "the specified grade)" if self.entry and self.entry.n_rk_s else "Characteristic steel resistance",
                 round(n_rk, 1), "kN", "As * fuk"),
            Step("gamma_Ms", "Partial factor", round(1 / phi, 2), "", "max(1.2 fuk/fyk, 1.4)"),
        ]
        return _fin(res, E, n_rk, phi)

    def _run_tension(self, forces: List[float]) -> Tuple[List[CheckResult], Optional[float], Optional[float]]:
        out: List[CheckResult] = []
        T = [i for i, f in enumerate(forces) if f > TOL]
        if not T:
            r = CheckResult(key="N_none", title="Tension", mode="tension", status=NA)
            r.notes.append("No anchor is in tension for this load combination.")
            return [r], None, None
        out.append(self._steel_tension(max(forces)))
        # most loaded anchor, single-anchor cone / pull-out
        i_max = max(range(len(forces)), key=lambda i: forces[i])
        grp_p, N_rk_p = self._tension_pullout(T, forces, "Combined pull-out and concrete failure - tension group",
                                              "N_pullout_group")
        out.append(grp_p)
        if len(T) > 1 and self.tau is not None:
            single, _ = self._tension_pullout([i_max], forces, "Combined pull-out and concrete failure - "
                                              "most loaded anchor", "N_pullout_single", ecc=False)
            out.append(single)
        cone, N_rk_c = self._tension_cone(T, forces, "Concrete cone failure - tension group", "N_cone")
        out.append(cone)
        # splitting (uncracked concrete only - cracked concrete assumes cracks are controlled by reinforcement)
        if not self.cracked:
            sp = self._splitting(T, forces, N_rk_p, N_rk_c)
            if sp is not None:
                out.append(sp)
        return out, N_rk_p, N_rk_c

    # ---------------------------------------------------------------- shear --
    def _lever_arm_vrk_s(self, n_max: float, N_rd_s: float) -> Tuple[float, List[Step], float]:
        s, f, b = self.steel, self.case.fixture, self.b
        e1 = f.standoff + 0.5 * f.thickness
        a3 = 0.5 * self.d
        l = e1 + a3
        M0 = 1.2 * s.W_el * s.f_uk * max(0.0, 1.0 - n_max / N_rd_s) / 1000.0     # N.mm -> kN.mm? (kN.mm)
        alpha_M = 2.0 if f.restraint == "fixed" else 1.0
        V = alpha_M * M0 / l
        steps = [
            Step("l", "Lever arm", round(l, 1), "mm", "e1 + a3; e1 = standoff + t_fixture/2, a3 = d/2"),
            Step("M0_Rk,s", "Characteristic bending resistance (axial-reduced)", round(M0 / 1000.0, 3),
                 "kNm", "1.2 W_el fuk (1 - N_Ed/N_Rd,s)"),
            Step("alpha_M", f"Restraint ({f.restraint})", alpha_M, ""),
            Step("V_Rk,s", "Characteristic shear resistance with lever arm", round(V, 1), "kN",
                 "alpha_M M0_Rk,s / l"),
        ]
        return V, steps, l

    def _steel_shear(self, V_i: float, n_max: float, N_rd_s: float) -> CheckResult:
        s, f, b = self.steel, self.case.fixture, self.b
        res = CheckResult(key="V_steel", title="Steel failure - shear (most loaded anchor)", mode="shear",
                          clause="EN 1992-4 7.2.2.3 / AS 5216:2021 - steel failure in shear")
        phi = b.phi_steel_shear(s.f_yk, s.f_uk)
        k6 = b.k6_low if s.f_uk <= 500 else b.k6_high
        k7 = b.k7_ductile
        v0 = (self.entry.v_rk_s if self.entry and self.entry.v_rk_s else k6 * s.A_s * s.f_uk / 1000.0)
        if f.standoff <= 0.0:
            v_rk = k7 * v0
            res.steps = [Step("V0_Rk,s", "Single-anchor steel shear" + (" (from product assessment)"
                              if self.entry and self.entry.v_rk_s else ""), round(v0, 1), "kN",
                              f"k6 As fuk, k6 = {k6}"),
                         Step("k7", "Ductility factor", k7, "")]
        elif f.grouted and f.standoff <= 0.5 * self.d:
            v_rk = 0.8 * k7 * v0
            res.steps = [Step("V0_Rk,s", "Single-anchor steel shear", round(v0, 1), "kN", f"k6 As fuk, k6 = {k6}"),
                         Step("0.8", "Reduction for mortar pad / small gap", 0.8, "")]
            res.notes.append("Grout pad / gap <= d/2: shear resistance reduced by 0.8 (verify grout strength).")
        else:
            v_rk, steps, _ = self._lever_arm_vrk_s(max(n_max, 0.0), N_rd_s)
            res.steps = steps
            res.notes.append("Standoff > d/2 or ungrouted: lever-arm (bending) resistance used.")
        res.steps.append(Step("gamma_Ms,V", "Partial factor", round(1 / phi, 2), "", "fuk/fyk >= 1.25"))
        return _fin(res, V_i, v_rk, phi)

    def _pryout(self, V: float, N_rk_p_all: Optional[float], N_rk_c_all: float) -> CheckResult:
        res = CheckResult(key="V_pryout", title="Concrete pry-out failure", mode="shear",
                          clause="EN 1992-4 7.2.2.4 / AS 5216:2021 - concrete pry-out")
        k8 = self.b.k8_deep if self.h_ef >= 60 else self.b.k8_shallow
        N_ref = N_rk_c_all if N_rk_p_all is None else min(N_rk_c_all, N_rk_p_all)
        v_rk = k8 * N_ref
        res.steps = [
            Step("k8", f"Pry-out factor (hef = {self.h_ef:g} mm)", k8, ""),
            Step("N_Rk", "Group tension resistance (all anchors; min of bond and cone)", round(N_ref, 1), "kN"),
            Step("V_Rk,cp", "Characteristic pry-out resistance", round(v_rk, 1), "kN", "k8 * N_Rk"),
        ]
        if N_rk_p_all is None:
            res.notes.append("Bond resistance unavailable - pry-out based on concrete cone only (not conservative).")
        return _fin(res, V, v_rk, self.phi_c)

    def _edge_shear(self, edge: str, Vx: float, Vy: float, Vres: float) -> Optional[CheckResult]:
        c, b = self.case, self.b
        g = geo.edge_geometry(c.layout, edge)
        if g is None:
            return None
        c1 = g["c1"]
        limit = max(10.0 * self.h_ef, 60.0 * self.d)
        names = {"xpos": "+x", "xneg": "-x", "ypos": "+y", "yneg": "-y"}
        res = CheckResult(key=f"V_edge_{edge}", title=f"Concrete edge failure - {names[edge]} edge (c1 = {c1:.0f} mm)",
                          mode="shear", clause="EN 1992-4 7.2.2.5 / AS 5216:2021 - concrete edge failure")
        if c1 >= limit:
            res.status = NA
            res.notes.append(f"c1 = {c1:.0f} mm >= max(10 hef, 60 d) = {limit:.0f} mm: edge failure need not be verified.")
            return res
        nvec = {"xpos": (1, 0), "xneg": (-1, 0), "ypos": (0, 1), "yneg": (0, -1)}[edge]
        cosa = max(-1.0, min(1.0, (Vx * nvec[0] + Vy * nvec[1]) / Vres))
        alpha = math.degrees(math.acos(cosa))
        v0 = v0_rk_c(self.fc, self.d, self.h_ef, c1, self.cracked, b)
        A, A0, b_eff = geo.projected_area_shear(c1, g["c2a"], g["c2b"], g["positions"], self.h)
        c2 = min(g["c2a"], g["c2b"])
        ps = 1.0 if c2 == float("inf") else min(1.0, 0.7 + 0.3 * c2 / (1.5 * c1))
        ph = max(1.0, math.sqrt(1.5 * c1 / self.h)) if self.h else 1.0
        pa = geo.psi_alpha_v(alpha)
        v_rk = v0 * (A / A0) * ps * ph * pa / 1000.0
        res.steps = [
            Step("k9", f"Edge-shear factor ({'cracked' if self.cracked else 'uncracked'})",
                 b.k9_cr if self.cracked else b.k9_ucr, ""),
            Step("V0_Rk,c", "Single-anchor edge resistance", round(v0 / 1000.0, 2), "kN",
                 "k9 d^a lf^b sqrt(f'c) c1^1.5; a=0.1(lf/c1)^0.5, b=0.1(d/c1)^0.2"),
            Step("A_c,V / A0_c,V", "Projected-area ratio", round(A / A0, 3), "",
                 f"b = {b_eff:.0f} mm, h' = {min(self.h or 1.5*c1, 1.5*c1):.0f} mm; A0 = 4.5 c1^2"),
            Step("psi_s,V", "Corner / side-edge factor", round(ps, 3), "", "0.7 + 0.3 c2/(1.5 c1) <= 1"),
            Step("psi_h,V", "Member-thickness factor", round(ph, 3), "", "(1.5 c1/h)^0.5 >= 1"),
            Step("psi_alpha,V", f"Load-direction factor (alpha = {alpha:.0f} deg)", round(pa, 3), ""),
            Step("V_Rk,c", "Characteristic resistance", round(v_rk, 2), "kN",
                 "V0 * (A/A0) * psi_s * psi_h * psi_alpha  (psi_re,V = 1.0: no credit for edge reinforcement)"),
        ]
        res.notes.append("Front row of anchors assumed to carry the full shear (conservative).")
        return _fin(res, Vres, v_rk, self.phi_c)

    # ---------------------------------------------------------- interaction --
    def _interaction(self, tens: List[CheckResult], shears: List[CheckResult]) -> List[CheckResult]:
        b = self.b

        def get(lst, key):
            return next((r for r in lst if r.key == key and r.utilisation is not None), None)

        out: List[CheckResult] = []
        ns, vs = get(tens, "N_steel"), get(shears, "V_steel")
        bn_c = max([r.utilisation for r in tens if r.key.startswith(("N_pullout", "N_cone")) and r.utilisation is not None] or [0.0])
        bv_c = max([r.utilisation for r in shears if r.key.startswith(("V_pryout", "V_edge")) and r.utilisation is not None] or [0.0])
        if ns and vs:
            u = ns.utilisation ** b.alpha_steel + vs.utilisation ** b.alpha_steel
            r = CheckResult(key="NV_steel", title="Combined tension + shear - steel", mode="interaction",
                            clause="EN 1992-4 7.3 / AS 5216:2021", utilisation=u,
                            status=PASS if u <= 1 + 1e-9 else FAIL)
            r.steps = [Step("beta_N", "N_Ed / N_Rd,s", round(ns.utilisation, 3), ""),
                       Step("beta_V", "V_Ed / V_Rd,s", round(vs.utilisation, 3), ""),
                       Step("sum", f"beta_N^{b.alpha_steel:g} + beta_V^{b.alpha_steel:g}", round(u, 3), "", "<= 1.0")]
            out.append(r)
        if bn_c > 0 and bv_c > 0:
            u = bn_c ** b.alpha_concrete + bv_c ** b.alpha_concrete
            r = CheckResult(key="NV_conc", title="Combined tension + shear - concrete failure modes",
                            mode="interaction", clause="EN 1992-4 7.3 / AS 5216:2021", utilisation=u,
                            status=PASS if u <= 1 + 1e-9 else FAIL)
            lin = bn_c + bv_c
            r.steps = [Step("beta_N", "max N_Ed/N_Rd (bond, cone)", round(bn_c, 3), ""),
                       Step("beta_V", "max V_Ed/V_Rd (edge, pry-out)", round(bv_c, 3), ""),
                       Step("sum", f"beta_N^{b.alpha_concrete:g} + beta_V^{b.alpha_concrete:g}", round(u, 3), "", "<= 1.0"),
                       Step("lin", "Simplified alternative beta_N + beta_V", round(lin, 3), "",
                            f"<= {b.interaction_linear_limit}")]
            if lin > b.interaction_linear_limit + 1e-9 and u <= 1:
                r.notes.append("Simplified linear interaction (<=1.2) is exceeded although the power form passes.")
            out.append(r)
        return out

    # ------------------------------------------------------------ detailing --
    def detailing(self) -> List[CheckResult]:
        c, b, e = self.case, self.b, self.entry
        L = c.layout
        out: List[CheckResult] = []
        d = self.d
        # embedment range
        hmin = e.hef_min if e and e.hef_min else max(b.hef_min_abs, b.hef_min_factor * d)
        hmax = e.hef_max if e and e.hef_max else b.hef_max_factor * d
        r = CheckResult(key="D_hef", title="Embedment depth within qualified range", mode="detailing",
                        clause="Product assessment / AS 5216:2021")
        r.steps = [Step("hef", "Embedment on drawing", self.h_ef, "mm"),
                   Step("range", "Range (product value if known, else EN default)", f"{hmin:.0f} - {hmax:.0f}", "mm")]
        r.status = PASS if hmin - 1e-9 <= self.h_ef <= hmax + 1e-9 else FAIL
        if self.h_ef > hmax:
            r.notes.append("Embedment exceeds the qualified maximum for anchors; for reinforcing bars "
                           "AS 5216:2021 App. D allows embedments > 20 d.")
        out.append(r)
        # spacing
        smin = e.s_min if e and e.s_min else b.smin_factor * d
        spacings = [s for n, s in ((L.n_x, L.s_x), (L.n_y, L.s_y)) if n > 1 and s > 0]
        sact = min(spacings) if spacings else None
        r = CheckResult(key="D_smin", title="Minimum anchor spacing", mode="detailing", clause="Product assessment")
        if sact is None:
            r.status = NA
            r.notes.append("Single anchor - no spacing requirement.")
        else:
            r.steps = [Step("s", "Actual minimum spacing", sact, "mm"),
                       Step("s_min", "Required minimum" + (" (product)" if e and e.s_min else f" ({b.smin_factor:g} d default)"),
                            round(smin, 0), "mm")]
            r.status = PASS if sact >= smin - 1e-9 else FAIL
        out.append(r)
        # edge
        cmin_req = e.c_min if e and e.c_min else b.cmin_factor * d
        cact = geo.min_edge(L)
        r = CheckResult(key="D_cmin", title="Minimum edge distance", mode="detailing", clause="Product assessment")
        if cact is None:
            r.status = NA
            r.notes.append("No free edge near the anchors (remote).")
        else:
            r.steps = [Step("c", "Actual minimum edge distance", cact, "mm"),
                       Step("c_min", "Required minimum" + (" (product)" if e and e.c_min else f" ({b.cmin_factor:g} d default)"),
                            round(cmin_req, 0), "mm")]
            r.status = PASS if cact >= cmin_req - 1e-9 else FAIL
        out.append(r)
        # thickness
        if e and e.h_min:
            hreq = e.h_min
            how = "product"
        elif d <= 12:
            hreq, how = max(self.h_ef + 30.0, 100.0), "hef + 30 >= 100"
        else:
            hreq, how = self.h_ef + 2.0 * self.d0, "hef + 2 d0"
        r = CheckResult(key="D_hmin", title="Minimum member thickness", mode="detailing", clause="Product assessment")
        if self.h is None:
            r.status = WARN
            r.notes.append(f"Member thickness not stated; at least {hreq:.0f} mm required ({how}). Confirm the "
                           "hole will not break through or reduce rear cover.")
        else:
            r.steps = [Step("h", "Member thickness", self.h, "mm"), Step("h_min", f"Required ({how})", round(hreq, 0), "mm")]
            r.status = PASS if self.h >= hreq - 1e-9 else FAIL
        out.append(r)
        # hole clearance
        if c.fixture.hole_df is not None:
            table = {6: 7, 8: 9, 10: 12, 12: 14, 14: 16, 16: 18, 18: 20, 20: 22, 22: 24, 24: 26, 27: 30, 30: 33}
            lim = table.get(int(round(d)))
            if lim is not None:
                r = CheckResult(key="D_df", title="Fixture clearance hole", mode="detailing",
                                clause="EN 1992-4 Table 6.1 (standard clearance)")
                r.steps = [Step("df", "Hole in fixture", c.fixture.hole_df, "mm"), Step("df,max", "Standard clearance", lim, "mm")]
                r.status = PASS if c.fixture.hole_df <= lim + 1e-9 else WARN
                if r.status == WARN:
                    r.notes.append("Oversize hole: shear cannot be assumed to be shared equally; set 'oversize holes'.")
                out.append(r)
        return out

    # ------------------------------------------------------------------ run --
    def run_combo(self, combo: LoadCombo) -> ComboResult:
        c = self.case
        n = c.layout.n
        N, Vx, Vy, Mx, My = combo.N, combo.Vx, combo.Vy, combo.Mx, combo.My
        if combo.basis == "per_anchor":
            N, Vx, Vy, Mx, My = N * n, Vx * n, Vy * n, Mx * n, My * n
        cr = ComboResult(combo=combo)
        if combo.limit_state != "ULS":
            self.warnings.append(f"Load combination '{combo.name}' is labelled {combo.limit_state}: the values were "
                                 "treated as factored ULS actions - confirm with the engineer of record.")
        forces, notes = geo.anchor_tensions(c.layout, N, Mx, My)
        pts = geo.anchor_coords(c.layout)
        cr.anchor_forces = [{"i": i + 1, "x": round(p[0], 1), "y": round(p[1], 1), "N": round(f, 2)}
                            for i, (p, f) in enumerate(zip(pts, forces))]
        for nt in notes:
            if nt not in self.warnings:
                self.warnings.append(nt)
        tens, N_rk_p_grp, N_rk_c_grp = self._run_tension(forces)
        cr.checks.extend(tens)
        # sustained check
        if combo.sustained_fraction > 0 and any(f > TOL for f in forces) and self.tau is not None:
            psi0, from_prod = self.psi_sus0()
            ref = next((r for r in tens if r.key == "N_pullout_group" and r.R_d), None)
            if ref and ref.R_d:
                E_s = combo.sustained_fraction * ref.E_d
                u = E_s / (psi0 * ref.R_d)
                r = CheckResult(key="N_sus", title="Sustained tension (creep) check", mode="tension",
                                clause="EN 1992-4 / EAD 330499 - sustained load",
                                utilisation=u, status=PASS if u <= 1 + 1e-9 else FAIL)
                r.steps = [Step("N_Ed,sus", "Sustained part of tension", round(E_s, 1), "kN"),
                           Step("psi_sus0", "Sustained-load factor" + ("" if from_prod
                                else " - DEFAULT (assessment value not found)"), psi0, ""),
                           Step("N_Rd,p", "Design pull-out resistance", round(ref.R_d, 1), "kN"),
                           Step("ratio", "N_Ed,sus / (psi_sus0 N_Rd,p)", round(u, 3), "")]
                if not from_prod:
                    r.notes.append("psi_sus0 not provided in the product documents; 0.6 assumed - replace with the assessment value.")
                cr.checks.append(r)
        Vres = math.hypot(Vx, Vy)
        shears: List[CheckResult] = []
        if Vres > TOL:
            if c.shear_to_all_anchors:
                n_eff = n
            else:
                n_eff = max(1, c.layout.n_y if abs(Vx) >= abs(Vy) else c.layout.n_x)
            n_max = max(forces) if forces else 0.0
            steel_tens = next((r for r in tens if r.key == "N_steel"), None)
            N_rd_s = steel_tens.R_d if steel_tens and steel_tens.R_d else (self.steel.A_s * self.steel.f_uk / 1000.0 *
                                                                             self.b.phi_steel_tension(self.steel.f_yk, self.steel.f_uk))
            shears.append(self._steel_shear(Vres / n_eff, n_max, N_rd_s))
            # pry-out needs group (all anchors) tension capacity
            all_idx = list(range(n))
            uni = [1.0] * n
            _, N_p_all = (self._tension_pullout(all_idx, uni, "tmp", "tmp", ecc=False) if self.tau is not None else (None, None))
            _, N_c_all = self._tension_cone(all_idx, uni, "tmp", "tmp", ecc=False)
            shears.append(self._pryout(Vres, N_p_all, N_c_all))
            for edge in geo.EDGES:
                r = self._edge_shear(edge, Vx, Vy, Vres)
                if r is not None:
                    shears.append(r)
        cr.checks.extend(shears)
        cr.checks.extend(self._interaction(tens, shears))
        utils = [(r.utilisation, r.title) for r in cr.checks if r.utilisation is not None and r.status in (PASS, FAIL)]
        if utils:
            u, t = max(utils, key=lambda x: x[0])
            cr.max_utilisation, cr.governing = u, t
        incomplete = any(r.status == INCOMPLETE for r in cr.checks)
        failed = any(r.status == FAIL for r in cr.checks)
        cr.status = FAIL if failed else (INCOMPLETE if incomplete else PASS)
        return cr

    def run(self) -> CaseResult:
        res = CaseResult(case=self.case, spec=self.spec)
        res.detailing = self.detailing()
        if not self.case.loads:
            res.warnings.append("No design loads available for this case - strength checks not performed.")
        for combo in self.case.loads:
            res.combos.append(self.run_combo(combo))
        res.warnings = list(dict.fromkeys(self.warnings + res.warnings))
        res.assumptions = list(dict.fromkeys(self.assumptions))
        done = [cr for cr in res.combos if cr.max_utilisation is not None]
        if done:
            worst = max(done, key=lambda cr: cr.max_utilisation)
            res.max_utilisation = worst.max_utilisation
            res.governing = f"{worst.governing} [{worst.combo.name}]"
        if self.spec is not None and self.gamma_inst is None:
            for cr in res.combos:
                for ck in cr.checks:
                    if ck.status == FAIL and ck.key.startswith(("N_pullout", "N_cone", "N_split", "V_pryout", "V_edge", "NV_conc")):
                        ck.notes.append(f"Provisional: resistance uses the DEFAULT installation factor gamma_inst = "
                                        f"{self.b.gamma_inst_default} (product value not found); the check may pass with the assessed value.")
        det_fail = any(r.status == FAIL for r in res.detailing)
        any_fail = any(cr.status == FAIL for cr in res.combos)
        any_inc = any(cr.status == INCOMPLETE for cr in res.combos) or not res.combos
        res.status = FAIL if (any_fail or det_fail) else (INCOMPLETE if any_inc else PASS)
        if self.spec is not None:
            res.spec_provenance = [f"{k}: {v.short()}" for k, v in self.spec.provenance.items()]
        b = self.b
        res.basis_table = [
            Step("phi_c", "Concrete-related capacity reduction factor", round(self.phi_c, 3), "", "1/(gamma_c gamma_inst)"),
            Step("gamma_c", "Partial factor", b.gamma_c, ""),
            Step("gamma_inst", "Installation safety factor", self.gamma_inst if self.gamma_inst is not None
                 else f"{b.gamma_inst_default} (default)", ""),
            Step("phi_Ms,N", "Steel tension", round(b.phi_steel_tension(self.steel.f_yk, self.steel.f_uk), 3), ""),
            Step("phi_Ms,V", "Steel shear", round(b.phi_steel_shear(self.steel.f_yk, self.steel.f_uk), 3), ""),
            Step("k", "Cone factor", b.k_cr if self.cracked else b.k_ucr, ""),
            Step("psi_c", "Concrete-strength factor on bond", round(self.psi_c, 3), ""),
        ]
        return res
