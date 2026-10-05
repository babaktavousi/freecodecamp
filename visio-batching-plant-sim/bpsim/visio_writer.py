"""Push corrections and simulation results back into the open Visio drawing (COM, Windows)."""
from __future__ import annotations

from . import catalog as C
from .model import PlantModel
from .visio_reader import _com_app


def _rgb(r: int, g: int, b: int) -> str:
    return f"RGB({r},{g},{b})"


def sync_model_to_visio(model: PlantModel, app=None) -> list[str]:
    """Draw equipment / connectors that exist in the model but not on the page; delete removed ones.
    Returns a log of what was done."""
    app = app or _com_app()
    page, log = app.ActivePage, []
    existing = {}
    for e in model.equipment.values():
        if e.shape.get("id"):
            try:
                existing[e.id] = page.Shapes.ItemFromID(int(e.shape["id"]))
            except Exception:
                pass
    xs = [s.CellsU("PinX").ResultIU for s in existing.values()] or [1.0]
    ys = [s.CellsU("PinY").ResultIU for s in existing.values()] or [5.0]
    nx, ny = max(xs) + 2.5, max(ys)
    for e in model.equipment.values():
        if e.id in existing:
            continue
        try:
            sh = page.DrawRectangle(nx - 0.9, ny - 0.4, nx + 0.9, ny + 0.4)
            sh.Text = f"{e.id}\n{e.label}"
            sh.CellsU("FillForegnd").FormulaU = _rgb(255, 242, 204)     # light yellow = added by the tool
            for key, val in (("EquipID", e.id), ("EquipType", e.type or ""), ("Material", e.material or "")):
                sh.AddNamedRow(243, key, 0)
                sh.CellsU(f"Prop.{key}").FormulaU = '"%s"' % val
            e.shape = {"id": str(sh.ID)}
            existing[e.id] = sh
            ny -= 1.1
            log.append(f"drew {e.id}")
        except Exception as exc:
            log.append(f"could not draw {e.id}: {exc}")
    wanted = {(c.src, c.dst) for c in model.connections}
    have = set()
    for sh in page.Shapes:
        try:
            if sh.OneD:
                b = en = None
                for cx in sh.Connects:
                    nm = str(cx.FromCell.Name)
                    if nm.startswith("Begin"):
                        b = str(cx.ToSheet.ID)
                    elif nm.startswith("End"):
                        en = str(cx.ToSheet.ID)
                ids = {str(s.ID): k for k, s in existing.items()}
                if b in ids and en in ids:
                    have.add((ids[b], ids[en]))
                    if (ids[b], ids[en]) not in wanted:
                        sh.Delete()
                        log.append(f"deleted connector {ids[b]}→{ids[en]}")
        except Exception:
            continue
    for a, b in sorted(wanted - have):
        try:
            con = page.Drop(app.ConnectorToolDataObject, 0, 0)
            con.CellsU("BeginX").GlueTo(existing[a].CellsU("PinX"))
            con.CellsU("EndX").GlueTo(existing[b].CellsU("PinX"))
            if model.conn_kind(a, b) == "control":
                con.CellsU("LinePattern").FormulaU = "2"       # dashed = control signal
            log.append(f"connected {a}→{b}")
        except Exception as exc:
            log.append(f"could not connect {a}→{b}: {exc}")
    for k in list(existing):
        if k not in model.equipment:
            existing[k].Delete()
    return log


def annotate_results(model: PlantModel, result, app=None) -> int:
    """Colour shapes by utilisation (green <60 %, amber <85 %, red >=85 %) and store results in Shape Data."""
    app = app or _com_app()
    page, n = app.ActivePage, 0
    for s in result.equipment.values():
        e = model.equipment.get(s.id)
        sid = e.shape.get("id") if e else None
        if not sid:
            continue
        try:
            sh = page.Shapes.ItemFromID(int(sid))
            col = _rgb(198, 224, 180) if s.util < 0.6 else _rgb(255, 217, 102) if s.util < 0.85 else _rgb(244, 120, 120)
            sh.CellsU("FillForegnd").FormulaU = col
            if s.id == result.bottleneck:
                sh.CellsU("LineWeight").FormulaU = "3 pt"
                sh.CellsU("LineColor").FormulaU = _rgb(192, 0, 0)
            for key, val in (("SimUtilization", f"{100 * s.util:.0f} %"), ("SimBlocked", f"{100 * s.blocked:.0f} %"),
                             ("SimPlantLimit", "n/a" if s.limit_m3h == float("inf") else f"{s.limit_m3h:.1f} m3/h")):
                if not sh.CellExistsU(f"Prop.{key}", 0):
                    sh.AddNamedRow(243, key, 0)
                sh.CellsU(f"Prop.{key}").FormulaU = '"%s"' % val
            n += 1
        except Exception:
            continue
    return n
