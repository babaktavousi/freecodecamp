"""Regression tests for the design engine.

The expected numbers come from PUBLISHED documents (not from this code):

* Ramset Specifiers Anchoring Resource Book (SARB), ChemSet Reo 502 Xtrem anchor-stud tables
  (f'c = 32 MPa, phi = 1/1.5): Table 2a cone, Table 4a-1 edge shear, Table 4e pry-out.
* AEFAC Technical Note TN-08 v1.1 (post-installed rebar): Examples 1 and 2.
* Ramset SARB rebar chapter: steel tension capacity table for N-grade bars.
"""
import math

import pytest

from anchorcheck.design import DesignBasis, check_case
from anchorcheck.design import anchors as A
from anchorcheck.design import geometry as G
from anchorcheck.design import rebar as R
from anchorcheck.models import (AdhesiveSpec, BondEntry, Concrete, DesignCase, Fixture, Layout,
                                LoadCombo)

B = DesignBasis()
PHI = 1 / 1.5


# ------------------------------------------------------------------ cone / pry-out / edge
@pytest.mark.parametrize("hef,expected", [(70, 24.3), (80, 29.7), (90, 35.4), (100, 41.5), (110, 47.9)])
def test_cone_uncracked_matches_ramset_table_2a(hef, expected):
    n0 = A.n0_rk_c(32, hef, cracked=False, basis=B) / 1000
    assert n0 * PHI == pytest.approx(expected, abs=0.1)


def test_cracked_cone_is_070_of_uncracked():
    assert B.k_cr / B.k_ucr == pytest.approx(0.70, abs=0.005)


@pytest.mark.parametrize("hef,expected", [(90, 70.8), (110, 95.7), (125, 116.0), (170, 183.9), (210, 252.5)])
def test_pryout_matches_ramset_table_4e(hef, expected):
    n0 = A.n0_rk_c(32, hef, cracked=False, basis=B)
    assert 2.0 * n0 / 1000 * PHI == pytest.approx(expected, rel=0.01)


@pytest.mark.parametrize("d,hef,c1,expected",
                         [(10, 70, 40, 4.3), (12, 90, 40, 4.7), (16, 110, 45, 6.2)])
def test_edge_shear_matches_ramset_table_4a1(d, hef, c1, expected):
    v0 = A.v0_rk_c(32, d, hef, c1, cracked=False, basis=B) / 1000
    assert v0 * PHI == pytest.approx(expected, abs=0.1)


def test_group_spacing_factor_equals_ramset_xna():
    """Two anchors: per-anchor factor must equal Ramset Xna = 0.5 + a/(6h)."""
    h, s = 100.0, 100.0
    lay = Layout(n_x=2, n_y=1, s_x=s)
    Aa, A0, _ = G.projected_area_tension(lay, [0, 1], 3 * h, 1.5 * h)
    assert Aa / A0 / 2 == pytest.approx(0.5 + s / (6 * h), abs=1e-6)


def test_single_anchor_near_edge_area_ratio():
    h, c = 100.0, 75.0
    lay = Layout(c_xneg=c)
    Aa, A0, _ = G.projected_area_tension(lay, [0], 3 * h, 1.5 * h)
    assert Aa / A0 == pytest.approx((c + 1.5 * h) / (3 * h), abs=1e-9)


def test_far_apart_anchors_do_not_interact():
    lay = Layout(n_x=2, s_x=1000)
    Aa, A0, _ = G.projected_area_tension(lay, [0, 1], 300, 150)
    assert Aa / A0 == pytest.approx(2.0)


def test_psi_alpha_v_matches_ramset_xvd():
    assert G.psi_alpha_v(30) == 1.0
    assert G.psi_alpha_v(60) == pytest.approx(1.07, abs=0.05)
    assert G.psi_alpha_v(70) == pytest.approx(1.2, abs=0.05)
    assert G.psi_alpha_v(80) == pytest.approx(1.5, abs=0.05)
    assert G.psi_alpha_v(90) == pytest.approx(2.0, abs=1e-9)      # parallel to edge -> 2.0
    assert G.psi_alpha_v(120) == 2.0


def test_three_edge_rule_reduces_hef():
    # three edges inside c_cr,N = 300 mm: c_max = 80 -> h'ef = max(80/1.5, s_max/3) = 53.3 mm
    lay = Layout(c_xneg=60, c_xpos=70, c_yneg=500, c_ypos=80)
    h_eff, reduced = G.effective_hef(lay, 200, 300)
    assert reduced is True
    assert h_eff == pytest.approx(80 / 1.5, abs=1e-6)


def test_two_edges_do_not_trigger_three_edge_rule():
    h_eff, reduced = G.effective_hef(Layout(c_xneg=60, c_xpos=70), 200, 300)
    assert reduced is False and h_eff == 200


def test_anchor_force_distribution_pure_moment():
    lay = Layout(n_x=2, n_y=2, s_x=200, s_y=200)
    f, _ = G.anchor_tensions(lay, N=0.0, Mx=20.0, My=0.0)    # rows at y = +/-100 mm
    assert sorted(f) == pytest.approx([-50, -50, 50, 50])      # M*y/sum(y^2) = 20000*100/40000
    pts = G.anchor_coords(lay)
    assert sum(fi * y for fi, (_, y) in zip(f, pts)) == pytest.approx(20.0 * 1000)   # moment equilibrium


def test_steel_phi_matches_ramset_gamma_ms():
    assert 1 / B.phi_steel_tension(400, 500) == pytest.approx(1.5)      # grade 5.8
    assert 1 / B.phi_steel_tension(640, 800) == pytest.approx(1.5)      # grade 8.8
    assert 1 / B.phi_steel_tension(450, 700) == pytest.approx(1.87, abs=0.01)   # A4-70


# --------------------------------------------------------------------- rebar (AEFAC TN-08)
def test_tn08_example1_development_length():
    L, (k1, k2, k3, l1, l2) = R.development_length(500, 12, 25, c_d=3 * 12)   # k3 = 0.7
    assert k3 == pytest.approx(0.7)
    assert l1 == pytest.approx(350, abs=1)
    assert l2 == pytest.approx(348, abs=1)
    assert L == pytest.approx(350, abs=1)


def test_tn08_example2_development_length_min_governs():
    L, (_, _, k3, l1, l2) = R.development_length(500, 12, 32, c_d=36)
    assert l1 == pytest.approx(310, abs=1.5)
    assert L == pytest.approx(348, abs=1)


def test_f_bd_table_values():
    assert R.f_bd_table(32, 16) == pytest.approx(3.2)
    assert R.f_bd_table(28.5, 16) == pytest.approx(2.7 + (3.2 - 2.7) * 3.5 / 7)
    assert R.f_bd_table(40, 36) == pytest.approx(3.7 * (132 - 36) / 100)


def test_min_cover_per_ead_330087():
    assert R.min_cover(200, 16, "hammer") == pytest.approx(max(30 + 0.06 * 200, 32))
    assert R.min_cover(200, 16, "compressed_air") == pytest.approx(max(50 + 0.08 * 200, 32))


def _rebar_case(**kw):
    c = DesignCase(id="R1", kind="rebar", fastener_d=16, grade="500N", h_ef=400,
                   layout=Layout(n_x=1, n_y=1, c_xneg=100),
                   concrete=Concrete(fc=32, thickness=600, cracked=True, stated_on_drawing=True),
                   loads=[LoadCombo(name="ULS", N=60.0)])
    for k, v in kw.items():
        setattr(c, k, v)
    return c


@pytest.mark.parametrize("d,expected", [(12, 350), (16, 465), (20, 580), (24, 700), (32, 990), (40, 1345)])
def test_nominal_development_length_matches_ramset_table_2(d, expected):
    """Ramset SARB Table 2: Lsy.t(nom), f'c = 32 MPa, k1 = 1.0, k3 = 0.7 (cover/spacing >= 3 db)."""
    L, _ = R.development_length(500, d, 32, c_d=3 * d)
    assert L == pytest.approx(expected, abs=6)


def test_rebar_full_development_capacity_is_phi_fsy_As():
    # f_bd equal to the EAD 330087 table => no length enhancement; embedment 500 > L_sy.t = 464 mm
    spec = AdhesiveSpec(name="Test epoxy", rebar_qualified=True, f_bd=dict(R.F_BD_TABLE))
    res = check_case(_rebar_case(h_ef=500, concrete=Concrete(fc=32, thickness=700, cracked=True,
                                                             stated_on_drawing=True)), spec)
    chk = res.combos[0].checks[0]
    As = math.pi * 16 ** 2 / 4
    # bar yields: phi*As*fsy = 0.8*201*500 = 80.4 kN  (Ramset SARB: phi*Nus for N16 = 80.4 kN)
    assert chk.R_d == pytest.approx(0.8 * As * 500 / 1000, rel=1e-3)
    assert chk.R_d == pytest.approx(80.4, abs=0.2)
    assert res.status == "PASS"


def test_rebar_low_product_bond_strength_lengthens_requirement():
    """A product f_bd below the EAD table value must reduce capacity at a fixed embedment."""
    ok = AdhesiveSpec(name="ok", rebar_qualified=True, f_bd=dict(R.F_BD_TABLE))
    weak = AdhesiveSpec(name="weak", rebar_qualified=True, f_bd={20: 2.0, 50: 3.0})
    case = lambda: _rebar_case(h_ef=470, concrete=Concrete(fc=32, thickness=700, cracked=True, stated_on_drawing=True))
    assert check_case(case(), weak).combos[0].checks[0].R_d < check_case(case(), ok).combos[0].checks[0].R_d


def test_rebar_partial_development_reduces_stress():
    spec = AdhesiveSpec(name="Test epoxy", rebar_qualified=True)
    res = check_case(_rebar_case(h_ef=150, loads=[LoadCombo(name="ULS", N=40.0)]), spec)
    chk = res.combos[0].checks[0]
    assert chk.R_d < 80.4
    assert any("Partial development" in n for n in chk.notes)


def test_rebar_embedment_below_12db_fails():
    spec = AdhesiveSpec(name="Test epoxy", rebar_qualified=True)
    res = check_case(_rebar_case(h_ef=150), spec)    # 150 < 12*16 = 192
    assert res.combos[0].checks[0].status == "FAIL"


def test_rebar_without_qualified_adhesive_is_provisional():
    res = check_case(_rebar_case(), None)
    assert res.status == "INCOMPLETE"
    assert any("PROVISIONAL" in w or "pre-qualified" in w for w in res.warnings)


def test_rebar_bond_ratio_scales_length():
    spec_lo = AdhesiveSpec(name="X", rebar_qualified=True, f_bd={20: 2.0, 50: 3.0})
    res = check_case(_rebar_case(), spec_lo)
    steps = {s.symbol: s.value for s in res.basis_table}
    assert steps["L_req"] > steps["L_sy.t"]


# ------------------------------------------------------------------ anchor system level
def _spec(tau_cr=9.0, tau_ucr=16.0, d=16):
    return AdhesiveSpec(name="Unit-test adhesive", gamma_inst=1.0, psi_sus0=0.72, rebar_qualified=False,
                        bond=[BondEntry(d=d, tau_cr=tau_cr, tau_ucr=tau_ucr)], is_demo=True)


def _anchor_case(**kw):
    c = DesignCase(id="A1", fastener_d=16, grade="8.8", h_ef=125,
                   layout=Layout(n_x=2, n_y=2, s_x=200, s_y=200, c_xneg=250, c_xpos=250, c_yneg=250, c_ypos=250),
                   concrete=Concrete(fc=32, thickness=300, cracked=True, stated_on_drawing=True),
                   fixture=Fixture(thickness=20, standoff=0, grouted=True, hole_df=18),
                   loads=[LoadCombo(name="ULS", N=60, Vx=20)])
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_anchor_group_runs_and_reports_all_modes():
    res = check_case(_anchor_case(), _spec())
    keys = {c.key for c in res.combos[0].checks}
    assert {"N_steel", "N_pullout_group", "N_cone", "V_steel", "V_pryout", "NV_steel", "NV_conc"} <= keys
    assert res.max_utilisation is not None and res.status in ("PASS", "FAIL")


def test_steel_tension_capacity_8_8_m16():
    res = check_case(_anchor_case(loads=[LoadCombo(N=40)]), _spec())
    st = next(c for c in res.combos[0].checks if c.key == "N_steel")
    assert st.R_k == pytest.approx(157 * 800 / 1000, rel=1e-6)      # 125.6 kN
    assert st.phi == pytest.approx(1 / 1.5)
    assert st.R_d == pytest.approx(83.7, abs=0.1)                     # Ramset M16 8.8 phiNus 83.7


def test_missing_bond_data_gives_incomplete_not_pass():
    res = check_case(_anchor_case(), AdhesiveSpec(name="No data"))
    assert res.status in ("INCOMPLETE", "FAIL")
    assert any(c.status == "INCOMPLETE" for c in res.combos[0].checks)


def test_overload_fails():
    res = check_case(_anchor_case(loads=[LoadCombo(N=900)]), _spec())
    assert res.status == "FAIL" and res.max_utilisation > 1


def test_edge_distance_reduces_capacity():
    far = check_case(_anchor_case(), _spec())
    near_layout = Layout(n_x=2, n_y=2, s_x=200, s_y=200, c_xneg=80, c_xpos=250, c_yneg=250, c_ypos=250)
    near = check_case(_anchor_case(layout=near_layout), _spec())
    cone_far = next(c for c in far.combos[0].checks if c.key == "N_cone")
    cone_near = next(c for c in near.combos[0].checks if c.key == "N_cone")
    assert cone_near.R_d < cone_far.R_d


def test_edge_shear_check_generated_near_edge():
    lay = Layout(n_x=1, n_y=1, c_xpos=60)
    res = check_case(_anchor_case(layout=lay, loads=[LoadCombo(Vx=10)], fastener_d=12, h_ef=90), _spec(d=12))
    edge = [c for c in res.combos[0].checks if c.key == "V_edge_xpos"]
    assert edge and edge[0].utilisation > 0


def test_min_edge_distance_detailing_fail():
    lay = Layout(c_xpos=40)    # < 5d = 80 for M16
    res = check_case(_anchor_case(layout=lay), _spec())
    assert any(r.key == "D_cmin" and r.status == "FAIL" for r in res.detailing)


def test_uncracked_gives_higher_capacity_than_cracked():
    cr = check_case(_anchor_case(), _spec())
    ucr = check_case(_anchor_case(concrete=Concrete(fc=32, thickness=300, cracked=False, stated_on_drawing=True)), _spec())
    n_cr = next(c for c in cr.combos[0].checks if c.key == "N_cone").R_d
    n_uc = next(c for c in ucr.combos[0].checks if c.key == "N_cone").R_d
    assert n_uc / n_cr == pytest.approx(11.0 / 7.7, rel=1e-3)


def test_seismic_flag_produces_warning():
    res = check_case(_anchor_case(seismic=True), _spec())
    assert any("Appendix F" in w for w in res.warnings)


def test_default_gamma_inst_is_conservative_and_flagged():
    s = _spec()
    s.gamma_inst = None
    res = check_case(_anchor_case(), s)
    assert any("gamma_inst" in w for w in res.warnings)
    assert res.basis_table[0].value == pytest.approx(1 / (1.5 * 1.4), abs=1e-3)
