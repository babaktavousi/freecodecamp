"""Equipment performance data: defaults, validation, completeness (backs the 'Equipment data' dialog)."""
from __future__ import annotations

from . import catalog as C
from .model import Equipment, PlantModel


def fields_for(e: Equipment) -> tuple[C.Param, ...]:
    return e.spec.params if e.spec else ()


def suggest_defaults(m: PlantModel, e: Equipment) -> dict[str, float]:
    d = {p.key: p.default for p in fields_for(e)}
    if e.type == "weigh_hopper":
        fams = m.families_upstream(e.id)
        fam = next(iter(fams)) if len(fams) == 1 else None
        if fam:
            d["capacity"] = C.HOPPER_DEFAULT_KG[fam]
            if fam in ("cement", "water"):
                d["dump_time_s"], d["settle_time_s"] = 6, 4
            if fam == "admixture":
                d["dump_time_s"], d["settle_time_s"] = 4, 3
    if e.type == "pump":
        fams = m.families_upstream(e.id)
        d["rate"] = 2.0 if fams == {"admixture"} else 50.0
    return d


def validate(e: Equipment, values: dict) -> list[str]:
    errs = []
    for p in fields_for(e):
        v = values.get(p.key)
        try:
            f = float(v)
        except (TypeError, ValueError):
            errs.append(f"{p.label}: enter a number")
            continue
        if f < p.minimum or (p.minimum > 0 and f <= 0):
            errs.append(f"{p.label}: must be at least {p.minimum:g} {p.unit}")
        if p.key == "units" and f != int(f):
            errs.append("Parallel units must be a whole number")
    return errs


def needs_data(m: PlantModel) -> list[Equipment]:
    return [e for e in m.equipment.values() if e.spec and e.spec.params and not e.finalized]


def apply_defaults(m: PlantModel, only_missing: bool = True) -> None:
    for e in m.equipment.values():
        if not e.spec or not e.spec.params:
            e.finalized = True
            continue
        for k, v in suggest_defaults(m, e).items():
            if not only_missing or k not in e.params:
                e.params[k] = v
        e.finalized = True


def all_final(m: PlantModel) -> bool:
    return not needs_data(m)


def invalidate(m: PlantModel) -> None:
    for e in m.equipment.values():
        e.finalized = False
