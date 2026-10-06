"""Generate a two-sheet SAMPLE structural drawing (vector PDF with text layer) for demos and tests.

Sheet S-101: steel column base plate on 4 chemical-anchor rods.
Sheet S-102: wall starter bars (post-installed N16 dowels) into an existing slab.

The drawing, loads and product names are fictitious - for exercising the software only.
Usage:  python samples/make_sample_drawing.py [out.pdf]
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas

W, H = landscape(A3)


def frame(c, sheet, title):
    c.setLineWidth(1.2)
    c.rect(15, 15, W - 30, H - 30)
    c.setLineWidth(0.6)
    c.rect(W - 330, 15, 315, 120)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(W - 322, 118, "SAMPLE ENGINEERING PTY LTD (FICTITIOUS)")
    c.setFont("Helvetica", 9)
    c.drawString(W - 322, 100, "PROJECT: DEMO WAREHOUSE EXTENSION")
    c.drawString(W - 322, 85, f"DRAWING TITLE: {title}")
    c.drawString(W - 322, 70, f"SHEET NO: {sheet}")
    c.drawString(W - 322, 55, "SCALE: 1:10 @ A3     REV: B     DATE: 06/10/2026")
    c.drawString(W - 322, 40, "STATUS: ISSUED FOR REVIEW")


def hatch(c, x, y, w, h, step=8):
    c.saveState()
    p = c.beginPath()
    p.rect(x, y, w, h)
    c.clipPath(p, stroke=1, fill=0)
    c.setLineWidth(0.3)
    k = -h
    while k < w:
        c.line(x + k, y, x + k + h, y + h)
        k += step
    c.restoreState()


def dim(c, x1, y1, x2, y2, text, off=14, vertical=False):
    c.setLineWidth(0.4)
    c.setFont("Helvetica", 8)
    if vertical:
        c.line(x1 + off, y1, x2 + off, y2)
        c.line(x1, y1, x1 + off + 3, y1)
        c.line(x2, y2, x2 + off + 3, y2)
        c.saveState()
        c.translate(x1 + off - 3, (y1 + y2) / 2)
        c.rotate(90)
        c.drawCentredString(0, 0, text)
        c.restoreState()
    else:
        c.line(x1, y1 + off, x2, y2 + off)
        c.line(x1, y1, x1, y1 + off + 3)
        c.line(x2, y2, x2, y2 + off + 3)
        c.drawCentredString((x1 + x2) / 2, y1 + off + 3, text)


def notes(c, x, y, lines, title="GENERAL NOTES"):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, title)
    c.setFont("Helvetica", 9)
    for i, ln in enumerate(lines):
        c.drawString(x, y - 16 * (i + 1), ln)


def sheet1(c):
    frame(c, "S-101", "COLUMN BASE PLATE - CHEMICAL ANCHORS")
    # plan: plate 300x300 with 4 holes at 200 spacing
    ox, oy = 120, 520
    c.setLineWidth(0.8)
    c.rect(ox, oy, 300, 300)
    c.rect(ox + 90, oy + 90, 120, 120)          # column
    for dx in (50, 250):
        for dy in (50, 250):
            c.circle(ox + dx, oy + dy, 9)
            c.line(ox + dx - 14, oy + dy, ox + dx + 14, oy + dy)
            c.line(ox + dx, oy + dy - 14, ox + dx, oy + dy + 14)
    dim(c, ox + 50, oy + 250, ox + 250, oy + 250, "200", off=40)
    dim(c, ox + 250, oy + 50, ox + 250, oy + 250, "200", off=40, vertical=True)
    c.setFont("Helvetica", 9)
    c.drawString(ox, oy - 18, "PLAN - 400x400x25 BASE PLATE WITH 4 No. M20 RODS")
    # section
    sx, sy = 560, 460
    hatch(c, sx, sy, 330, 260)
    c.rect(sx, sy, 330, 260)
    c.setFillGray(0.85)
    c.rect(sx + 60, sy + 260, 210, 25, fill=1)
    c.setFillGray(0)
    c.rect(sx + 60, sy + 260, 210, 25)
    for rx in (sx + 90, sx + 240):
        c.setLineWidth(2)
        c.line(rx, sy + 90, rx, sy + 310)
        c.setLineWidth(0.8)
    dim(c, sx + 40, sy + 90, sx + 40, sy + 260, "200 EMBED", off=-30, vertical=True)
    dim(c, sx + 330, sy, sx + 330, sy + 260, "400 THK", off=18, vertical=True)
    c.drawString(sx, sy - 18, "SECTION A-A - ANCHOR RODS IN 400 THK CONCRETE SLAB")
    notes(c, 120, 360, [
        "1. CHEMICAL ANCHORS TO BE RAMSET CHEMSET REO 502 XTREM OR APPROVED EQUIVALENT.",
        "2. ANCHORS: 4 No. M20 GRADE 8.8 GALVANISED THREADED ROD, 200 mm MIN EMBEDMENT, 22 mm DIA HOLE.",
        "3. CONCRETE: N40 (f'c = 40 MPa). SLAB THICKNESS 400 THK. CRACKED CONCRETE ASSUMED.",
        "4. EDGE DISTANCE 150 mm MIN. ANCHOR SPACING 200 mm.",
        "5. DESIGN LOADS (ULS, GROUP TOTAL): TENSION N* = 60 kN, SHEAR V* = 20 kN.",
        "6. INSTALL IN ACCORDANCE WITH THE MANUFACTURER'S INSTRUCTIONS. CLEAN HOLES WITH BRUSH AND BLOWER.",
        "7. SCAN FOR EXISTING REINFORCEMENT BEFORE DRILLING. ADHESIVE ANCHORS TO AS 5216.",
    ])


def sheet2(c):
    frame(c, "S-102", "WALL STARTER BARS - POST-INSTALLED DOWELS")
    sx, sy = 140, 420
    hatch(c, sx, sy, 380, 200)
    c.rect(sx, sy, 380, 200)
    c.rect(sx + 130, sy + 200, 60, 250)       # new wall
    for rx in (sx + 145, sx + 175):
        c.setLineWidth(1.6)
        c.line(rx, sy + 30, rx, sy + 420)
        c.setLineWidth(0.8)
    dim(c, sx + 100, sy + 30, sx + 100, sy + 200, "400 EMBED", off=-25, vertical=True)
    dim(c, sx + 380, sy, sx + 380, sy + 200, "600 THK", off=20, vertical=True)
    c.setFont("Helvetica", 9)
    c.drawString(sx, sy - 18, "SECTION B-B - WALL TO EXISTING SLAB")
    c.setFont("Helvetica", 10)
    c.drawString(sx + 205, sy + 330, "N16 @ 300 CTS STARTER BARS")
    c.drawString(sx + 205, sy + 315, "EPOXY GROUTED - HILTI HIT-RE 500 V3")
    notes(c, 140, 330, [
        "1. N16 DOWELS @ 300 CTS EMBEDDED 400 mm INTO EXISTING SLAB WITH HILTI HIT-RE 500 V3 INJECTION EPOXY.",
        "2. EXISTING SLAB CONCRETE f'c = 32 MPa (N32), 600 THK. COVER 50 mm.",
        "3. DESIGN TENSION IN DOWELS: 45 kN/m ULS.",
        "4. DRILL 20 mm DIA HOLES BY HAMMER DRILL. CLEAN HOLES WITH BRUSH AND COMPRESSED AIR.",
        "5. INSTALLATION TO BE CARRIED OUT BY AEFAC CERTIFIED INSTALLERS. PROOF LOAD TEST 5% OF DOWELS.",
    ])


def make(path):
    c = canvas.Canvas(str(path), pagesize=landscape(A3))
    c.setTitle("SAMPLE structural drawing (fictitious)")
    sheet1(c)
    c.showPage()
    sheet2(c)
    c.showPage()
    c.save()
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("sample_structural_drawing.pdf")
    print("wrote", make(out))
