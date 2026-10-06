"""Design basis: every code factor in ONE place, each with its provenance.

The factors follow the EN 1992-4 / EAD 330499 methodology that AS 5216:2021
adopts for post-installed bonded anchors.  Where a value is verified against
published manufacturer tables that are themselves stated to be AS 5216
designs, the `verified_against` field says so.  Where a value is a default
because the product's assessment document did not supply one, the report says
so and flags it.

IMPORTANT: AS 5216:2021 is a paid standard and its clause text is not
reproduced here.  Clause numbers are therefore given only where they could be
confirmed from public sources (Appendix D - post-installed reinforcing bars;
Appendix F - seismic).  Reviewers must cross-check against their licensed copy;
the report prints every factor used so this is a quick table-vs-table check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Factor:
    symbol: str
    value: float
    description: str
    basis: str                      # where it comes from
    verified: bool = False          # cross-checked against a published design table
    note: str = ""


@dataclass
class DesignBasis:
    """Mutable so the engineer can override any factor in the UI / a YAML file."""

    # concrete cone / pull-out
    k_cr: float = 7.7               # N0Rk,c = k sqrt(f'c) hef^1.5  (cracked)
    k_ucr: float = 11.0             # (uncracked)
    k_g_cr: float = 2.3             # group factor constant in psi0_g,Np
    k_g_ucr: float = 3.2
    # shear
    k9_cr: float = 1.7              # V0Rk,c edge factor (cracked)
    k9_ucr: float = 2.4
    k8_deep: float = 2.0            # pry-out, hef >= 60 mm
    k8_shallow: float = 1.0
    k6_low: float = 0.6             # V0Rk,s = k6 As fuk  (fuk <= 500 MPa)
    k6_high: float = 0.5
    k7_ductile: float = 1.0
    k7_brittle: float = 0.8
    # partial factors -> phi = 1/gamma
    gamma_c: float = 1.5            # concrete-related failure (before gamma_inst)
    gamma_inst_default: float = 1.4  # used ONLY if the assessment gives none (conservative)
    # detailing defaults (product assessment governs)
    smin_factor: float = 5.0        # s_min = smin_factor * d
    cmin_factor: float = 5.0
    hef_min_abs: float = 60.0
    hef_min_factor: float = 4.0
    hef_max_factor: float = 20.0
    # AS 3600 reinforcement
    phi_rebar_tension: float = 0.8  # N* <= phi fsy As  (AS 3600 axial tension)
    # interaction exponents
    alpha_steel: float = 2.0
    alpha_concrete: float = 1.5
    interaction_linear_limit: float = 1.2
    # hole condition / sustained / flooded reductions come from the product spec
    overrides: Dict[str, str] = field(default_factory=dict)

    # ---- derived -----------------------------------------------------
    def phi_steel_tension(self, f_yk: float, f_uk: float) -> float:
        gamma = max(1.2 * f_uk / f_yk, 1.4)
        return 1.0 / gamma

    def phi_steel_shear(self, f_yk: float, f_uk: float) -> float:
        if f_uk <= 800.0 and f_yk / f_uk <= 0.8:
            gamma = max(1.0 * f_uk / f_yk, 1.25)
        else:
            gamma = 1.5
        return 1.0 / gamma

    def phi_concrete(self, gamma_inst: Optional[float]) -> float:
        gi = gamma_inst if gamma_inst is not None else self.gamma_inst_default
        return 1.0 / (self.gamma_c * gi)

    def factors(self) -> List[Factor]:
        """Human-readable list for the report appendix."""
        return [
            Factor("k_cr", self.k_cr, "Concrete cone factor, cracked concrete",
                   "EN 1992-4 / EAD 330499 methodology adopted by AS 5216:2021",
                   True, "Reproduces Ramset SARB cone tables (cracked = 0.70 x uncracked)"),
            Factor("k_ucr", self.k_ucr, "Concrete cone factor, uncracked concrete",
                   "as above", True,
                   "11.0*sqrt(32)*70^1.5/1.5 = 24.3 kN = Ramset SARB Table 2a at hef=70"),
            Factor("k9_ucr", self.k9_ucr, "Edge-shear factor, uncracked", "as above", True,
                   "Reproduces Ramset SARB Table 4a-1 (M10/M12/M16 at minimum edge)"),
            Factor("k9_cr", self.k9_cr, "Edge-shear factor, cracked (=0.7 x uncracked)",
                   "as above", True),
            Factor("k8", self.k8_deep, "Pry-out factor (hef >= 60 mm); 1.0 if hef < 60 mm",
                   "as above", True, "Reproduces Ramset SARB Table 4e (M10 h=90: 70.8 kN)"),
            Factor("gamma_c", self.gamma_c, "Partial factor, concrete-related failure modes",
                   "phi = 1/(gamma_c*gamma_inst); Ramset SARB uses phi = 1/1.5 = 0.67",
                   True),
            Factor("gamma_inst", self.gamma_inst_default,
                   "Installation safety factor - DEFAULT used only when the product assessment "
                   "gives none", "Conservative upper bound of EAD 330499 categories (1.0/1.2/1.4)",
                   False, "Replace with the value in the product's ETA/assessment"),
            Factor("phi_Ms,N", 1 / 1.5, "Steel tension, grade 5.8/8.8 (gamma_Ms = 1.2 fuk/fyk >= 1.4)",
                   "EN 1992-4; Ramset SARB uses gamma_Ms = 1.5 (5.8/8.8), 1.87 (A4-70)", True),
            Factor("phi_Ms,V", 1 / 1.25, "Steel shear, 5.8/8.8 (gamma_Ms,V = fuk/fyk >= 1.25)",
                   "EN 1992-4", False,
                   "Ramset tabulates 0.67 for shear; this tool uses the EN value - confirm vs AS 5216"),
            Factor("phi_rebar", self.phi_rebar_tension, "Reinforcement in axial tension",
                   "AS 3600; used in Ramset SARB worked example (N* <= 0.8 fsy Ab)", True),
            Factor("alpha_N+V", self.alpha_concrete,
                   "Interaction exponent concrete modes (steel: 2.0); linear alternative 1.2",
                   "EN 1992-4 7.3 / Ramset SARB 'N*/phiNur + V*/phiVur <= 1.2'", True),
        ]
