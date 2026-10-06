"""Post-installed reinforcing bars - AS 5216:2021 Appendix D (D.4.1) with AS 3600 cl 13.1.2.

Method (verified against AEFAC Technical Note TN-08 v1.1 and the Ramset Specifiers
Anchoring Resource Book, both public documents):

  L_sy.t = max( 0.5 k1 k3 f_sy d_b / (k2 sqrt(f'c)) , 0.058 f_sy k1 d_b )      AS 3600 13.1.2.2
  k2 = (132 - d_b)/100 ; k3 = 1 - 0.15 (c_d - d_b)/d_b  (0.7 <= k3 <= 1.0)
  If the adhesive's design ultimate bond strength f_bd (EAD 330087 qualification) is less than the
  EAD 330087 table value for the concrete strength, L_sy.t is increased in proportion.
  L_st = L_sy.t * sigma_st / f_sy  >= 12 d_b                                    AS 3600 13.1.2.4
  Design: N* <= phi * A_s * sigma_st, phi = 0.8

Not covered (flagged): D.4.2 bond-splitting verification (needs product EAD 332402 data),
seismic/fatigue/fire (App. D scope is static), flooded holes, lap splices with new bars
(AS 3600 13.2), shear transfer / dowel action across the joint (AS 3600 cl 8.4).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from ..models import (AdhesiveSpec, CaseResult, CheckResult, ComboResult, DesignCase, Step)
from .anchors import AnchorEngine, FAIL, INCOMPLETE, NA, PASS, WARN, interp
from .basis import DesignBasis
from .steel import bar_props

# EAD 330087 / AS 5216:2021 Table D.4 - design ultimate bond strength without limitation
F_BD_TABLE: Dict[float, float] = {20: 2.3, 25: 2.7, 32: 3.2, 40: 3.7, 45: 4.0, 50: 4.3}


def f_bd_table(fc: float, d_b: float) -> float:
    v = interp(F_BD_TABLE, fc)
    if d_b > 32:
        v *= (132.0 - d_b) / 100.0
    return v


def k_factors(d_b: float, c_d: float, k1_top: bool = False):
    k1 = 1.3 if k1_top else 1.0
    k2 = (132.0 - d_b) / 100.0
    k3 = 1.0 - 0.15 * (c_d - d_b) / d_b
    k3 = max(0.7, min(1.0, k3))
    return k1, k2, k3


def development_length(f_sy: float, d_b: float, fc: float, c_d: float, k1_top: bool = False):
    k1, k2, k3 = k_factors(d_b, c_d, k1_top)
    fc_eff = min(fc, 65.0)
    l1 = 0.5 * k1 * k3 * f_sy * d_b / (k2 * math.sqrt(fc_eff))
    l2 = 0.058 * f_sy * k1 * d_b
    return max(l1, l2), (k1, k2, k3, l1, l2)


def min_cover(L: float, d_b: float, drilling: str) -> float:
    """EAD 330087 minimum concrete cover (AEFAC TN-08 section 5.3)."""
    large = d_b >= 25
    if drilling == "compressed_air":
        base, k = (60.0 if large else 50.0), 0.08
    else:
        base, k = (40.0 if large else 30.0), 0.06
    return max(base + k * L, 2.0 * d_b)


def check_rebar_case(case: DesignCase, spec: Optional[AdhesiveSpec],
                     basis: Optional[DesignBasis] = None) -> CaseResult:
    b = basis or DesignBasis()
    d = case.fastener_d
    s = bar_props(d, case.grade)
    L = case.layout
    res = CaseResult(case=case, spec=spec)
    res.assumptions.append("Static ULS tension in the bar; AS 5216:2021 Appendix D.4.1 development-length method "
                           "(AS 3600 cl 13.1.2.2) with the adhesive's design bond strength f_bd.")
    # --- scope flags
    for flag, text in ((case.seismic, "Seismic actions"), (case.fire, "Fire"), (case.fatigue, "Fatigue")):
        if flag:
            res.warnings.append(f"{text} flagged: outside the scope of AS 5216 App. D / AEFAC TN-08 (static loading only).")
    if case.hole_condition == "flooded":
        res.warnings.append("Flooded hole: not covered by the App. D / TN-08 method (dry and wet concrete only).")
    if d < 10:
        res.warnings.append("Bar diameter < 10 mm is outside the EAD 330087 / TN-08 scope.")
    if case.concrete.fc < 20:
        res.warnings.append("f'c < 20 MPa is outside the EAD 330087 / TN-08 scope.")
    if not case.concrete.stated_on_drawing:
        res.assumptions.append(f"Concrete strength f'c = {case.concrete.fc:g} MPa assumed (not stated on drawing).")

    # --- cover / spacing => c_d
    edges = [e for e in (L.c_xneg, L.c_xpos, L.c_yneg, L.c_ypos) if e is not None]
    if case.rebar_clear_cover is not None:
        cover = case.rebar_clear_cover
    elif edges:
        cover = min(edges) - d / 2.0
    else:
        cover = None
    sp_list = [sv for n, sv in ((L.n_x, L.s_x), (L.n_y, L.s_y)) if n > 1 and sv > 0]
    if case.rebar_clear_spacing is not None:
        clear_sp = case.rebar_clear_spacing
    elif sp_list:
        clear_sp = min(sp_list) - d
    else:
        clear_sp = None
    cands = [v for v in (cover, None if clear_sp is None else clear_sp / 2.0) if v is not None]
    if cands:
        c_d = min(cands)
    else:
        c_d = 3.0 * d
        res.assumptions.append("Cover and spacing not stated: c_d assumed >= 3 d_b (k3 = 0.7) - verify on site.")
    L_sy, (k1, k2, k3, l1, l2) = development_length(s.f_yk, d, case.concrete.fc, c_d, case.rebar_k1_top_bar)

    # --- bond strength adjustment
    fbd_tab = f_bd_table(case.concrete.fc, d)
    fbd_eta = interp(spec.f_bd, case.concrete.fc) if spec and spec.f_bd else None
    if spec is None:
        res.warnings.append("Adhesive product not identified: App. D requires a product pre-qualified to "
                            "EAD 330087 ('ETA-Rebar') - result is PROVISIONAL.")
    elif fbd_eta is None:
        res.warnings.append(f"No f_bd (EAD 330087) value found for {spec.name}: assumed >= the EAD table value "
                            f"({fbd_tab:.1f} MPa). Confirm against the product's ETA-Rebar.")
    if spec is not None and spec.rebar_qualified is not True:
        res.warnings.append(f"{spec.name} is not confirmed as qualified for post-installed rebar (EAD 330087 / "
                            "TR 023) - Appendix D design cannot be relied on until confirmed.")
    ratio = max(1.0, fbd_tab / fbd_eta) if fbd_eta else 1.0
    L_req = L_sy * ratio
    # --- capacity at provided embedment
    emb = case.h_ef
    cap_ratio = min(1.0, emb / L_req)
    sigma = s.f_yk * cap_ratio
    L_min12 = 12.0 * d
    N_st = s.A_s * sigma / 1000.0
    phi = b.phi_rebar_tension
    R_d = phi * N_st if emb >= L_min12 - 1e-9 else 0.0

    steps = [
        Step("d_b / f_sy", f"Bar {case.grade}", f"{d:g} / {s.f_yk:g}", "mm / MPa"),
        Step("c_d", "min(cover, half clear spacing)", round(c_d, 1), "mm"),
        Step("k1, k2, k3", "AS 3600 13.1.2.2 factors", f"{k1:g}, {k2:.2f}, {k3:.2f}", "",
             "k2=(132-d)/100; k3=1-0.15(c_d-d)/d in [0.7,1.0]"),
        Step("L_sy.t (basic)", "0.5 k1 k3 fsy d/(k2 sqrt f'c)", round(l1, 0), "mm"),
        Step("L_sy.t (min)", "0.058 fsy k1 d", round(l2, 0), "mm"),
        Step("L_sy.t", "Development length to yield (governing)", round(L_sy, 0), "mm"),
        Step("f_bd,EAD", "EAD 330087 table design bond strength", round(fbd_tab, 2), "MPa"),
        Step("f_bd,ETA", "Product design bond strength (ETA-Rebar)",
             "n/a" if fbd_eta is None else round(fbd_eta, 2), "MPa"),
        Step("L_req", "Required length = L_sy.t x max(1, f_bd,EAD/f_bd,ETA)", round(L_req, 0), "mm"),
        Step("L", "Embedment provided", emb, "mm"),
        Step("sigma_st", "Stress developed in bar = fsy x min(1, L/L_req)", round(sigma, 0), "MPa",
             "AS 3600 13.1.2.4: L_st = L_sy.t sigma_st/f_sy >= 12 d_b"),
        Step("N_st", "Nominal tensile capacity A_s sigma_st", round(N_st, 1), "kN"),
        Step("phi", "Capacity reduction factor (tension)", phi, ""),
    ]
    res.basis_table = steps

    for combo in case.loads:
        n = L.n
        N = combo.N * (n if combo.basis == "per_anchor" else 1.0)
        per_bar = N / n
        cr = ComboResult(combo=combo)
        chk = CheckResult(key="R_dev", title="Bar tension capacity - development length (AS 3600 13.1.2 / AS 5216 App. D)",
                          mode="rebar", clause="AS 5216:2021 Appendix D.4.1; AS 3600:2018 cl 13.1.2",
                          steps=steps, R_k=N_st, phi=phi, R_d=R_d, E_d=per_bar)
        if emb < L_min12 - 1e-9:
            chk.status = FAIL
            chk.utilisation = float("inf") if per_bar > 0 else 0.0
            chk.notes.append(f"Embedment {emb:g} mm < 12 d_b = {L_min12:g} mm (AS 3600 cl 13.1.2.4 minimum).")
        elif per_bar <= 1e-9:
            chk.status, chk.utilisation = NA, 0.0
        else:
            chk.utilisation = per_bar / R_d
            chk.status = PASS if chk.utilisation <= 1 + 1e-9 else FAIL
        if emb < L_req and chk.status in (PASS, FAIL):
            chk.notes.append(f"Partial development: bar embedded {emb:g} mm < L_req {L_req:.0f} mm "
                             f"- stress limited to {sigma:.0f} MPa; bar cannot reach yield.")
        cr.checks.append(chk)
        sh = math.hypot(combo.Vx, combo.Vy)
        if sh > 1e-9:
            c2 = CheckResult(key="R_shear", title="Shear transfer across joint / dowel action", mode="rebar",
                             status=WARN, clause="AS 3600:2018 cl 8.4 (shear friction)")
            c2.notes.append(f"Shear {sh:.1f} kN: shear transfer by shear-friction or dowel action (AS 3600 cl 8.4) "
                            "is NOT verified by this tool - verify separately.")
            cr.checks.append(c2)
        cr.max_utilisation = chk.utilisation if chk.status in (PASS, FAIL) else None
        cr.governing = chk.title
        cr.status = chk.status if chk.status != NA else PASS
        res.combos.append(cr)

    # --- detailing (EAD 330087 / TN-08 5.3)
    dets: List[CheckResult] = []
    ccov = min_cover(emb, d, case.drilling)
    r = CheckResult(key="D_cover", title="Minimum concrete cover to post-installed bar (EAD 330087)", mode="detailing",
                    clause="AEFAC TN-08 5.3 / EAD 330087")
    if cover is None:
        r.status = WARN
        r.notes.append(f"Cover not stated; at least {ccov:.0f} mm required for {case.drilling} drilling.")
    else:
        r.steps = [Step("c", "Clear cover provided", round(cover, 1), "mm"),
                   Step("c_min", "30 + 0.06 L >= 2 d (hammer/diamond, d<25); 40+0.06L (d>=25); "
                    "50+0.08L / 60+0.08L (compressed air)", round(ccov, 0), "mm")]
        r.status = PASS if cover >= ccov - 1e-9 else FAIL
    dets.append(r)
    r = CheckResult(key="D_sp", title="Minimum clear spacing between post-installed bars", mode="detailing",
                    clause="AEFAC TN-08 5.3 / EAD 330087")
    smin = max(40.0, 4.0 * d)
    if clear_sp is None:
        r.status = NA
        r.notes.append("Single bar / spacing not applicable.")
    else:
        r.steps = [Step("s_clear", "Clear spacing", round(clear_sp, 1), "mm"), Step("s_min", "max(40, 4 d)", smin, "mm")]
        r.status = PASS if clear_sp >= smin - 1e-9 else FAIL
    dets.append(r)
    r = CheckResult(key="D_thick", title="Member thickness vs hole depth", mode="detailing", clause="Product assessment")
    if case.concrete.thickness is None:
        r.status = WARN
        r.notes.append(f"Member thickness not stated: ensure thickness >= embedment + rear cover (>= {emb + ccov:.0f} mm).")
    else:
        need = emb + ccov
        r.steps = [Step("h", "Member thickness", case.concrete.thickness, "mm"),
                   Step("h_req", "Embedment + minimum rear cover", round(need, 0), "mm")]
        r.status = PASS if case.concrete.thickness >= need - 1e-9 else FAIL
    dets.append(r)
    res.detailing = dets

    # --- aggregate
    done = [cr for cr in res.combos if cr.max_utilisation is not None]
    if done:
        w = max(done, key=lambda c: c.max_utilisation)
        res.max_utilisation, res.governing = w.max_utilisation, f"{w.governing} [{w.combo.name}]"
    failed = any(cr.status == FAIL for cr in res.combos) or any(r.status == FAIL for r in dets)
    res.status = FAIL if failed else (PASS if res.combos else INCOMPLETE)
    if spec is None or (spec.rebar_qualified is not True):
        if res.status == PASS:
            res.status = INCOMPLETE
            res.warnings.append("Status downgraded to INCOMPLETE: adhesive qualification for post-installed rebar "
                                "(EAD 330087) not confirmed.")
    if spec is not None:
        res.spec_provenance = [f"{k}: {v.short()}" for k, v in spec.provenance.items()]
    # --- alternative: bar designed as an anchor if bond data exists
    if spec is not None and any(abs(e.d - d) < 0.51 for e in spec.bond):
        try:
            alt = AnchorEngine(case, spec, b).run()
            res.alt_method = alt
        except Exception as exc:  # pragma: no cover - defensive
            res.warnings.append(f"Anchor-method cross-check not possible: {exc}")
    return res
