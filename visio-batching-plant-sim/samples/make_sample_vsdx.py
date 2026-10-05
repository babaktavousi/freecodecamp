"""Write a minimal .vsdx (Visio package) from a JSON model - used to test the reader and as a demo drawing.
   python samples/make_sample_vsdx.py samples/sample_plant_defective.json out.vsdx
Shapes carry Shape Data (EquipID, EquipType, Material); connectors are glued 1-D shapes."""
import json, sys, zipfile
from xml.sax.saxutils import escape, quoteattr

V = "http://schemas.microsoft.com/office/visio/2012/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def layout(model):
    ids = [e["id"] for e in model["equipment"]]
    succ = {i: [] for i in ids}
    for c in model["connections"]:
        if c.get("kind", "flow") == "flow" and c["from"] in succ and c["to"] in succ:
            succ[c["from"]].append(c["to"])
    depth = {i: 0 for i in ids}
    for _ in range(len(ids)):
        for a, bs in succ.items():
            for b in bs:
                depth[b] = max(depth[b], depth[a] + 1)
    cols = {}
    pos = {}
    for i in ids:
        r = cols.get(depth[i], 0)
        cols[depth[i]] = r + 1
        pos[i] = (1.5 + depth[i] * 2.2, 10 - r * 1.2)
    return pos


def build(model, out):
    pos = layout(model)
    sid = {e["id"]: n for n, e in enumerate(model["equipment"], 1)}
    shapes, connects = [], []
    for e in model["equipment"]:
        x, y = pos[e["id"]]
        props = "".join(
            f'<Row N={quoteattr(k)}><Cell N="Value" V={quoteattr(str(v))}/></Row>'
            for k, v in (("EquipID", e["id"]), ("EquipType", e.get("type") or ""), ("Material", e.get("material") or "")) if v)
        shapes.append(f'<Shape ID="{sid[e["id"]]}" Type="Shape" NameU={quoteattr(e["id"])}>'
                      f'<Cell N="PinX" V="{x}"/><Cell N="PinY" V="{y}"/><Cell N="Width" V="1.8"/><Cell N="Height" V="0.8"/>'
                      f'<Section N="Property">{props}</Section><Text>{escape(e["id"] + chr(10) + e.get("label", ""))}</Text></Shape>')
    n = len(sid)
    for c in model["connections"]:
        if c["from"] not in sid or c["to"] not in sid:
            continue
        n += 1
        (x1, y1), (x2, y2) = pos[c["from"]], pos[c["to"]]
        shapes.append(f'<Shape ID="{n}" Type="Shape" NameU="Dynamic connector.{n}"><Cell N="BeginX" V="{x1}"/><Cell N="BeginY" V="{y1}"/>'
                      f'<Cell N="EndX" V="{x2}"/><Cell N="EndY" V="{y2}"/></Shape>')
        connects.append(f'<Connect FromSheet="{n}" FromCell="BeginX" ToSheet="{sid[c["from"]]}" ToCell="PinX"/>')
        connects.append(f'<Connect FromSheet="{n}" FromCell="EndX" ToSheet="{sid[c["to"]]}" ToCell="PinX"/>')
    page = (f'<?xml version="1.0" encoding="utf-8"?><PageContents xmlns="{V}" xmlns:r="{R}"><Shapes>'
            + "".join(shapes) + "</Shapes><Connects>" + "".join(connects) + "</Connects></PageContents>")
    ct = ('<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>'
          '<Override PartName="/visio/pages/pages.xml" ContentType="application/vnd.ms-visio.pages+xml"/>'
          '<Override PartName="/visio/pages/page1.xml" ContentType="application/vnd.ms-visio.page+xml"/></Types>')
    rel = lambda t, tgt, i="rId1": (f'<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                    f'<Relationship Id="{i}" Type="{t}" Target="{tgt}"/></Relationships>')
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rel("http://schemas.microsoft.com/visio/2010/relationships/document", "visio/document.xml"))
        z.writestr("visio/document.xml", f'<?xml version="1.0" encoding="utf-8"?><VisioDocument xmlns="{V}" xmlns:r="{R}"/>')
        z.writestr("visio/_rels/document.xml.rels", rel("http://schemas.microsoft.com/visio/2010/relationships/pages", "pages/pages.xml"))
        z.writestr("visio/pages/pages.xml", f'<?xml version="1.0" encoding="utf-8"?><Pages xmlns="{V}" xmlns:r="{R}">'
                   f'<Page ID="0" Name={quoteattr(model.get("name", "Plant"))}><Rel r:id="rId1"/></Page></Pages>')
        z.writestr("visio/pages/_rels/pages.xml.rels", rel("http://schemas.microsoft.com/visio/2010/relationships/page", "page1.xml"))
        z.writestr("visio/pages/page1.xml", page)


if __name__ == "__main__":
    build(json.load(open(sys.argv[1])), sys.argv[2])
    print("wrote", sys.argv[2])
