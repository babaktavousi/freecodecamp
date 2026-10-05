"""Apply structured correction actions to a PlantModel (used by rules and AI)."""
from __future__ import annotations

from . import catalog as C
from .model import PlantModel

OPS = {"add_equipment", "remove_equipment", "add_connection", "remove_connection",
       "reverse_connection", "set_type", "rename_id", "insert_between", "manual"}


def apply_action(m: PlantModel, a: dict) -> tuple[bool, str]:
    """Returns (ok, message). Never raises for bad input; the model is untouched on failure."""
    try:
        op = a.get("op")
        if op not in OPS:
            return False, f"unknown operation {op!r}"
        if op == "manual":
            return True, a.get("text", "manual action")
        if op == "add_equipment":
            t = C.normalise_type(a.get("type"))
            if not t:
                return False, f"unknown equipment type {a.get('type')!r}"
            ident = a.get("id") or m.next_id(t)
            if ident in m.equipment:
                return False, f"{ident} already exists"
            m.add_equipment(t, ident, a.get("label", ""), a.get("material"))
            return True, f"added {ident}"
        if op == "remove_equipment":
            if a["id"] not in m.equipment:
                return False, f"{a['id']} not found"
            m.remove_equipment(a["id"])
            return True, f"removed {a['id']}"
        if op == "set_type":
            t = C.normalise_type(a.get("type"))
            if a["id"] not in m.equipment or not t:
                return False, "bad set_type"
            e = m.equipment[a["id"]]
            e.type, e.material = t, C.infer_material(t, e.label, e.id)
            for c in m.connections:           # re-derive connection kinds
                c.kind = m.conn_kind(c.src, c.dst) if c.src in m.equipment and c.dst in m.equipment else c.kind
            return True, f"{e.id} is now {C.TYPES[t].label}"
        if op == "rename_id":
            m.rename(a["id"], a["new_id"])
            return True, f"renamed {a['id']} to {a['new_id']}"
        s, d = a.get("from"), a.get("to")
        if s not in m.equipment or d not in m.equipment:
            return False, f"unknown equipment in {s}->{d}"
        if op == "add_connection":
            return (True, f"connected {s} → {d}") if m.add_connection(s, d, a.get("kind")) else (False, "already connected")
        if op == "remove_connection":
            return (True, f"removed {s} → {d}") if m.remove_connection(s, d) else (False, "no such connection")
        if op == "reverse_connection":
            if not m.remove_connection(s, d):
                return False, "no such connection"
            m.add_connection(d, s)
            return True, f"reversed {s} → {d}"
        if op == "insert_between":
            if not m.has_connection(s, d):
                return False, f"no connection {s}->{d} to split"
            nid = a.get("equip_id")
            if nid:
                if nid not in m.equipment:
                    return False, f"{nid} not found"
            else:
                t = C.normalise_type(a.get("type"))
                if not t:
                    return False, "unknown type"
                nid = a.get("id") or m.next_id(t)
                m.add_equipment(t, nid)
            m.remove_connection(s, d)
            m.add_connection(s, nid)
            m.add_connection(nid, d)
            return True, f"inserted {nid} between {s} and {d}"
    except (KeyError, ValueError) as exc:
        return False, str(exc)
    return False, "unhandled"


def describe_action(a: dict) -> str:
    op = a.get("op")
    if op == "add_equipment":
        return f"Add {C.TYPES[a['type']].label if a.get('type') in C.TYPES else a.get('type')} {a.get('id', '')}".strip()
    if op == "remove_equipment":
        return f"Remove {a['id']}"
    if op in ("add_connection", "remove_connection", "reverse_connection"):
        verb = {"add_connection": "Connect", "remove_connection": "Disconnect", "reverse_connection": "Reverse"}[op]
        return f"{verb} {a['from']} → {a['to']}" + (" (control signal)" if a.get("kind") == "control" else "")
    if op == "insert_between":
        return f"Insert {a.get('equip_id') or a.get('type')} between {a['from']} and {a['to']}"
    if op == "set_type":
        return f"Set type of {a['id']} to {a['type']}"
    if op == "rename_id":
        return f"Rename {a['id']} to {a['new_id']}"
    return a.get("text", "Manual action")
