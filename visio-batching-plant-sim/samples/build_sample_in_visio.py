"""Draws a sample plant in the Visio that is currently open (Windows + pywin32).  NOT tested against real Visio.
   python samples/build_sample_in_visio.py samples/sample_plant_defective.json
Creates rectangles carrying Shape Data (EquipID, EquipType, Material) and glued dynamic connectors."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import win32com.client
from make_sample_vsdx import layout

model = json.load(open(sys.argv[1]))
try:
    app = win32com.client.GetActiveObject("Visio.Application")
except Exception:
    app = win32com.client.Dispatch("Visio.Application")
doc = app.ActiveDocument or app.Documents.Add("")
page = doc.Pages(1)
pos, shapes = layout(model), {}
for e in model["equipment"]:
    x, y = pos[e["id"]]
    sh = page.DrawRectangle(x - 0.9, y - 0.4, x + 0.9, y + 0.4)
    sh.Text = f'{e["id"]}\n{e.get("label", "")}'
    for k, v in (("EquipID", e["id"]), ("EquipType", e.get("type") or ""), ("Material", e.get("material") or "")):
        sh.AddNamedRow(243, k, 0)
        sh.CellsU(f"Prop.{k}").FormulaU = '"%s"' % v
    shapes[e["id"]] = sh
for c in model["connections"]:
    con = page.Drop(app.ConnectorToolDataObject, 0, 0)
    con.CellsU("BeginX").GlueTo(shapes[c["from"]].CellsU("PinX"))
    con.CellsU("EndX").GlueTo(shapes[c["to"]].CellsU("PinX"))
    if c.get("kind") == "control":
        con.CellsU("LinePattern").FormulaU = "2"
print("drawn", len(shapes), "shapes")
