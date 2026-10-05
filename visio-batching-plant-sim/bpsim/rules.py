"""Deterministic completeness / defect rules with machine-applicable fixes."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import catalog as C
from .model import CONTROL_TYPES, PlantModel


@dataclass
class Fix:
    title: str
    why: str
    actions: list[dict]
    origin: str = "rules"

    @property
    def key(self) -> str:
        return json.dumps(self.actions, sort_keys=True)

    @property
    def manual(self) -> bool:
        return all(a.get("op") == "manual" for a in self.actions)


@dataclass
class Issue:
    code: str
    severity: str                 # error | warning | info
    message: str
    ids: list[str] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)
    origin: str = "rules"


def _label(m: PlantModel, i: str) -> str:
    e = m.equipment[i]
    return f"{i} ({e.spec.label if e.spec else 'unknown type'})"


def _find_cycle(m: PlantModel):
    color: dict[str, int] = {}
    def dfs(n, path):
        color[n] = 1
        for s in m.succ(n):
            if color.get(s) == 1:
                return (n, s)
            if color.get(s) is None:
                r = dfs(s, path)
                if r:
                    return r
        color[n] = 2
        return None
    for n in list(m.equipment):
        if color.get(n) is None:
            r = dfs(n, [])
            if r:
                return r
    return None


def _hopper_claims(m: PlantModel) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """family -> hoppers fed only by that family; orphan-input hoppers; mixed hoppers."""
    by_fam: dict[str, list[str]] = {}
    free, mixed = [], []
    for h in m.of_type("weigh_hopper"):
        fams = m.families_upstream(h.id)
        if not fams:
            free.append(h.id)
        elif len(fams) == 1:
            by_fam.setdefault(next(iter(fams)), []).append(h.id)
        else:
            mixed.append(h.id)
    return by_fam, free, mixed


def verify_rules(m: PlantModel) -> list[Issue]:
    out: list[Issue] = []
    eq = m.equipment

    # 1. problems found while reading the drawing ---------------------------------
    for ri in m.read_issues:
        out.append(Issue(ri["code"], ri.get("severity", "error"), ri["message"], ri.get("ids", []),
                         [Fix("Fix in Visio", ri["message"], [{"op": "manual", "text": ri.get("hint", ri["message"])}])]))

    # 2. unknown equipment type -----------------------------------------------------
    for e in eq.values():
        if e.type is None:
            guess = C.infer_type(None, e.id, e.label)
            fixes = [Fix(f"Set type of {e.id} to {C.TYPES[guess].label}", "Guessed from the ID / label.",
                         [{"op": "set_type", "id": e.id, "type": guess}])] if guess else \
                    [Fix(f"Remove {e.id}", "Shape is not recognisable as plant equipment.",
                         [{"op": "remove_equipment", "id": e.id}])]
            out.append(Issue("UNKNOWN_TYPE", "error", f"{e.id} has no recognised equipment type.", [e.id], fixes))
    typed = {i for i, e in eq.items() if e.type}

    # 3. connection validity -------------------------------------------------------
    for c in list(m.connections):
        a, b = eq[c.src], eq[c.dst]
        if not (a.type and b.type) or c.kind == "control":
            continue
        if b.type in C.ALLOWED.get(a.type, set()):
            continue
        fixes: list[Fix] = []
        if a.type in C.ALLOWED.get(b.type, set()):
            fixes.append(Fix(f"Reverse the connection {c.src}→{c.dst}",
                             "Material flows from the supplier to the consumer, not the other way round.",
                             [{"op": "reverse_connection", "from": c.src, "to": c.dst}]))
        elif a.spec.role in ("source", "transfer") and b.type == "mixer":
            spare = next((h.id for h in m.of_type("weigh_hopper") if not m.pred(h.id) and not m.succ(h.id)), None)
            ins = {"equip_id": spare} if spare else {"type": "weigh_hopper"}
            fixes.append(Fix(f"Insert weigh hopper {spare or ''} between {c.src} and {c.dst}".replace("  ", " "),
                             "Materials must be weighed in a hopper before they are charged into the mixer.",
                             [{"op": "insert_between", "from": c.src, "to": c.dst, **ins}]))
        fixes.append(Fix(f"Remove the connection {c.src}→{c.dst}",
                         f"{a.spec.label} cannot feed {b.spec.label}.",
                         [{"op": "remove_connection", "from": c.src, "to": c.dst}]))
        out.append(Issue("INVALID_CONNECTION", "error",
                         f"Invalid connection {_label(m, c.src)} → {_label(m, c.dst)}.", [c.src, c.dst], fixes))

    cyc = _find_cycle(m)
    if cyc:
        out.append(Issue("CYCLE", "error", f"Material flow loops back ({cyc[0]} → {cyc[1]}).", list(cyc),
                         [Fix(f"Remove {cyc[0]}→{cyc[1]}", "Flow must go from storage to truck without loops.",
                              [{"op": "remove_connection", "from": cyc[0], "to": cyc[1]}])]))

    # 4. required equipment ---------------------------------------------------------
    for t in C.REQUIRED_TYPES:
        if not m.of_type(t):
            nid = m.next_id(t)
            out.append(Issue(f"MISSING_{t.upper()}", "error",
                             f"The model has no {C.TYPES[t].label}.", [],
                             [Fix(f"Add a {C.TYPES[t].label} ({nid})",
                                  "Every batching plant needs this equipment.",
                                  [{"op": "add_equipment", "type": t, "id": nid}])]))
    if not m.of_type("admixture_tank"):
        out.append(Issue("NO_ADMIXTURE", "info", "No admixture tank (optional for plain concrete).", []))

    mixers = [e.id for e in m.of_type("mixer")]
    loadouts = [e.id for e in m.of_type("loadout")]
    by_fam, free_h, mixed_h = _hopper_claims(m)

    # 5a. weigh hopper required before the mixer ------------------------------------
    for mx in mixers:
        for p in m.pred(mx):
            pe = eq[p]
            if pe.type == "weigh_hopper" or not pe.type:
                continue
            if any(eq[a].type == "weigh_hopper" for a in m.ancestors(p) | {p}):
                continue
            fams = m.families_upstream(p)
            sev = "error" if fams & {"cement", "aggregate"} else "warning"
            if not (C.ALLOWED.get(pe.type, set()) >= {"mixer"}):
                continue            # already reported as invalid connection
            out.append(Issue("NO_WEIGH_HOPPER", sev,
                             f"{_label(m, p)} feeds {mx} without a weigh hopper "
                             f"(material {', '.join(sorted(fams)) or '?'}).", [p, mx],
                             [Fix(f"Insert a weigh hopper between {p} and {mx}",
                                  "Dosing accuracy needs a weigh hopper (or scale) per material.",
                                  [{"op": "insert_between", "from": p, "to": mx,
                               **({"equip_id": next(h.id for h in m.of_type("weigh_hopper") if not m.pred(h.id) and not m.succ(h.id))}
                                  if any(not m.pred(h.id) and not m.succ(h.id) for h in m.of_type("weigh_hopper"))
                                  else {"type": "weigh_hopper"})}])]))

    # 5b. every mixer needs cement, aggregate, water (and admixture if a tank exists) --
    for mx in mixers:
        have_fams = set()
        for a in m.ancestors(mx):
            e = eq[a]
            if e.spec and e.spec.role == "source":
                have_fams.add(C.family_of_material(e.material) or e.spec.family)
        for fam in C.FAMILY_ORDER:
            srcs = [e for e in m.of_type(C.SOURCE_FOR_FAMILY[fam])]
            if fam in have_fams or not srcs:
                continue
            sev = "warning" if fam == "admixture" else "error"
            by_fam, free_h, mixed_h = _hopper_claims(m)
            hs = by_fam.get(fam, [])
            H = next((h for h in hs if mx not in m.descendants(h) | set(m.succ(h))), None)
            H = H or (hs[0] if hs else None) or (free_h[0] if free_h else None)
            msg = f"{mx} receives no {fam} (no {fam} supply path reaches the mixer)."
            if H is None:
                nid = m.next_id("weigh_hopper")
                fix = Fix(f"Add a weigh hopper {nid} for {fam}", f"{fam.capitalize()} must be weighed before mixing.",
                          [{"op": "add_equipment", "type": "weigh_hopper", "id": nid}])
            elif not m.families_upstream(H):
                free_src = [s for s in srcs if not m.succ(s.id)]
                S = (free_src or srcs)[0].id
                fix = Fix(f"Connect {S} → {H}", f"{S} is the {fam} supply; {H} will weigh it.",
                          [{"op": "add_connection", "from": S, "to": H}])
            elif mx not in m.succ(H):
                fix = Fix(f"Connect {H} → {mx}", f"{H} weighs the {fam} and must discharge into the mixer.",
                          [{"op": "add_connection", "from": H, "to": mx}])
            else:
                fix = Fix("Check the supply path manually", msg, [{"op": "manual", "text": msg}])
            out.append(Issue("MIXER_NO_" + fam.upper(), sev, msg, [mx], [fix]))

    # 6. mixer → loadout ------------------------------------------------------------
    for d in m.of_type("discharge_hopper"):
        if loadouts and not any(l in m.descendants(d.id) for l in loadouts):
            tgt = next((l for l in loadouts if not m.pred(l)), loadouts[0])
            out.append(Issue("DISCHARGE_HOPPER_DEAD_END", "error",
                             f"{d.id} does not lead to any truck loadout.", [d.id],
                             [Fix(f"Connect {d.id} → {tgt}", "Discharged concrete must reach a truck.",
                                  [{"op": "add_connection", "from": d.id, "to": tgt}])]))
    for mx in mixers:
        if loadouts and not any(l in m.descendants(mx) for l in loadouts):
            dch = next((d.id for d in m.of_type("discharge_hopper") if not m.pred(d.id)), None)
            tgt = dch or next((l for l in loadouts if not m.pred(l)), loadouts[0])
            out.append(Issue("MIXER_NO_OUTPUT", "error", f"{mx} has no path to a truck loadout.", [mx],
                             [Fix(f"Connect {mx} → {tgt}", "The mixer must discharge into a hopper or a truck.",
                                  [{"op": "add_connection", "from": mx, "to": tgt}])]))
    for d in m.of_type("discharge_hopper"):
        if not m.pred(d.id) and mixers:
            mx = min(mixers, key=lambda x: len(m.succ(x)))
            out.append(Issue("DISCHARGE_HOPPER_NO_INPUT", "error", f"{d.id} receives nothing.", [d.id],
                             [Fix(f"Connect {mx} → {d.id}", "A holding hopper is fed by a mixer.",
                                  [{"op": "add_connection", "from": mx, "to": d.id}])]))
    for l in loadouts:
        if not m.pred(l):
            src = next((d.id for d in m.of_type("discharge_hopper") if not m.succ(d.id)), None) or \
                  (min(mixers, key=lambda x: len(m.succ(x))) if mixers else None)
            if src:
                out.append(Issue("LOADOUT_NO_INPUT", "error", f"{l} receives no concrete.", [l],
                                 [Fix(f"Connect {src} → {l}", "A loadout is fed by a mixer or a discharge hopper.",
                                      [{"op": "add_connection", "from": src, "to": l}])]))

    # 7. orphan / leftover equipment ---------------------------------------------------
    by_fam, free_h, mixed_h = _hopper_claims(m)
    for e in m.of_type("aggregate_bin", "cement_silo", "water_tank", "admixture_tank"):
        if not m.succ(e.id):
            fam = C.family_of_material(e.material)
            hs = by_fam.get(fam, [])
            fixes = [Fix(f"Connect {e.id} → {hs[0]}", "Parallel supply into the same weigh hopper.",
                         [{"op": "add_connection", "from": e.id, "to": hs[0]}])] if hs else []
            fixes.append(Fix(f"Remove {e.id}", "Not used by the process.", [{"op": "remove_equipment", "id": e.id}]))
            out.append(Issue("SOURCE_UNUSED", "error", f"{_label(m, e.id)} is not connected to anything.", [e.id], fixes))
    for e in m.of_type("belt_conveyor", "screw_conveyor", "pump"):
        if not m.succ(e.id) or not m.pred(e.id):
            fixes = []
            fam_src = {"belt_conveyor": "aggregate", "screw_conveyor": "cement", "pump": None}[e.type]
            for c in m.flow_edges():
                a, b = eq[c.src], eq[c.dst]
                if b.type == "weigh_hopper" and a.spec.role == "source" and not m.succ(e.id) and not m.pred(e.id):
                    famc = C.family_of_material(a.material)
                    ok = (fam_src == famc) if fam_src else famc in ("water", "admixture")
                    if ok and e.type in C.ALLOWED[a.type]:
                        fixes.append(Fix(f"Place {e.id} between {c.src} and {c.dst}", "Transfer equipment belongs between storage and weigh hopper.",
                                         [{"op": "insert_between", "from": c.src, "to": c.dst, "equip_id": e.id}]))
                        break
            fixes.append(Fix(f"Remove {e.id}", "Dangling equipment.", [{"op": "remove_equipment", "id": e.id}]))
            out.append(Issue("TRANSFER_DANGLING", "error", f"{_label(m, e.id)} is not in a complete line (needs input and output).", [e.id], fixes))
    for h in m.of_type("weigh_hopper"):
        if not m.succ(h.id) or not m.pred(h.id):
            if not m.pred(h.id) and h.id in free_h and mixers:
                continue          # will be claimed by the mixer-feed rule
            out.append(Issue("HOPPER_DANGLING", "warning", f"{_label(m, h.id)} is not in a complete line.", [h.id],
                             [Fix(f"Remove {h.id}", "Dangling equipment.", [{"op": "remove_equipment", "id": h.id}])]))
    for h in mixed_h:
        out.append(Issue("MIXED_HOPPER", "error", f"{h} receives incompatible materials ({', '.join(sorted(m.families_upstream(h)))}).",
                         [h], [Fix("Separate the materials", "Each weigh hopper should weigh one material family.",
                                   [{"op": "manual", "text": f"Give each material family its own weigh hopper instead of {h}."}])]))

    # 8. control links --------------------------------------------------------------
    ctl = m.of_type("controller")
    if ctl:
        c0 = ctl[0].id
        linked = {c.dst for c in m.connections if c.kind == "control" and c.src in {x.id for x in ctl}} | \
                 {c.src for c in m.connections if c.kind == "control" and c.dst in {x.id for x in ctl}}
        for e in m.of_type("mixer", "weigh_hopper"):
            if e.id not in linked:
                out.append(Issue("NOT_CONTROLLED", "warning", f"{e.id} is not connected to the batching controller.", [e.id],
                                 [Fix(f"Connect controller {c0} → {e.id}", "The PLC has to weigh/dose/mix this equipment.",
                                      [{"op": "add_connection", "from": c0, "to": e.id, "kind": "control"}])]))
    for d in m.of_type("dust_collector"):
        if not any(c for c in m.connections if d.id in (c.src, c.dst)):
            silo = next((s.id for s in m.of_type("cement_silo")), None)
            if silo:
                out.append(Issue("DUST_UNCONNECTED", "warning", f"{d.id} is not attached to any equipment.", [d.id],
                                 [Fix(f"Attach {d.id} to {silo}", "Silo venting needs dust filtering.",
                                      [{"op": "add_connection", "from": silo, "to": d.id, "kind": "control"}])]))
    return out


def is_complete(issues: list[Issue]) -> bool:
    return not any(i.severity == "error" for i in issues)
