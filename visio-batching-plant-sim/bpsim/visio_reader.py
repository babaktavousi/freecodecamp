"""Read equipment IDs and connections from Visio.

Three sources: the live Visio application (COM, Windows), a .vsdx file (pure XML - works anywhere),
or a JSON model. Equipment is recognised by Shape Data (EquipID, EquipType, Material), else by the
shape text / master name (e.g. "CSL-01", "Cement silo"). Connectors are the glued 1-D shapes.
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

from . import catalog as C
from .model import PlantModel

NS = {"v": "http://schemas.microsoft.com/office/visio/2012/main"}
_Q = "{%s}" % NS["v"]


class _Raw:
    def __init__(self, sid, text, name, master, props, one_d, begin=None, end=None, pos=None):
        self.sid, self.text, self.name, self.master = sid, text, name, master
        self.props, self.one_d, self.begin, self.end, self.pos = props, one_d, begin, end, pos


def _build(raws: list[_Raw], page_name: str = "") -> PlantModel:
    m = PlantModel()
    m.name = page_name or "Visio drawing"
    sid_to_id: dict[str, str] = {}
    seen: dict[str, str] = {}
    for r in raws:
        if r.one_d:
            continue
        ident = (r.props.get("EquipID") or "").strip()
        label = (r.text or "").strip().replace("\n", " ")
        if not ident:
            mt = C.ID_RX.match(label)
            ident = f"{mt.group(1).upper()}-{mt.group(2)}" if mt else ""
        t = C.infer_type(r.props.get("EquipType"), ident, label, r.master, r.name)
        if not ident and not t:
            if label:
                m.ignored.append(label)
            continue
        if not t and not r.props.get("EquipID"):
            m.ignored.append(label or r.name)
            continue
        if not ident:
            ident = m.next_id(t)
            m.read_issues.append({"code": "NO_ID", "severity": "warning", "ids": [ident],
                                  "message": f"Shape '{label or r.name}' had no equipment ID; assigned {ident}.",
                                  "hint": f"Run 'Tag shapes' to store {ident} on the shape."})
        if ident in m.equipment:
            new = m.next_id(t) if t else ident + "-DUP"
            m.read_issues.append({"code": "DUPLICATE_ID", "severity": "warning", "ids": [ident, new],
                                  "message": f"Equipment ID {ident} is used by more than one shape; the later one is treated as {new}.",
                                  "hint": f"Rename the duplicate shape to {new} (Tag shapes does this)."})
            ident = new
        mat = r.props.get("Material") or (C.infer_material(t, label, ident, r.name) if t else None)
        e = m.add_equipment(t, ident, label or ident, mat)
        e.shape = {"id": r.sid, "pos": r.pos}
        for k, v in r.props.items():
            if k in ("EquipID", "EquipType", "Material"):
                continue
            try:
                e.params[k] = float(v)
            except ValueError:
                pass
        sid_to_id[r.sid] = ident
    for r in raws:
        if not r.one_d:
            continue
        a, b = sid_to_id.get(r.begin or ""), sid_to_id.get(r.end or "")
        if r.begin is None or r.end is None:
            m.read_issues.append({"code": "UNGLUED_CONNECTOR", "severity": "error", "ids": [],
                                  "message": f"Connector (shape {r.sid}) has an end that is not glued to any shape.",
                                  "hint": f"In Visio, drag the loose end of connector {r.sid} onto an equipment shape until it turns red."})
        elif a and b:
            m.add_connection(a, b)
        else:
            m.ignored.append(f"connector {r.sid} touches a non-equipment shape")
    return m


# ----------------------------------------------------------------------------- .vsdx
def read_vsdx(path: str, page: int = 1) -> PlantModel:
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if re.fullmatch(r"visio/pages/page\d+\.xml", n))
        if not names:
            raise ValueError("no pages found in the .vsdx file")
        pname = names[min(page, len(names)) - 1]
        masters: dict[str, str] = {}
        if "visio/masters/masters.xml" in z.namelist():
            for mm in ET.fromstring(z.read("visio/masters/masters.xml")).iter(_Q + "Master"):
                masters[mm.get("ID")] = mm.get("NameU") or mm.get("Name") or ""
        root = ET.fromstring(z.read(pname))
        page_title = ""
        if "visio/pages/pages.xml" in z.namelist():
            pg = ET.fromstring(z.read("visio/pages/pages.xml")).find("v:Page", NS)
            page_title = pg.get("Name", "") if pg is not None else ""
    raws: list[_Raw] = []
    for sh in root.iter(_Q + "Shape"):
        cells = {c.get("N"): c.get("V") for c in sh.findall("v:Cell", NS)}
        props = {}
        for sec in sh.findall("v:Section", NS):
            if sec.get("N") == "Property":
                for row in sec.findall("v:Row", NS):
                    cell = next((c for c in row.findall("v:Cell", NS) if c.get("N") == "Value"), None)
                    if cell is not None:
                        props[row.get("N")] = cell.get("V", "")
        txt_el = sh.find("v:Text", NS)
        text = "".join(txt_el.itertext()) if txt_el is not None else ""
        one_d = "BeginX" in cells and "EndX" in cells
        pos = (cells.get("PinX"), cells.get("PinY"))
        raws.append(_Raw(sh.get("ID"), text, sh.get("NameU") or sh.get("Name") or "",
                         masters.get(sh.get("Master", ""), ""), props, one_d, pos=pos))
    by_id = {r.sid: r for r in raws}
    ends: dict[str, dict] = {}
    for c in root.iter(_Q + "Connect"):
        side = "begin" if c.get("FromCell", "").startswith("Begin") else "end"
        ends.setdefault(c.get("FromSheet"), {})[side] = c.get("ToSheet")
    for sid, r in by_id.items():
        if r.one_d:
            r.begin, r.end = ends.get(sid, {}).get("begin"), ends.get(sid, {}).get("end")
    return _build(raws, page_title)


# ----------------------------------------------------------------------------- live Visio (COM)
def _com_app():
    try:
        import win32com.client            # type: ignore
    except ImportError as e:
        raise RuntimeError("Live Visio access needs Windows + 'pip install pywin32'. "
                           "Alternatively save the drawing as .vsdx and open that file.") from e
    try:
        return win32com.client.GetActiveObject("Visio.Application")
    except Exception:
        raise RuntimeError("Visio is not running. Open your drawing in Visio first.")


def _cell_text(shape, name: str) -> str:
    try:
        if shape.CellExistsU(name, 0):
            return str(shape.CellsU(name).ResultStr(0)).strip()
    except Exception:
        pass
    return ""


def read_visio_live(app=None) -> PlantModel:
    app = app or _com_app()
    page = app.ActivePage
    raws: list[_Raw] = []

    def walk(shapes):
        for sh in shapes:
            props = {}
            for key in ("EquipID", "EquipType", "Material", "rate", "capacity", "units", "settle_time_s",
                        "dump_time_s", "mix_time_s", "discharge_time_s"):
                v = _cell_text(sh, f"Prop.{key}")
                if v:
                    props[key] = v
            master = ""
            try:
                master = sh.Master.NameU if sh.Master is not None else ""
            except Exception:
                pass
            one_d = bool(sh.OneD)
            begin = end = None
            if one_d:
                try:
                    for cx in sh.Connects:
                        who = str(cx.FromCell.Name)
                        if who.startswith("Begin"):
                            begin = str(cx.ToSheet.ID)
                        elif who.startswith("End"):
                            end = str(cx.ToSheet.ID)
                except Exception:
                    pass
            raws.append(_Raw(str(sh.ID), sh.Text or "", sh.NameU, master, props, one_d, begin, end,
                             (sh.CellsU("PinX").ResultIU, sh.CellsU("PinY").ResultIU)))
            try:
                if sh.Type == 2 and not props.get("EquipID"):       # group: look inside
                    walk(sh.Shapes)
            except Exception:
                pass

    walk(page.Shapes)
    m = _build(raws, f"{app.ActiveDocument.Name} / {page.Name}")
    return m


def tag_shapes_live(model: PlantModel, app=None) -> int:
    """Write EquipID / EquipType / Material into each shape's Shape Data so the drawing is self-describing."""
    app = app or _com_app()
    page, n = app.ActivePage, 0
    for e in model.equipment.values():
        sid = e.shape.get("id")
        if not sid:
            continue
        try:
            sh = page.Shapes.ItemFromID(int(sid))
            for key, val in (("EquipID", e.id), ("EquipType", e.type or ""), ("Material", e.material or "")):
                if not sh.CellExistsU(f"Prop.{key}", 0):
                    sh.AddNamedRow(243, key, 0)
                    sh.CellsU(f"Prop.{key}").FormulaU = '""'
                    sh.CellsU(f"Prop.{key}.Label").FormulaU = f'"{key}"'
                sh.CellsU(f"Prop.{key}").FormulaU = '"%s"' % val
            n += 1
        except Exception:
            continue
    return n


def load_any(path: str) -> PlantModel:
    if path.lower().endswith((".vsdx", ".vsdm")):
        return read_vsdx(path)
    if path.lower().endswith(".json"):
        return PlantModel.load(path)
    raise ValueError("Unsupported file type (use .vsdx or .json). Save .vsd/.vsdm drawings as .vsdx first.")
