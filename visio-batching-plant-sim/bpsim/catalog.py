"""Equipment catalogue for a concrete / cement batching plant.

Every equipment type has: a recognisable ID prefix, a role in the material flow,
the material family it handles, and the parameters the user is asked for in the
"Equipment data" dialog (with sensible defaults, units and what-if direction).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    unit: str
    default: float
    minimum: float = 0.0
    kind: str = "rate"      # rate | capacity | time | count  (used by what-if analysis)
    better: str = "high"    # high | low | none  (direction of an upgrade)
    help: str = ""


@dataclass(frozen=True)
class TypeSpec:
    key: str
    label: str
    prefix: str
    role: str                # source | transfer | hopper | mixer | buffer | sink | control | aux
    family: str | None       # aggregate | cement | water | admixture | None
    params: tuple[Param, ...]


UNITS = Param("units", "Parallel identical units", "-", 1, 1, "count", "high",
              "Number of identical machines working in parallel (e.g. 2 mixers).")


def _p(*extra: Param, units: bool = True) -> tuple[Param, ...]:
    return tuple(extra) + ((UNITS,) if units else ())


TYPES: dict[str, TypeSpec] = {t.key: t for t in [
    TypeSpec("aggregate_bin", "Aggregate bin", "AGB", "source", "aggregate", _p(
        Param("rate", "Feeder / gate discharge rate", "t/h", 120, 0.1, "rate"),
        Param("capacity", "Storage capacity", "t", 40, 0.1, "capacity", "none"))),
    TypeSpec("cement_silo", "Cement silo", "CSL", "source", "cement", _p(
        Param("rate", "Silo outlet / rotary valve rate", "t/h", 80, 0.1, "rate"),
        Param("capacity", "Storage capacity", "t", 100, 0.1, "capacity", "none"))),
    TypeSpec("water_tank", "Water tank", "WTK", "source", "water", _p(
        Param("rate", "Outlet rate (1 m3 = 1 t)", "t/h", 60, 0.1, "rate"),
        Param("capacity", "Storage capacity", "t", 20, 0.1, "capacity", "none"))),
    TypeSpec("admixture_tank", "Admixture tank", "ADT", "source", "admixture", _p(
        Param("rate", "Outlet rate", "t/h", 2, 0.01, "rate"),
        Param("capacity", "Storage capacity", "t", 1, 0.01, "capacity", "none"))),
    TypeSpec("belt_conveyor", "Belt conveyor", "CNV", "transfer", "aggregate", _p(
        Param("rate", "Conveying capacity", "t/h", 250, 0.1, "rate"))),
    TypeSpec("screw_conveyor", "Screw conveyor", "SCW", "transfer", "cement", _p(
        Param("rate", "Conveying capacity", "t/h", 80, 0.1, "rate"))),
    TypeSpec("pump", "Pump / dosing pump", "PMP", "transfer", None, _p(
        Param("rate", "Pumping capacity", "t/h", 50, 0.01, "rate"))),
    TypeSpec("weigh_hopper", "Weigh hopper", "WGH", "hopper", None, _p(
        Param("capacity", "Hopper capacity", "kg", 3000, 1, "capacity"),
        Param("settle_time_s", "Weighing / settling time per material", "s", 5, 0, "time", "low"),
        Param("dump_time_s", "Discharge (dump) time", "s", 8, 0, "time", "low"))),
    TypeSpec("mixer", "Mixer", "MIX", "mixer", None, _p(
        Param("capacity", "Batch size (concrete)", "m3", 2.0, 0.05, "capacity"),
        Param("mix_time_s", "Mixing time", "s", 45, 1, "time", "low"),
        Param("discharge_time_s", "Discharge time", "s", 20, 0, "time", "low"))),
    TypeSpec("discharge_hopper", "Discharge / holding hopper", "DCH", "buffer", None, _p(
        Param("capacity", "Hopper capacity", "m3", 3, 0.05, "capacity"),
        Param("rate", "Discharge rate", "m3/h", 120, 0.1, "rate"))),
    TypeSpec("loadout", "Truck loadout / transit-mixer bay", "TRK", "sink", None, _p(
        Param("rate", "Loading rate per bay", "m3/h", 80, 0.1, "rate"))),
    TypeSpec("controller", "Batching controller (PLC)", "CTL", "control", None, ()),
    TypeSpec("dust_collector", "Dust collector / filter", "DCL", "aux", None, ()),
]}

REQUIRED_TYPES = ["aggregate_bin", "cement_silo", "water_tank", "mixer", "loadout", "controller"]
FAMILY_ORDER = ["cement", "aggregate", "water", "admixture"]
SOURCE_FOR_FAMILY = {"cement": "cement_silo", "aggregate": "aggregate_bin",
                     "water": "water_tank", "admixture": "admixture_tank"}

# Which equipment may feed which (material-flow edges only).
ALLOWED: dict[str, set[str]] = {
    "aggregate_bin": {"belt_conveyor", "weigh_hopper"},
    "cement_silo": {"screw_conveyor", "weigh_hopper"},
    "water_tank": {"pump", "weigh_hopper"},
    "admixture_tank": {"pump", "weigh_hopper"},
    "belt_conveyor": {"belt_conveyor", "weigh_hopper", "mixer"},
    "screw_conveyor": {"screw_conveyor", "weigh_hopper", "mixer"},
    "pump": {"pump", "weigh_hopper", "mixer"},
    "weigh_hopper": {"mixer", "belt_conveyor", "screw_conveyor", "pump"},
    "mixer": {"discharge_hopper", "loadout"},
    "discharge_hopper": {"loadout"},
    "loadout": set(),
}

DEFAULT_RECIPE = {"cement": 350.0, "sand": 800.0, "gravel": 1000.0,
                  "aggregate": 1800.0, "water": 175.0, "admixture": 3.5}   # kg per m3
HOPPER_DEFAULT_KG = {"aggregate": 3500, "cement": 800, "water": 500, "admixture": 20}

_PREFIX = {s.prefix: s.key for s in TYPES.values()}
_PREFIX.update({"BIN": "aggregate_bin", "AGG": "aggregate_bin", "SIL": "cement_silo", "SILO": "cement_silo",
                "TK": "water_tank", "TNK": "water_tank", "BLT": "belt_conveyor", "CV": "belt_conveyor",
                "SC": "screw_conveyor", "P": "pump", "PU": "pump", "HOP": "weigh_hopper",
                "WH": "weigh_hopper", "M": "mixer", "MX": "mixer", "TR": "loadout", "LO": "loadout",
                "PLC": "controller", "DC": "dust_collector", "BH": "dust_collector"})

_KEYWORDS = [  # order matters: specific before generic
    ("discharge_hopper", r"discharge|holding|surge|buffer"),
    ("admixture_tank", r"admix|additive|plasticis|plasticiz|retarder"),
    ("water_tank", r"water"),
    ("cement_silo", r"cement|silo|fly ?ash|\bgbfs\b"),
    ("screw_conveyor", r"screw|auger"),
    ("belt_conveyor", r"belt|conveyor"),
    ("pump", r"pump|doser|dosing"),
    ("weigh_hopper", r"weigh|scale|batcher|hopper"),
    ("mixer", r"mixer|pan mix|twin.?shaft|planetary"),
    ("loadout", r"truck|loadout|load-out|load out|transit|agitator|bay"),
    ("controller", r"control|plc|scada|panel|hmi"),
    ("dust_collector", r"dust|filter|baghouse|bag house"),
    ("aggregate_bin", r"aggregate|\bbin\b|sand|gravel|stone|crushed|rock|chips"),
]


def normalise_type(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"[\s\-]+", "_", text.strip().lower())
    if t in TYPES:
        return t
    for spec in TYPES.values():
        if t == re.sub(r"[\s\-/]+", "_", spec.label.lower()):
            return spec.key
    return None


def infer_type(explicit: str | None = None, ident: str | None = None, *names: str | None) -> str | None:
    """Explicit EquipType property > ID prefix > keywords in labels/master names."""
    t = normalise_type(explicit)
    if t:
        return t
    if ident:
        m = re.match(r"\s*([A-Za-z]+)", ident)
        if m and m.group(1).upper() in _PREFIX:
            return _PREFIX[m.group(1).upper()]
    for name in (ident, *names):
        if not name:
            continue
        low = name.lower()
        for key, rx in _KEYWORDS:
            if re.search(rx, low):
                return key
    return None


def infer_material(spec_key: str, *texts: str | None) -> str | None:
    fam = TYPES[spec_key].family
    if spec_key != "aggregate_bin":
        return fam if TYPES[spec_key].role == "source" else None
    low = " ".join(t.lower() for t in texts if t)
    if "sand" in low or "fine" in low:
        return "sand"
    if re.search(r"gravel|stone|coarse|crushed|rock|chip|\d+\s?mm", low):
        return "gravel"
    return "aggregate"


def family_of_material(material: str | None) -> str | None:
    if material in ("sand", "gravel", "aggregate"):
        return "aggregate"
    return material if material in ("cement", "water", "admixture") else None


ID_RX = re.compile(r"^\s*([A-Za-z]{1,4})[\-_ ]?(\d{1,3}[A-Za-z]?)\b")
