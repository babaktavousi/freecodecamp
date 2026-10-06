"""Shared data models.

Units everywhere: forces in kN, moments in kN.m, lengths in mm, stresses in
MPa (N/mm2) unless a field name says otherwise.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# Provenance - every extracted / looked-up value says where it came from
# --------------------------------------------------------------------------


@dataclass
class Provenance:
    """Where a value came from.  Reports print this next to the value."""

    source: str = "unknown"      # "drawing", "attached TDS", "web", "built-in", "user"
    document: str = ""            # file name or URL
    page: Optional[int] = None
    quote: str = ""               # literal text the value was read from
    confidence: float = 0.5       # 0..1 (heuristic)
    note: str = ""

    def short(self) -> str:
        bits = [self.source]
        if self.document:
            bits.append(self.document)
        if self.page:
            bits.append(f"p.{self.page}")
        return " | ".join(bits)


@dataclass
class Sourced:
    """A value with provenance."""

    value: Any
    prov: Provenance = field(default_factory=Provenance)


# --------------------------------------------------------------------------
# Adhesive (chemical anchor) product specification
# --------------------------------------------------------------------------


@dataclass
class BondEntry:
    """Characteristic bond resistance for one bar/rod size (EAD 330499 style)."""

    d: float                              # nominal diameter, mm
    fastener: str = "rod"                 # "rod" (threaded rod / stud) | "rebar"
    tau_cr: Optional[float] = None        # tau_Rk,cr  (MPa)  cracked concrete
    tau_ucr: Optional[float] = None       # tau_Rk,ucr (MPa)  uncracked concrete
    condition: str = "dry_wet"            # "dry_wet" | "flooded"
    temp_range: str = "default"           # label as given by the TDS/ETA, e.g. "I: 40C/24C"
    temp_key: str = ""                    # normalised "40/24" (long-term/short-term max temperature)
    drilling: str = "hammer"              # "hammer" | "diamond" | "diamond_roughened"
    service_life: int = 50                # years the value applies to (50 or 100)
    d0: Optional[float] = None            # drill hole diameter, mm
    hef_min: Optional[float] = None
    hef_max: Optional[float] = None
    s_min: Optional[float] = None
    c_min: Optional[float] = None
    h_min: Optional[float] = None
    n_rk_s: Optional[float] = None        # kN, steel tension from ETA (if product-specific)
    v_rk_s: Optional[float] = None        # kN, steel shear from ETA (if product-specific)
    prov: Provenance = field(default_factory=Provenance)


@dataclass
class AdhesiveSpec:
    name: str
    manufacturer: str = ""
    chemistry: str = ""                   # epoxy / vinylester / hybrid / polyester / cementitious
    bond: List[BondEntry] = field(default_factory=list)
    psi_c: Dict[float, float] = field(default_factory=dict)  # f'c (MPa) -> factor vs C20/25
    psi_sus0: Optional[float] = None      # sustained-load factor (lowest found = conservative)
    psi_sus0_map: Dict[str, float] = field(default_factory=dict)   # by temp_key "40/24"
    gamma_inst: Optional[float] = None    # installation safety factor (hammer, dry/wet)
    gamma_inst_map: Dict[str, float] = field(default_factory=dict) # "hammer" | "diamond" | "flooded"
    k_cr: Optional[float] = None          # cone factor override (cracked)
    k_ucr: Optional[float] = None
    c_cr_sp: Optional[float] = None       # splitting edge distance override (mm)
    s_cr_sp: Optional[float] = None
    c_cr_sp_rule_en: Optional[bool] = None  # ETA states the EN rule 1.0hef / 4.6hef-1.8h / 2.26hef
    # Post-installed rebar (EAD 330087 / "ETA-Rebar") design bond strength, MPa by f'c
    f_bd: Dict[float, float] = field(default_factory=dict)
    # Qualification / approvals
    eta: List[str] = field(default_factory=list)          # e.g. "ETA-25/0648"
    eads: List[str] = field(default_factory=list)         # e.g. "EAD 330499"
    seismic: List[str] = field(default_factory=list)      # "C1", "C2"
    as5216_prequalified: Optional[bool] = None
    rebar_qualified: Optional[bool] = None                # EAD 330087 / TR 023
    cracked_concrete_ok: Optional[bool] = None
    overhead_ok: Optional[bool] = None
    flooded_hole_ok: Optional[bool] = None
    diamond_drilling_ok: Optional[bool] = None
    fire_data: Optional[bool] = None
    service_temp: Optional[tuple] = None                  # (min C, max C)
    install_temp: Optional[tuple] = None
    working_life_years: Optional[int] = None
    cure_notes: str = ""
    website: str = ""
    docs: List[Dict[str, str]] = field(default_factory=list)  # {"title","url"|"file","kind"}
    verified: bool = False                # engineer confirmed the numbers against the source
    is_demo: bool = False                 # placeholder - never issuable
    provenance: Dict[str, Provenance] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Design case - one anchor group / dowel group to be checked
# --------------------------------------------------------------------------


@dataclass
class Concrete:
    fc: float = 32.0                      # f'c, MPa (cylinder, characteristic)
    thickness: Optional[float] = None     # member thickness h, mm
    cracked: bool = True                  # conservative default
    dense_reinforcement: bool = False     # reinforcement spacing <150 mm (psi_re,N)
    edge_reinforcement: bool = False      # supplementary reinforcement at edge (shear)
    stated_on_drawing: bool = False


@dataclass
class Layout:
    n_x: int = 1                          # anchors in the x direction
    n_y: int = 1
    s_x: float = 0.0                      # spacing mm
    s_y: float = 0.0
    # distance from the *outer* anchor to the free edge, mm (None = remote)
    c_xneg: Optional[float] = None
    c_xpos: Optional[float] = None
    c_yneg: Optional[float] = None
    c_ypos: Optional[float] = None

    @property
    def n(self) -> int:
        return max(1, self.n_x) * max(1, self.n_y)


@dataclass
class Fixture:
    """Attachment (base plate etc.)."""

    thickness: float = 0.0                # plate thickness, mm
    standoff: float = 0.0                 # gap between plate and concrete, mm
    grouted: bool = True                  # mortar/grout pad under plate
    hole_df: Optional[float] = None       # clearance hole diameter, mm
    restraint: str = "free"               # "free" | "fixed"  (alpha_M = 1 | 2)
    rigid: bool = True                    # assumed rigid for load distribution


@dataclass
class LoadCombo:
    """ULS design action on the *whole group* (not per anchor) unless basis says otherwise."""

    name: str = "ULS"
    N: float = 0.0                        # kN, +ve = tension
    Vx: float = 0.0                       # kN
    Vy: float = 0.0
    Mx: float = 0.0                       # kN.m  (about x axis -> tension on +y side)
    My: float = 0.0                       # kN.m  (about y axis -> tension on +x side)
    sustained_fraction: float = 0.0       # share of N that is permanent (0..1)
    basis: str = "group"                  # "group" | "per_anchor" | "per_metre"
    limit_state: str = "ULS"              # "ULS" | "SLS" | "unknown"
    prov: Provenance = field(default_factory=Provenance)


@dataclass
class DesignCase:
    id: str = "A1"
    title: str = "Chemical anchor group"
    kind: str = "anchor"                  # "anchor" (threaded rod/stud) | "rebar" (post-installed bar)
    page: Optional[int] = None
    source: str = ""                      # drawing file the case was read from
    fastener_d: float = 16.0              # nominal diameter, mm
    grade: str = "8.8"                    # rod grade, or "500N" for rebar
    h_ef: float = 125.0                   # effective embedment, mm
    d0: Optional[float] = None            # hole diameter
    layout: Layout = field(default_factory=Layout)
    concrete: Concrete = field(default_factory=Concrete)
    fixture: Fixture = field(default_factory=Fixture)
    loads: List[LoadCombo] = field(default_factory=list)
    adhesive_name: str = ""
    hole_condition: str = "dry_wet"       # "dry_wet" | "flooded"
    drilling: str = "hammer"              # "hammer" | "diamond" | "compressed_air"
    temp_range: str = "default"
    overhead: bool = False
    seismic: bool = False
    fire: bool = False
    fatigue: bool = False
    shear_to_all_anchors: bool = True     # steel / pry-out; edge failure uses front row
    rebar_clear_cover: Optional[float] = None   # rebar method: cover to bar, mm
    rebar_clear_spacing: Optional[float] = None
    rebar_k1_top_bar: bool = False        # >300 mm concrete below bar (AS 3600 k1=1.3)
    notes: List[str] = field(default_factory=list)
    prov: Dict[str, Provenance] = field(default_factory=dict)  # field -> provenance


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass
class Step:
    """One line of a worked calculation."""

    symbol: str
    description: str
    value: Any
    unit: str = ""
    formula: str = ""


@dataclass
class CheckResult:
    key: str
    title: str
    mode: str                              # tension | shear | interaction | detailing | rebar
    clause: str = ""
    steps: List[Step] = field(default_factory=list)
    R_k: Optional[float] = None            # kN
    phi: Optional[float] = None
    R_d: Optional[float] = None            # kN
    E_d: Optional[float] = None            # kN
    utilisation: Optional[float] = None
    status: str = "NA"                     # PASS | FAIL | NA | INCOMPLETE | WARN
    notes: List[str] = field(default_factory=list)


@dataclass
class ComboResult:
    combo: LoadCombo
    checks: List[CheckResult] = field(default_factory=list)
    anchor_forces: List[Dict[str, float]] = field(default_factory=list)
    max_utilisation: Optional[float] = None
    governing: str = ""
    status: str = "NA"


@dataclass
class CaseResult:
    case: DesignCase
    spec: Optional[AdhesiveSpec]
    combos: List[ComboResult] = field(default_factory=list)
    detailing: List[CheckResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    max_utilisation: Optional[float] = None
    governing: str = ""
    status: str = "NA"                     # PASS | FAIL | INCOMPLETE
    basis_table: List[Step] = field(default_factory=list)
    spec_provenance: List[str] = field(default_factory=list)
    alt_method: Optional["CaseResult"] = None   # rebar: second method for comparison
