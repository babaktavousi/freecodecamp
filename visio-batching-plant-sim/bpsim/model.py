"""In-memory plant model: equipment, connections, recipe, (de)serialisation."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field

from . import catalog as C


@dataclass
class Equipment:
    id: str
    type: str | None
    label: str = ""
    material: str | None = None
    params: dict = field(default_factory=dict)
    finalized: bool = False
    shape: dict = field(default_factory=dict)     # Visio back-reference (shape id, page, x, y)

    @property
    def spec(self) -> C.TypeSpec | None:
        return C.TYPES.get(self.type) if self.type else None

    def p(self, key: str, default: float | None = None) -> float:
        v = self.params.get(key, default)
        return float(v) if v is not None else 0.0


@dataclass
class Connection:
    src: str
    dst: str
    kind: str = "flow"        # flow | control

    def key(self) -> tuple[str, str]:
        return (self.src, self.dst)


CONTROL_TYPES = {"controller", "dust_collector"}


class PlantModel:
    def __init__(self) -> None:
        self.equipment: dict[str, Equipment] = {}
        self.connections: list[Connection] = []
        self.recipe: dict[str, float] = {}
        self.read_issues: list[dict] = []     # problems found while reading the drawing
        self.ignored: list[str] = []          # shapes that were not recognised as equipment
        self.name = "Batching plant"

    # ---- editing -------------------------------------------------------
    def next_id(self, type_key: str) -> str:
        prefix = C.TYPES[type_key].prefix
        n = 1
        while f"{prefix}-{n:02d}" in self.equipment:
            n += 1
        return f"{prefix}-{n:02d}"

    def add_equipment(self, type_key: str | None, ident: str | None = None, label: str = "",
                      material: str | None = None, params: dict | None = None) -> Equipment:
        ident = ident or self.next_id(type_key)
        if ident in self.equipment:
            raise ValueError(f"ID {ident} already exists")
        if type_key and material is None:
            material = C.infer_material(type_key, label, ident)
        eq = Equipment(ident, type_key, label or (C.TYPES[type_key].label if type_key else ident),
                       material, dict(params or {}))
        self.equipment[ident] = eq
        return eq

    def remove_equipment(self, ident: str) -> None:
        self.equipment.pop(ident, None)
        self.connections = [c for c in self.connections if ident not in (c.src, c.dst)]

    def conn_kind(self, a: str, b: str) -> str:
        ta, tb = self.equipment[a].type, self.equipment[b].type
        return "control" if (ta in CONTROL_TYPES or tb in CONTROL_TYPES) else "flow"

    def has_connection(self, a: str, b: str) -> bool:
        return any(c.src == a and c.dst == b for c in self.connections)

    def add_connection(self, a: str, b: str, kind: str | None = None) -> bool:
        if a not in self.equipment or b not in self.equipment:
            raise ValueError(f"unknown equipment in connection {a}->{b}")
        if a == b or self.has_connection(a, b):
            return False
        self.connections.append(Connection(a, b, kind or self.conn_kind(a, b)))
        return True

    def remove_connection(self, a: str, b: str) -> bool:
        n = len(self.connections)
        self.connections = [c for c in self.connections if c.key() != (a, b)]
        return len(self.connections) != n

    def rename(self, old: str, new: str) -> None:
        if new in self.equipment:
            raise ValueError(f"ID {new} already exists")
        eq = self.equipment.pop(old)
        eq.id = new
        self.equipment[new] = eq
        for c in self.connections:
            c.src = new if c.src == old else c.src
            c.dst = new if c.dst == old else c.dst

    # ---- graph ---------------------------------------------------------
    def flow_edges(self) -> list[Connection]:
        return [c for c in self.connections if c.kind == "flow"]

    def succ(self, ident: str, kind: str = "flow") -> list[str]:
        return [c.dst for c in self.connections if c.src == ident and c.kind == kind]

    def pred(self, ident: str, kind: str = "flow") -> list[str]:
        return [c.src for c in self.connections if c.dst == ident and c.kind == kind]

    def ancestors(self, ident: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.pred(ident))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(self.pred(n))
        return seen

    def descendants(self, ident: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.succ(ident))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                stack.extend(self.succ(n))
        return seen

    def of_type(self, *types: str) -> list[Equipment]:
        return [e for e in self.equipment.values() if e.type in types]

    def families_upstream(self, ident: str) -> set[str]:
        out = set()
        for a in self.ancestors(ident) | {ident}:
            e = self.equipment[a]
            if e.spec and e.spec.role == "source":
                out.add(C.family_of_material(e.material) or e.spec.family)
        return out

    # ---- recipe --------------------------------------------------------
    def effective_recipe(self) -> dict[str, float]:
        """Recipe (kg/m3) restricted to the materials that have a source in the plant."""
        have = {e.material for e in self.of_type("aggregate_bin", "cement_silo", "water_tank", "admixture_tank")}
        r = {**C.DEFAULT_RECIPE, **self.recipe}
        out = {m: r[m] for m in ("cement", "water", "admixture") if m in have}
        total = r["aggregate"]
        fine, coarse, generic = "sand" in have, "gravel" in have, "aggregate" in have
        if fine and coarse:
            out["sand"], out["gravel"] = r["sand"], r["gravel"]
            if generic:
                out["aggregate"] = max(total - r["sand"] - r["gravel"], 0.1 * total)
        elif fine or coarse:
            part = r["sand"] if fine else r["gravel"]
            key = "sand" if fine else "gravel"
            if generic:
                out[key] = part
                out["aggregate"] = max(total - part, 0.1 * total)
            else:
                out[key] = total
        elif generic:
            out["aggregate"] = total
        return {k: v for k, v in out.items() if v > 0}

    # ---- misc ----------------------------------------------------------
    def copy(self) -> "PlantModel":
        return copy.deepcopy(self)

    def signature(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "equipment": [{"id": e.id, "type": e.type, "label": e.label, "material": e.material,
                           "params": e.params, "finalized": e.finalized}
                          for e in self.equipment.values()],
            "connections": [{"from": c.src, "to": c.dst, "kind": c.kind} for c in self.connections],
            "recipe": self.recipe,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlantModel":
        m = cls()
        m.name = d.get("name", m.name)
        for e in d.get("equipment", []):
            t = C.normalise_type(e.get("type")) or C.infer_type(None, e.get("id"), e.get("label"))
            eq = m.add_equipment(t, e["id"], e.get("label", ""), e.get("material"), e.get("params"))
            eq.finalized = bool(e.get("finalized", False))
        for c in d.get("connections", []):
            m.add_connection(c["from"], c["to"], c.get("kind"))
        m.recipe = dict(d.get("recipe", {}))
        return m

    @classmethod
    def load(cls, path: str) -> "PlantModel":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
