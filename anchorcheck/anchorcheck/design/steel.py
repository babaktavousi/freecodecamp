"""Fastener steel properties (threaded rod / stud grades, deformed reinforcing bar)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ISO 898-1 / ISO 3506 characteristic strengths (MPa): (f_yk, f_uk)
ROD_GRADES: Dict[str, Tuple[float, float]] = {
    "4.6": (240.0, 400.0),
    "4.8": (320.0, 400.0),
    "5.6": (300.0, 500.0),
    "5.8": (400.0, 500.0),
    "6.8": (480.0, 600.0),
    "8.8": (640.0, 800.0),
    "10.9": (900.0, 1000.0),
    "A2-70": (450.0, 700.0),
    "A4-70": (450.0, 700.0),
    "A4-80": (600.0, 800.0),
}

# ISO metric coarse thread tensile stress area As (mm2)
STRESS_AREA: Dict[int, float] = {
    6: 20.1, 8: 36.6, 10: 58.0, 12: 84.3, 14: 115.0, 16: 157.0, 18: 192.0,
    20: 245.0, 22: 303.0, 24: 353.0, 27: 459.0, 30: 561.0, 33: 694.0,
    36: 817.0, 39: 976.0,
}

# Typical drill-hole diameters (mm).  PRODUCT-SPECIFIC: the adhesive's
# assessment/TDS governs - these are only used if the TDS value is absent.
ROD_D0: Dict[int, float] = {8: 10, 10: 12, 12: 14, 16: 18, 20: 22, 24: 28, 27: 30, 30: 35}
BAR_D0: Dict[int, float] = {10: 12, 12: 15, 16: 20, 20: 25, 24: 30, 25: 30, 28: 35, 32: 40,
                            36: 45, 40: 50}

# AS/NZS 4671 reinforcing steel
BAR_GRADES: Dict[str, Tuple[float, float]] = {
    "500N": (500.0, 540.0),   # f_sy, f_su = 1.08 f_sy (minimum Rm/Re,k for class N)
    "500L": (500.0, 515.0),
    "500E": (500.0, 575.0),
    "250N": (250.0, 270.0),
    "R250N": (250.0, 270.0),
}


@dataclass
class SteelProps:
    d: float
    grade: str
    f_yk: float
    f_uk: float
    A_s: float          # tensile stress area (rod) or nominal area (bar), mm2
    d_s: float          # equivalent shank diameter, mm
    W_el: float         # elastic section modulus, mm3 (from stress-area diameter)
    ductile: bool = True


def normalise_grade(g: str) -> str:
    g = (g or "").strip().upper().replace("GRADE", "").replace("GR", "").replace(" ", "")
    g = g.replace("CLASS", "").replace(",", ".")
    aliases = {
        "316": "A4-70", "A4": "A4-70", "SS316": "A4-70", "304": "A2-70", "A2": "A2-70",
        "88": "8.8", "58": "5.8", "46": "4.6", "109": "10.9",
        "D500N": "500N", "N": "500N", "500": "500N", "D500L": "500L", "D500E": "500E",
    }
    return aliases.get(g, g)


def is_rebar_grade(g: str) -> bool:
    return normalise_grade(g) in BAR_GRADES


def rod_props(d: float, grade: str) -> SteelProps:
    g = normalise_grade(grade)
    if g not in ROD_GRADES:
        raise ValueError(f"Unknown threaded-rod steel grade '{grade}'. Known: {sorted(ROD_GRADES)}")
    f_yk, f_uk = ROD_GRADES[g]
    di = int(round(d))
    A_s = STRESS_AREA.get(di)
    if A_s is None:  # fall back: ~0.78 * nominal area
        A_s = 0.78 * math.pi * d * d / 4.0
    d_s = math.sqrt(4.0 * A_s / math.pi)
    W = math.pi * d_s ** 3 / 32.0
    return SteelProps(d=d, grade=g, f_yk=f_yk, f_uk=f_uk, A_s=A_s, d_s=d_s, W_el=W)


def bar_props(d: float, grade: str = "500N") -> SteelProps:
    g = normalise_grade(grade)
    if g not in BAR_GRADES:
        raise ValueError(f"Unknown reinforcing steel grade '{grade}'. Known: {sorted(BAR_GRADES)}")
    f_sy, f_su = BAR_GRADES[g]
    A = math.pi * d * d / 4.0
    W = math.pi * d ** 3 / 32.0
    return SteelProps(d=d, grade=g, f_yk=f_sy, f_uk=f_su, A_s=A, d_s=d, W_el=W)


def fastener_props(d: float, grade: str) -> SteelProps:
    return bar_props(d, grade) if is_rebar_grade(grade) else rod_props(d, grade)


def default_d0(d: float, grade: str) -> float:
    di = int(round(d))
    table = BAR_D0 if is_rebar_grade(grade) else ROD_D0
    return float(table.get(di, d + 4 if d <= 12 else d + 6))
