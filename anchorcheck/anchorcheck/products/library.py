"""Persistent library of confirmed adhesive specifications (JSON in the data directory)."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, fields
from pathlib import Path
from typing import Dict, List, Optional

from ..config import data_dir
from ..models import AdhesiveSpec, BondEntry, Provenance


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "spec"


def spec_to_dict(spec: AdhesiveSpec) -> dict:
    d = asdict(spec)
    for k in ("psi_c", "f_bd"):
        d[k] = {str(a): b for a, b in d[k].items()}
    return d


def _prov(d: Optional[dict]) -> Provenance:
    if not d:
        return Provenance()
    return Provenance(**{k: v for k, v in d.items() if k in {f.name for f in fields(Provenance)}})


def spec_from_dict(d: dict) -> AdhesiveSpec:
    d = dict(d)
    bond = []
    for b in d.pop("bond", []):
        b = dict(b)
        pv = _prov(b.pop("prov", None))
        bond.append(BondEntry(prov=pv, **{k: v for k, v in b.items() if k in {f.name for f in fields(BondEntry)}}))
    prov = {k: _prov(v) for k, v in (d.pop("provenance", {}) or {}).items()}
    for k in ("psi_c", "f_bd"):
        d[k] = {float(a): float(b) for a, b in (d.get(k) or {}).items()}
    for k in ("service_temp", "install_temp"):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
    valid = {f.name for f in fields(AdhesiveSpec)}
    return AdhesiveSpec(bond=bond, provenance=prov, **{k: v for k, v in d.items() if k in valid})


def specs_dir() -> Path:
    p = data_dir() / "specs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_spec(spec: AdhesiveSpec, directory: Optional[Path] = None) -> Path:
    p = (directory or specs_dir()) / (_slug(spec.name) + ".json")
    p.write_text(json.dumps(spec_to_dict(spec), indent=1, default=str))
    return p


def load_specs(directory: Optional[Path] = None) -> Dict[str, AdhesiveSpec]:
    out: Dict[str, AdhesiveSpec] = {}
    for p in sorted((directory or specs_dir()).glob("*.json")):
        try:
            s = spec_from_dict(json.loads(p.read_text()))
            out[s.name] = s
        except Exception:
            continue
    return out


def merge_specs(base: AdhesiveSpec, extra: AdhesiveSpec) -> AdhesiveSpec:
    """Fill gaps in `base` from `extra` (never overwrites existing values)."""
    for f in fields(AdhesiveSpec):
        if f.name in ("bond", "docs", "notes", "eta", "eads", "seismic", "provenance"):
            continue
        bv, ev = getattr(base, f.name), getattr(extra, f.name)
        empty = bv in (None, "", {}, [], ())
        if empty and ev not in (None, "", {}, [], ()):
            setattr(base, f.name, ev)
    existing = {(b.fastener, b.d, b.condition, b.drilling, b.temp_key or b.temp_range, b.service_life) for b in base.bond}
    for b in extra.bond:
        if (b.fastener, b.d, b.condition, b.drilling, b.temp_key or b.temp_range, b.service_life) not in existing:
            base.bond.append(b)
    for lst in ("eta", "eads", "seismic", "docs", "notes"):
        cur = getattr(base, lst)
        for x in getattr(extra, lst):
            if x not in cur:
                cur.append(x)
    for k, v in extra.provenance.items():
        base.provenance.setdefault(k, v)
    return base
