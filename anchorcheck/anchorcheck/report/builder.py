"""Professional PDF report (ReportLab).  Content comes only from the Session: computed results,
extracted data with provenance, product documents, and rule-based findings."""
from __future__ import annotations

import html
import io
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from .. import __version__
from ..design.basis import DesignBasis
from ..models import CaseResult, CheckResult, DesignCase
from ..pipeline import Session
from .figures import layout_figure, utilisation_chart

NAVY = colors.HexColor("#14365d")
LIGHT = colors.HexColor("#eef2f7")
ZEBRA = colors.HexColor("#f7f9fb")
GREEN, RED, AMBER, GREY = colors.HexColor("#1b7f3b"), colors.HexColor("#b3261e"), colors.HexColor("#b26a00"), colors.HexColor("#6b7280")
STATUS_COL = {"PASS": GREEN, "FAIL": RED, "INCOMPLETE": AMBER, "WARN": AMBER, "NA": GREY, "OK": GREEN, "GAP": RED,
              "UNKNOWN": AMBER, "INFO": GREY}
SEV_COL = {"Critical": RED, "Major": AMBER, "Minor": colors.HexColor("#2f6fa8"), "Info": GREY}

FONT, BOLD = "DejaVuSans", "DejaVuSans-Bold"
_FONTS_OK = False


def register_fonts() -> None:
    global FONT, BOLD, _FONTS_OK
    if _FONTS_OK:
        return
    here = Path(__file__).parent / "fonts"
    cands = [(here / "DejaVuSans.ttf", here / "DejaVuSans-Bold.ttf"),
             (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
             (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf"))]
    for reg, bld in cands:
        if reg.exists() and bld.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(reg)))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(bld)))
            pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
                                          italic="DejaVuSans", boldItalic="DejaVuSans-Bold")
            FONT, BOLD = "DejaVuSans", "DejaVuSans-Bold"
            break
    else:
        FONT, BOLD = "Helvetica", "Helvetica-Bold"
    _FONTS_OK = True


# ------------------------------------------------------------------------ text helpers
GREEK = {"tau": "τ", "psi": "ψ", "phi": "φ", "gamma": "γ", "alpha": "α", "beta": "β", "sigma": "σ", "delta": "δ",
         "lambda": "λ", "mu": "μ"}


def pretty(t) -> str:
    """Escape, then turn ASCII engineering notation into typeset text (Greek, sub/superscripts)."""
    s = html.escape(str(t), quote=False)
    s = s.replace("&lt;=", "≤").replace("&gt;=", "≥").replace("&gt;", ">").replace("&lt;", "<")
    s = re.sub(r"\bsqrt\b", "√", s)
    s = re.sub(r"\bpi\b", "π", s)
    s = s.replace(" * ", " · ")
    for k, v in GREEK.items():
        s = re.sub(rf"(?<![A-Za-z0-9]){k}(?![a-z])", v, s)
    s = re.sub(r"\b([NVMA])0(?=_)", r"\1⁰", s)
    s = re.sub(r"_([A-Za-z0-9,.'+\-]+)", r"<sub>\1</sub>", s)
    s = re.sub(r"\b(h)ef\b", r"\1<sub>ef</sub>", s)
    s = re.sub(r"\bf(uk|yk|sy)\b", r"f<sub>\1</sub>", s)
    s = re.sub(r"\bAs\b", "A<sub>s</sub>", s)
    s = s.replace("^1.5", "<super>1.5</super>").replace("^2", "²").replace("^0.5", "<super>0.5</super>")
    s = re.sub(r"\^\((.*?)\)", r"<super>\1</super>", s)
    return s


def plain(t) -> str:
    """Escape free text (file names, quotes, URLs) - NO notation processing."""
    return html.escape(str(t), quote=False)


def trunc(t: str, n: int) -> str:
    t = " ".join(str(t).split())
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    return cut + "…"


def uf(v) -> str:
    """Utilisation format (always 2 decimals)."""
    return "-" if v is None else ("∞" if math.isinf(v) else f"{v:.2f}")


CHECK_LABELS = {"N_steel": "Steel - tension", "N_pullout_group": "Bond / pull-out - group", "N_pullout_single": "Bond / pull-out - single anchor",
                "N_cone": "Concrete cone - group", "N_split": "Splitting", "N_sus": "Sustained load (creep)",
                "V_steel": "Steel - shear", "V_pryout": "Concrete pry-out", "NV_steel": "Interaction N+V - steel",
                "NV_conc": "Interaction N+V - concrete", "R_dev": "Bar development (AS 3600 / App. D)"}


def check_label(ck) -> str:
    if ck.key.startswith("V_edge_"):
        return "Edge failure - " + {"xpos": "+x", "xneg": "-x", "ypos": "+y", "yneg": "-y"}.get(ck.key[7:], ck.key[7:])
    return CHECK_LABELS.get(ck.key, ck.title[:40])


def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        if math.isinf(v):
            return "∞"
        if abs(v) >= 100:
            return f"{v:.0f}"
        if abs(v) >= 10:
            return f"{v:.1f}"
        return f"{v:.3g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def build_styles() -> Dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=ss["Normal"], fontName=FONT, fontSize=8.6, leading=11.6, textColor=colors.HexColor("#1f2933"))
    st = {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.4, leading=9.6, textColor=colors.HexColor("#4b5563")),
        "tiny": ParagraphStyle("tiny", parent=base, fontSize=6.6, leading=8.4, textColor=colors.HexColor("#6b7280")),
        "cell": ParagraphStyle("cell", parent=base, fontSize=7.8, leading=10),
        "cellb": ParagraphStyle("cellb", parent=base, fontName=BOLD, fontSize=7.8, leading=10),
        "th": ParagraphStyle("th", parent=base, fontName=BOLD, fontSize=7.6, leading=9.6, textColor=colors.white),
        "toctitle": ParagraphStyle("toctitle", parent=base, fontName=BOLD, fontSize=14, leading=18, textColor=NAVY, spaceBefore=6, spaceAfter=6),
        "h1": ParagraphStyle("h1", parent=base, fontName=BOLD, fontSize=14, leading=18, textColor=NAVY, spaceBefore=6, spaceAfter=6, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=base, fontName=BOLD, fontSize=10.5, leading=14, textColor=NAVY, spaceBefore=9, spaceAfter=4, keepWithNext=1),
        "h3": ParagraphStyle("h3", parent=base, fontName=BOLD, fontSize=9, leading=12, textColor=colors.HexColor("#243b53"), spaceBefore=6, spaceAfter=2, keepWithNext=1),
        "title": ParagraphStyle("title", parent=base, fontName=BOLD, fontSize=23, leading=28, textColor=NAVY),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=11.5, leading=15, textColor=colors.HexColor("#4b5563")),
        "banner": ParagraphStyle("banner", parent=base, fontName=BOLD, fontSize=14, leading=18, textColor=colors.white, alignment=TA_CENTER),
        "bannersub": ParagraphStyle("bannersub", parent=base, fontSize=8.4, leading=11, textColor=colors.white, alignment=TA_CENTER),
        "note": ParagraphStyle("note", parent=base, fontSize=7.6, leading=10, backColor=colors.HexColor("#fff8e6"),
                               borderColor=colors.HexColor("#f0c36d"), borderWidth=0.6, borderPadding=4, spaceBefore=3, spaceAfter=3),
    }
    return st


def P(text, style) -> Paragraph:
    return Paragraph(pretty(text), style)


def status_p(status: str, styles) -> Paragraph:
    col = STATUS_COL.get(status, GREY)
    return Paragraph(f'<font color="{col.hexval().replace("0x", "#")}"><b>{html.escape(status)}</b></font>', styles["cell"])


def table(data, widths, styles, header: bool = True, zebra: bool = True, extra: Optional[List] = None, repeat: int = 1) -> Table:
    t = Table(data, colWidths=widths, repeatRows=repeat if header else 0)
    cmds = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d5dbe3"))]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), NAVY)]
    if zebra:
        for r in range(1 if header else 0, len(data)):
            if r % 2 == 0:
                cmds.append(("BACKGROUND", (0, r), (-1, r), ZEBRA))
    t.setStyle(TableStyle(cmds + (extra or [])))
    return t


# ------------------------------------------------------------------------------ canvas
class NumberedCanvas(canvas.Canvas):
    meta: Dict = {}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._decorate(n)
            super().showPage()
        super().save()

    def _decorate(self, total: int):
        m = self.meta
        w, h = A4
        pg = self._pageNumber
        if pg > 1:
            self.setFillColor(NAVY)
            self.rect(0, h - 15 * mm, w, 15 * mm, fill=1, stroke=0)
            self.setFillColor(colors.white)
            self.setFont(BOLD, 8.5)
            self.drawString(15 * mm, h - 9.5 * mm, "Structural review - post-installed anchors & dowels")
            self.setFont(FONT, 8)
            self.drawRightString(w - 15 * mm, h - 9.5 * mm, f"{m.get('project', '')}  |  Rev {m.get('rev', '')}")
        self.setStrokeColor(colors.HexColor("#c8d0da"))
        self.line(15 * mm, 14 * mm, w - 15 * mm, 14 * mm)
        self.setFillColor(GREY)
        self.setFont(FONT, 6.8)
        self.drawString(15 * mm, 9.5 * mm, f"Generated by AnchorCheck v{__version__} - software-assisted review; engineer verification required. "
                                           f"{m.get('date', '')}")
        self.drawRightString(w - 15 * mm, 9.5 * mm, f"Page {pg} of {total}")
        if m.get("watermark"):
            self.saveState()
            self.setFillColor(colors.Color(0.75, 0.2, 0.2, alpha=0.10))
            self.setFont(BOLD, 62)
            self.translate(w / 2, h / 2)
            self.rotate(52)
            self.drawCentredString(0, 0, m["watermark"])
            self.restoreState()


class Doc(BaseDocTemplate):
    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph):
            name = fl.style.name
            if name in ("h1", "h2"):
                text = re.sub(r"<[^>]+>", "", fl.text)
                lvl = 0 if name == "h1" else 1
                key = f"h{self.seq.nextf('toc')}"
                self.canv.bookmarkPage(key)
                self.notify("TOCEntry", (lvl, text, self.page, key))


# --------------------------------------------------------------------------- sections
def watermark_for(verdict_label: str) -> str:
    return "" if verdict_label == "ADEQUATE" else ("DRAFT - PROVISIONAL" if "PROVISIONAL" in verdict_label or verdict_label in
                                                     ("INCOMPLETE", "NO DESIGN CASES") else "DRAFT - NOT ADEQUATE")


def cover(s: Session, st) -> List[Flowable]:
    p = s.project
    label, why = s.verdict
    col = GREEN if label == "ADEQUATE" else (RED if label.startswith("NOT ADEQUATE") else AMBER)
    banner = Table([[Paragraph(html.escape(label), st["banner"])], [Paragraph(html.escape(why), st["bannersub"])]],
                   colWidths=[170 * mm])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), col), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 10)]))
    info = [["Project", p.name or "-"], ["Reference", p.reference or "-"], ["Client", p.client or "-"],
            ["Drawings reviewed", ", ".join(s.files) or "-"], ["Report date", p.date], ["Revision", p.revision],
            ["Prepared by", p.engineer or "(software-assisted draft)"], ["Checked by", p.checker or "(pending)"],
            ["Governing standard", s.standards.primary if s.standards else "-"]]
    it = Table([[Paragraph(f"<b>{html.escape(a)}</b>", st["cell"]), Paragraph(html.escape(str(b)), st["cell"])] for a, b in info],
               colWidths=[40 * mm, 130 * mm])
    it.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5dbe3")), ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("BACKGROUND", (0, 0), (0, -1), LIGHT)]))
    return [Spacer(1, 38 * mm), Paragraph("Structural Analysis &amp; Review Report", st["title"]), Spacer(1, 3),
            Paragraph("Chemical anchors, post-installed dowels and reinforcement in concrete", st["subtitle"]),
            Paragraph("Assessment against AS 5216 and AS 3600", st["subtitle"]), Spacer(1, 16 * mm), banner, Spacer(1, 10 * mm), it,
            Spacer(1, 10 * mm),
            Paragraph("This report was prepared with software assistance from the drawings supplied. Extracted data, product "
                      "specifications and calculation results are traceable to their sources in the sections that follow. "
                      "It is a design-review aid and does not replace the judgement of the responsible registered/chartered "
                      "structural engineer, who must verify the inputs, product data and results before reliance.", st["small"]),
            PageBreak()]


def summary_section(s: Session, st) -> List[Flowable]:
    out = [Paragraph("1. Executive summary", st["h1"])]
    label, why = s.verdict
    out.append(Paragraph(f"<b>Outcome: {html.escape(label)}.</b> {html.escape(why)}", st["base"]))
    if s.ai_summary:
        out += [Spacer(1, 3), Paragraph("AI-drafted summary (generated from the computed results below; engineer to confirm)", st["h3"]),
                Paragraph(html.escape(s.ai_summary), st["base"])]
    rows = [[Paragraph(h, st["th"]) for h in ("Case", "Description", "Adhesive / data source", "Governing check", "Max util.", "Status")]]
    extra = []
    for i, c in enumerate(s.cases, start=1):
        r = s.results.get(c.id)
        if r is None:
            continue
        spec = s.specs.get(c.id)
        src = f"{c.adhesive_name or 'not named'}<br/><font size=6.5 color='#6b7280'>{html.escape(s.spec_source.get(c.id, 'none'))}</font>"
        desc = f"{'M' if c.kind == 'anchor' else 'N'}{c.fastener_d:g} {'rods' if c.kind == 'anchor' else 'dowels'}, hef {c.h_ef:g} mm" \
               + (f"<br/><font size=6.5 color='#6b7280'>{c.layout.n_x}×{c.layout.n_y} anchors</font>" if c.kind == "anchor" else "")
        rows.append([Paragraph(c.id, st["cellb"]), Paragraph(desc, st["cell"]), Paragraph(src, st["cell"]),
                     P(trunc(r.governing or "-", 70), st["cell"]), Paragraph(uf(r.max_utilisation), st["cellb"]),
                     status_p(r.status, st)])
    out.append(table(rows, [11 * mm, 37 * mm, 38 * mm, 46 * mm, 14 * mm, 24 * mm], st))
    top = [f for f in s.findings if f.severity in ("Critical", "Major")][:7]
    if top:
        out += [Spacer(1, 6), Paragraph("Key findings", st["h2"])]
        for f in top:
            col = SEV_COL[f.severity].hexval().replace("0x", "#")
            out.append(Paragraph(f'<font color="{col}"><b>{f.severity.upper()}</b></font> &nbsp;{html.escape(f.title)}'
                                 f' - <font color="#4b5563">{html.escape(trunc(f.detail, 230))}</font>', st["base"]))
            out.append(Spacer(1, 1.5))
    chart_rows = []
    for c in s.cases:
        r = s.results.get(c.id)
        if r and r.max_utilisation is not None:
            gk = next((ck for cr in r.combos for ck in cr.checks if ck.utilisation == r.max_utilisation), None)
            chart_rows.append((f"{c.id} - {check_label(gk) if gk else r.governing[:30]}", r.max_utilisation, r.status))
    if chart_rows:
        out += [Spacer(1, 6), Paragraph("Governing utilisation by case", st["h2"]), utilisation_chart(chart_rows, 470, FONT, BOLD)]
    out.append(PageBreak())
    return out


def scope_section(s: Session, st) -> List[Flowable]:
    ex = s.extraction
    out = [Paragraph("2. Scope, documents and method", st["h1"])]
    out.append(Paragraph("2.1 Documents reviewed", st["h2"]))
    rows = [[Paragraph(h, st["th"]) for h in ("File", "Pages", "Text layer", "Reading method")]]
    for f in s.files:
        pgs = [p for p in ex.pages]
        rows.append([Paragraph(html.escape(f), st["cell"]), Paragraph(str(len(pgs)) if len(s.files) == 1 else "-", st["cell"]),
                     Paragraph("yes" if any(p.has_text for p in pgs) else "NO (scanned)", st["cell"]),
                     Paragraph(" + ".join({"regex": "drawing-note text parsing", "vision": "vision model (shape recognition)"}.get(m, m)
                                          for m in ex.methods), st["cell"])])
    out.append(table(rows, [62 * mm, 18 * mm, 28 * mm, 62 * mm], st))
    out.append(Paragraph("2.2 Method", st["h2"]))
    out.append(Paragraph(
        "Anchors and dowels were identified from the drawing notes (embedded text) and, where a vision-capable model was "
        "configured, from the drawn shapes. Every extracted value is listed in Section 4 with its source. The applicable "
        "Australian Standards were identified from the fastener type, host material and drawing references (Section 3). "
        "The adhesive product was recognised by name, its specification obtained from attached documents or the "
        "manufacturer's published data, and compared with what the design requires (Section 5). The strength verification "
        "(Section 6) follows the AS 5216:2021 bonded-anchor methodology (EN 1992-4 / EAD 330499 basis) for threaded rods and "
        "AS 5216:2021 Appendix D with AS 3600 cl 13.1.2 for post-installed reinforcing bars. Capacity reduction factors "
        "are φ = 1/γ<sub>M</sub> with the product's installation safety factor γ<sub>inst</sub>. Calculations are deterministic; "
        "language models were used only to read drawings and product text, and never to calculate.", st["base"]))
    out.append(Paragraph("2.3 Limits of this review", st["h2"]))
    for t in ("Static ultimate-limit-state loading only. Seismic design (AS 5216:2021 Appendix F), fire, fatigue and impact are not designed.",
              "Solid concrete, rigid base plate, no torsion; anchor forces from an elastic distribution (plate bearing neglected, conservative).",
              "Plate bending, welds, bearing and the attached structure are outside the scope (AS 4100).",
              "AS 5216 is a licensed document: clause numbers shown are those confirmed from public sources; the reviewer should "
              "cross-check the factors listed in Appendix A against the licensed copy."):
        out.append(Paragraph("• " + html.escape(t), st["base"]))
    return out


def standards_section(s: Session, st) -> List[Flowable]:
    out = [Paragraph("3. Applicable Australian Standards", st["h1"])]
    d = s.standards
    if d is None:
        return out
    out.append(Paragraph(f"<b>Primary standard: {html.escape(d.primary)}</b>. {html.escape(d.summary)}", st["base"]))
    out.append(Spacer(1, 3))
    rows = [[Paragraph(h, st["th"]) for h in ("Standard", "Role", "Why it applies", "Attached reference")]]
    for r in d.refs:
        why = "; ".join(r.reasons)
        if r.sections:
            why += "<br/><font size=6.5 color='#6b7280'>Relevant: " + html.escape("; ".join(r.sections)) + "</font>"
        if r.note:
            why += "<br/><font size=6.5 color='#6b7280'>" + html.escape(r.note) + "</font>"
        rows.append([Paragraph(f"<b>{html.escape(r.label)}</b><br/><font size=6.5>{html.escape(r.title)}</font>", st["cell"]),
                     Paragraph(("" if r.applies else "<font color='#b3261e'>[N/A] </font>") + html.escape(r.role), st["cell"]),
                     Paragraph(why if "<br/>" in why else html.escape(why), st["cell"]),
                     Paragraph(html.escape(", ".join(r.attached)) or "<font color='#6b7280'>not attached</font>", st["cell"])])
    out.append(table(rows, [34 * mm, 34 * mm, 74 * mm, 28 * mm], st))
    if d.cited_on_drawing:
        out.append(Spacer(1, 4))
        out.append(Paragraph("Standards cited on the drawings: " + html.escape(", ".join(d.cited_on_drawing)), st["base"]))
    for iss in d.issues:
        out.append(Paragraph("⚠ " + html.escape(iss), st["note"]))
    return out


def case_inputs_table(c: DesignCase, st) -> Table:
    L = c.layout
    def pv(k):
        p = c.prov.get(k)
        if p is None:
            return "default / derived"
        if p.source == "assumed":
            return "ASSUMED - " + (p.note or "not on drawing")
        bits = [p.document or p.source]
        if p.page:
            bits.append(f"p.{p.page}")
        txt = " ".join(bits)
        if p.quote:
            txt += f' - "{trunc(p.quote, 48)}"'
        return txt + (f" [{p.note}]" if p.note in ("vision",) else "")
    rows = [[Paragraph(h, st["th"]) for h in ("Parameter", "Value", "Source")]]
    add = lambda a, b, src="": rows.append([P(a, st["cell"]), P(b, st["cell"]), Paragraph(plain(src), st["tiny"])])
    add("Fastener", f"{'Threaded rod M' if c.kind == 'anchor' else 'Post-installed bar N'}{c.fastener_d:g}, grade {c.grade}", pv("grade"))
    add("Effective embedment h_ef", f"{c.h_ef:g} mm", pv("h_ef"))
    add("Drill hole diameter d0", f"{c.d0:g} mm" if c.d0 else "-", pv("d0"))
    if c.kind == "anchor":
        add("Layout", f"{L.n_x} × {L.n_y} anchors" + (f" @ {L.s_x:g}/{L.s_y:g} mm" if L.n > 1 else ""), "drawing / assumed")
    else:
        add("Bar spacing", f"{L.s_x:g} mm" if L.s_x else "single bar", "drawing")
    edges = [(n, v) for n, v in (("-x", L.c_xneg), ("+x", L.c_xpos), ("-y", L.c_yneg), ("+y", L.c_ypos)) if v is not None]
    add("Edge distances", ", ".join(f"{n}: {v:g} mm" for n, v in edges) or "remote (not stated)", pv("edge"))
    add("Concrete", f"f'c = {c.concrete.fc:g} MPa, {'cracked' if c.concrete.cracked else 'uncracked'}"
        + (f", thickness {c.concrete.thickness:g} mm" if c.concrete.thickness else ", thickness not stated"), pv("fc"))
    add("Adhesive", c.adhesive_name or "not named", pv("adhesive"))
    add("Installation", f"{c.drilling} drilling, {c.hole_condition.replace('_', '/')} hole" + (", overhead" if c.overhead else ""), "drawing / assumed")
    for lc in c.loads:
        vtxt = f"V* = {math.hypot(lc.Vx, lc.Vy):g} kN" + (f" (Vx = {lc.Vx:g}, Vy = {lc.Vy:g})" if lc.Vx and lc.Vy else
                                                         (" acting in +x" if lc.Vx else (" acting in +y" if lc.Vy else "")))
        add(f"Design action ({lc.name})", f"N* = {lc.N:g} kN, {vtxt} ({lc.basis.replace('_', ' ')}, {lc.limit_state})",
            (f"{lc.prov.document or lc.prov.source} p.{lc.prov.page} - \"{trunc(lc.prov.quote, 48)}\"" if lc.prov and lc.prov.page else ""))
    return table(rows, [42 * mm, 86 * mm, 42 * mm], st)


def extraction_section(s: Session, st) -> List[Flowable]:
    out = [Paragraph("4. Design data extracted from the drawings", st["h1"])]
    out.append(Paragraph("Values are shown with their source (drawing text, vision reading, user entry or assumption). Assumptions are "
                         "conservative defaults and are repeated as review comments.", st["base"]))
    for c in s.cases:
        out.append(Paragraph(f"4.{c.id} {c.title}", st["h2"]))
        out.append(case_inputs_table(c, st))
        if c.notes:
            for n in c.notes:
                out.append(Paragraph("• " + html.escape(n), st["small"]))
    if not s.cases:
        out.append(Paragraph("No anchor or dowel groups were identified.", st["note"]))
    if s.rfis:
        out.append(Paragraph("Information not found on the drawings (requests for information)", st["h2"]))
        for r in s.rfis:
            out.append(Paragraph("• " + html.escape(r), st["base"]))
    return out


def spec_section(s: Session, st) -> List[Flowable]:
    out = [Paragraph("5. Adhesive product assessment", st["h1"])]
    seen = set()
    for c in s.cases:
        spec = s.specs.get(c.id)
        key = c.adhesive_name or c.id
        if key in seen:
            continue
        seen.add(key)
        out.append(Paragraph(f"5.{len(seen)} {html.escape(c.adhesive_name or 'No adhesive named for ' + c.id)}", st["h2"]))
        if spec is None:
            out.append(Paragraph("No specification could be obtained. Attach the manufacturer's TDS / European Technical Assessment "
                                 "or run the web lookup. The pull-out (bond) checks are INCOMPLETE until this is done.", st["note"]))
        else:
            info = [["Source of data", s.spec_source.get(c.id, "-")],
                    ["Chemistry", spec.chemistry or "not stated"],
                    ["Approvals found", ", ".join(spec.eta + spec.eads) or "none found"],
                    ["Seismic categories", ", ".join(spec.seismic) or "none found"],
                    ["Installation safety factor γinst", ", ".join(f"{k}: {v:g}" for k, v in spec.gamma_inst_map.items()) or
                     (f"{spec.gamma_inst:g}" if spec.gamma_inst else "not found (conservative default used)")],
                    ["Sustained-load factor ψ0sus", ", ".join(f"{k}: {v:g}" for k, v in spec.psi_sus0_map.items()) or
                     (f"{spec.psi_sus0:g}" if spec.psi_sus0 else "not found")],
                    ["Data status", "DEMONSTRATION VALUES" if spec.is_demo else ("confirmed by engineer" if spec.verified
                                                                                else "machine-read - NOT yet confirmed")]]
            out.append(table([[P(a, st["cellb"]), P(b, st["cell"])] for a, b in info], [55 * mm, 115 * mm], st, header=False))
            docs = [d for d in spec.docs]
            if docs:
                out.append(Spacer(1, 2))
                out.append(Paragraph("Source documents", st["h3"]))
                for d in docs[:8]:
                    out.append(Paragraph("• " + html.escape(d.get("title", "")) + (f" - {html.escape(d.get('url', ''))}" if d.get("url") else ""), st["small"]))
            kind = "rebar" if c.kind == "rebar" else "rod"
            ents = [e for e in spec.bond if abs(e.d - c.fastener_d) < 0.51 and e.service_life == 50 and e.drilling == "hammer"
                    and e.condition == "dry_wet" and e.fastener == kind]
            if ents:
                out.append(Paragraph(f"Bond resistance for {'bar' if c.kind == 'rebar' else 'rod'} d = {c.fastener_d:g} mm (hammer drilled, dry/wet, 50 years)", st["h3"]))
                rws = [[Paragraph(h, st["th"]) for h in ("Temperature range", "τRk,cr (MPa)", "τRk,ucr (MPa)", "Source")]]
                for e in ents:
                    rws.append([Paragraph(plain(e.temp_range), st["cell"]), Paragraph(fmt(e.tau_cr), st["cell"]),
                                Paragraph(fmt(e.tau_ucr), st["cell"]),
                                Paragraph(plain(f"{e.prov.document or e.prov.source} p.{e.prov.page}"), st["tiny"])])
                out.append(table(rws, [40 * mm, 28 * mm, 28 * mm, 74 * mm], st))
        rows = [[Paragraph(h, st["th"]) for h in ("Requirement", "Needed by the design", "Product documents provide", "Status")]]
        extra = []
        for row in s.comparisons.get(c.id, []):
            rows.append([P(row.topic, st["cellb"]), P(row.required, st["cell"]),
                         Paragraph(plain(row.provided + (f" - {row.note}" if row.note else "")), st["cell"]), status_p(row.status, st)])
        out += [Paragraph("Comparison of design requirements with product specification", st["h3"]),
                table(rows, [40 * mm, 48 * mm, 62 * mm, 20 * mm], st)]
        alts = s.alternatives.get(c.id)
        if alts:
            out.append(Paragraph("Alternative adhesives (same design re-run with each product's data)", st["h3"]))
            rws = [[Paragraph(h, st["th"]) for h in ("Product", "Result", "Max util.", "Governing", "Data confirmed")]]
            for a in alts:
                rws.append([P(a.name, st["cell"]), status_p(a.status, st), P(uf(a.max_util), st["cell"]),
                            P(trunc(a.governing, 60), st["cell"]), P("yes" if a.verified else "no", st["cell"])])
            out.append(table(rws, [50 * mm, 22 * mm, 18 * mm, 60 * mm, 20 * mm], st))
    return out


def check_block(ck: CheckResult, st) -> Flowable:
    col = STATUS_COL.get(ck.status, GREY)
    head = Paragraph(f"<b>{pretty(ck.title)}</b> <font size=6.6 color='#6b7280'>&nbsp;{html.escape(ck.clause)}</font>", st["cell"])
    rows = [[head, "", "", ""]]
    for stp in ck.steps:
        rows.append([P(stp.symbol, st["cell"]), P(stp.description, st["cell"]), P(f"{fmt(stp.value)} {stp.unit}".strip(), st["cellb"]),
                     P(stp.formula, st["tiny"])])
    if ck.R_d is not None:
        res = (f"R<sub>k</sub> = {fmt(ck.R_k)} kN, φ = {fmt(ck.phi)}, R<sub>d</sub> = {fmt(ck.R_d)} kN; "
               f"E<sub>d</sub> = {fmt(ck.E_d)} kN; utilisation = {uf(ck.utilisation)}")
        rows.append([Paragraph(res, st["cellb"]), "", "", status_p(ck.status, st)])
    elif ck.utilisation is not None:
        rows.append([Paragraph(f"utilisation = {uf(ck.utilisation)} (limit 1.00)", st["cellb"]), "", "", status_p(ck.status, st)])
    else:
        rows.append([Paragraph("Not evaluated - data missing" if ck.status == "INCOMPLETE" else "Not applicable", st["cellb"]), "", "",
                     status_p(ck.status, st)])
    for n in ck.notes:
        rows.append([Paragraph("• " + plain(n), st["tiny"]), "", "", ""])
    n = len(rows)
    t = Table(rows, colWidths=[31 * mm, 66 * mm, 27 * mm, 46 * mm])
    cmds = [("SPAN", (0, 0), (3, 0)), ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("LINEABOVE", (0, 0), (-1, 0), 1.2, NAVY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("LINEBELOW", (0, 1), (-1, -2), 0.2, colors.HexColor("#e3e8ee")),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd2db"))]
    last = len(ck.steps) + 1
    cmds += [("SPAN", (0, last), (2, last)), ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#f4f6f9")),
             ("LINEBELOW", (0, last), (-1, last), 0.5, col)]
    for i in range(last + 1, n):
        cmds.append(("SPAN", (0, i), (3, i)))
    t.setStyle(TableStyle(cmds))
    return t


def calc_section(s: Session, st) -> List[Flowable]:
    out = [Paragraph("6. Structural checks", st["h1"])]
    for c in s.cases:
        r = s.results.get(c.id)
        if r is None:
            continue
        out.append(Paragraph(f"6.{c.id} {c.title}", st["h2"]))
        # figure + chart
        gov = max([cr for cr in r.combos if cr.max_utilisation is not None], key=lambda x: x.max_utilisation, default=(r.combos[0] if r.combos else None))
        forces = [a["N"] for a in gov.anchor_forces] if gov and c.kind == "anchor" else None
        fig = layout_figure(c, forces, 235, 175, FONT) if c.kind == "anchor" else None
        crows = []
        if gov:
            for ck in gov.checks:
                if ck.utilisation is not None and ck.status in ("PASS", "FAIL") and ck.utilisation > 0:
                    crows.append((check_label(ck), ck.utilisation, ck.status))
        ch = utilisation_chart(crows, 270, FONT, BOLD) if crows else None
        if fig is not None and ch is not None:
            out.append(Table([[fig, ch]], colWidths=[85 * mm, 87 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elif ch is not None:
            out.append(ch)
        elif fig is not None:
            out.append(fig)
        out.append(Spacer(1, 3))
        out.append(Paragraph(f"Result: <b>{html.escape(r.status)}</b>" + (f" - maximum utilisation {uf(r.max_utilisation)} ({html.escape(r.governing)})" if r.max_utilisation is not None else ""), st["base"]))
        if r.assumptions:
            out.append(Paragraph("Assumptions", st["h3"]))
            for a in r.assumptions:
                out.append(Paragraph("• " + html.escape(a), st["small"]))
        if r.warnings:
            out.append(Paragraph("Warnings", st["h3"]))
            for w in r.warnings:
                out.append(Paragraph("⚠ " + html.escape(w), st["small"]))
        # combos summary
        if r.combos:
            rows = [[Paragraph(h, st["th"]) for h in ("Load combination", "N* (kN)", "V* (kN)", "Max util.", "Governing check", "Status")]]
            for cr in r.combos:
                rows.append([P(cr.combo.name, st["cell"]), P(fmt(cr.combo.N * (c.layout.n if cr.combo.basis == 'per_anchor' else 1)), st["cell"]),
                             P(fmt(math.hypot(cr.combo.Vx, cr.combo.Vy) * (c.layout.n if cr.combo.basis == 'per_anchor' else 1)), st["cell"]),
                             P(uf(cr.max_utilisation), st["cellb"]), P(trunc(cr.governing, 60), st["cell"]), status_p(cr.status, st)])
            out.append(Spacer(1, 3))
            out.append(table(rows, [32 * mm, 18 * mm, 18 * mm, 18 * mm, 62 * mm, 22 * mm], st))
        out.append(Paragraph("Design basis for this case", st["h3"]))
        out.append(table([[Paragraph(h, st["th"]) for h in ("Symbol", "Description", "Value")]] +
                         [[P(x.symbol, st["cell"]), P(x.description + (f" ({x.formula})" if x.formula else ""), st["cell"]), P(fmt(x.value), st["cellb"])]
                          for x in r.basis_table], [28 * mm, 114 * mm, 28 * mm], st))
        if gov:
            if gov.anchor_forces and c.kind == "anchor" and len(gov.anchor_forces) > 1:
                out.append(Paragraph(f"Anchor forces - governing combination '{gov.combo.name}' (kN, + = tension)", st["h3"]))
                out.append(table([[Paragraph(h, st["th"]) for h in ("Anchor", "x (mm)", "y (mm)", "N (kN)")]] +
                                 [[P(a["i"], st["cell"]), P(a["x"], st["cell"]), P(a["y"], st["cell"]), P(a["N"], st["cellb"])] for a in gov.anchor_forces],
                                 [20 * mm, 30 * mm, 30 * mm, 30 * mm], st))
            out.append(Paragraph(f"Calculations - governing combination '{gov.combo.name}'", st["h3"]))
            for ck in gov.checks:
                if ck.steps or ck.notes or ck.status in ("INCOMPLETE", "WARN"):
                    out.append(KeepTogether([check_block(ck, st), Spacer(1, 4)]))
        if r.detailing:
            out.append(Paragraph("Detailing and installation checks", st["h3"]))
            rows = [[Paragraph(h, st["th"]) for h in ("Check", "Values", "Status")]]
            for d in r.detailing:
                vals = "; ".join(f"{x.description}: {fmt(x.value)} {x.unit}" for x in d.steps) or "; ".join(d.notes)
                rows.append([P(d.title, st["cell"]), P(vals + (" - " + "; ".join(d.notes) if d.steps and d.notes else ""), st["cell"]), status_p(d.status, st)])
            out.append(table(rows, [52 * mm, 98 * mm, 20 * mm], st))
        if r.alt_method is not None:
            a = r.alt_method
            out.append(Paragraph("Cross-check: bar designed as a bonded anchor (AS 5216 / EAD 330499 basis)", st["h3"]))
            out.append(Paragraph(f"Status {html.escape(a.status)}; maximum utilisation {uf(a.max_utilisation)} ({html.escape(a.governing)}). "
                                 "Either method may be used by the designer; the Appendix D method is reported as the primary result.", st["small"]))
        fx = s.fixes.get(c.id)
        if fx:
            out.append(Paragraph("What would make this pass (sensitivity study)", st["h3"]))
            for f in fx:
                out.append(Paragraph(f"• {html.escape(f.description)} → max utilisation {uf(f.new_max_util)}" + ("" if f.passes else " (does not yet pass)"), st["base"]))
        out.append(PageBreak())
    return out


def findings_section(s: Session, st) -> List[Flowable]:
    out = [Paragraph("7. Review comments", st["h1"])]
    if not s.findings:
        out.append(Paragraph("No findings.", st["base"]))
        return out
    cnt = {k: sum(1 for f in s.findings if f.severity == k) for k in SEV_COL}
    out.append(Paragraph("  ".join(f"<b>{k}</b>: {v}" for k, v in cnt.items()), st["base"]))
    out.append(Spacer(1, 3))
    rows = [[Paragraph(h, st["th"]) for h in ("#", "Severity", "Case", "Category", "Comment", "Action")]]
    extra = []
    for i, f in enumerate(s.findings, start=1):
        rows.append([P(i, st["cell"]), Paragraph(f'<font color="{SEV_COL[f.severity].hexval().replace("0x", "#")}"><b>{f.severity}</b></font>', st["cell"]),
                     P(f.case_id or "-", st["cell"]), P(f.category, st["cell"]),
                     Paragraph(f"<b>{plain(f.title)}</b><br/>{plain(f.detail)}", st["cell"]), Paragraph(plain(f.action), st["cell"])])
    out.append(table(rows, [7 * mm, 16 * mm, 11 * mm, 22 * mm, 76 * mm, 38 * mm], st))
    return out


def appendices(s: Session, st) -> List[Flowable]:
    out = [PageBreak(), Paragraph("Appendix A - Design factors used and their verification status", st["h1"])]
    out.append(Paragraph("φ = 1/γ for concrete-related failure modes with γ = γc·γinst. 'Cross-checked' means the factor reproduces published "
                         "AS 5216-based manufacturer design tables (see tests in the software). Other factors follow the EN 1992-4 / EAD 330499 "
                         "methodology on which AS 5216:2021 is based and must be confirmed by the reviewer against the licensed standard.", st["base"]))
    rows = [[Paragraph(h, st["th"]) for h in ("Symbol", "Value", "Description", "Basis", "Cross-checked")]]
    for f in s.basis.factors():
        rows.append([P(f.symbol, st["cell"]), P(fmt(f.value), st["cellb"]), P(f.description, st["cell"]),
                     P(f.basis + (f" - {f.note}" if f.note else ""), st["tiny"]), P("yes" if f.verified else "NO - confirm", st["cell"])])
    out.append(table(rows, [20 * mm, 14 * mm, 56 * mm, 62 * mm, 18 * mm], st))
    out += [Paragraph("Appendix B - Notes read from the drawings", st["h1"])]
    if s.extraction.general_notes:
        for n in s.extraction.general_notes[:50]:
            out.append(Paragraph("• " + html.escape(n), st["small"]))
    else:
        out.append(Paragraph("No anchor-related notes were extracted.", st["small"]))
    if s.extraction.standards_cited:
        out.append(Paragraph("Standards cited: " + html.escape(", ".join(s.extraction.standards_cited)), st["small"]))
    out.append(Paragraph("Appendix C - Reference extracts from attached documents", st["h1"]))
    if s.kb_docs:
        out.append(table([[Paragraph(h, st["th"]) for h in ("Attached document", "Type", "Pages", "Standard codes detected")]] +
                         [[P(d["name"], st["cell"]), P(d["kind"], st["cell"]), P(d["pages"], st["cell"]), P(", ".join(d["codes"]) or "-", st["cell"])] for d in s.kb_docs],
                         [70 * mm, 20 * mm, 15 * mm, 65 * mm], st))
    else:
        out.append(Paragraph("No reference documents were attached; product data came from the sources listed in Section 5.", st["small"]))
    if s.citations:
        out.append(Spacer(1, 4))
        out.append(Paragraph("Passages of the attached standard / guide most relevant to each check (retrieved automatically; verify the clause)", st["small"]))
        for ct in s.citations:
            out.append(Paragraph(f"<b>{html.escape(ct['topic'])}</b> - {html.escape(ct['doc'])}, p.{ct['page']}: <i>“{html.escape(ct['snippet'])}…”</i>", st["small"]))
            out.append(Spacer(1, 2))
    out.append(Paragraph("Appendix D - Limitations and engineer verification", st["h1"]))
    for t in ("Product properties marked 'machine-read' were extracted automatically from the quoted source line/page and must be confirmed against the "
              "original manufacturer document before issue.",
              "Where a value is an assumption, the report says so; assumptions are conservative but do not replace project information.",
              "This report does not constitute certification. Responsibility for the design remains with the registered/chartered structural engineer.",
              "Installation must follow the manufacturer's instructions and AS 5216:2021 Appendix B (installer competence, hole preparation, proof testing)."):
        out.append(Paragraph("• " + html.escape(t), st["base"]))
    out.append(Spacer(1, 8))
    sign = Table([[Paragraph("<b>Prepared (software-assisted)</b>", st["cell"]), Paragraph("<b>Reviewed / verified by engineer</b>", st["cell"])],
                  [Paragraph(f"AnchorCheck v{__version__}<br/>{html.escape(s.project.date)}", st["cell"]),
                   Paragraph("Name: ______________________<br/><br/>Registration (RPEQ / CPEng / NER): ____________<br/><br/>"
                             "Signature: ____________________   Date: ____________", st["cell"])]],
                 colWidths=[70 * mm, 100 * mm], rowHeights=[8 * mm, 32 * mm])
    sign.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, NAVY), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd2db")),
                              ("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out.append(sign)
    # drawing thumbnails
    if s.page_images:
        out += [PageBreak(), Paragraph("Appendix E - Drawing pages reviewed", st["h1"])]
        for key, png in list(s.page_images.items())[:6]:
            try:
                ir = ImageReader(io.BytesIO(png))
                iw, ih = ir.getSize()
                w = 170 * mm
                h = w * ih / iw
                if h > 118 * mm:
                    h = 118 * mm
                    w = h * iw / ih
                out.append(KeepTogether([Paragraph(html.escape(key), st["small"]), Image(io.BytesIO(png), width=w, height=h), Spacer(1, 6)]))
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------- entry
def build_report(s: Session, path: os.PathLike) -> Path:
    register_fonts()
    st = build_styles()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    NumberedCanvas.meta = {"project": s.project.name, "rev": s.project.revision, "date": s.project.date,
                           "watermark": watermark_for(s.verdict[0])}
    doc = Doc(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=20 * mm,
              title=f"Structural review - {s.project.name}", author=s.project.engineer or "AnchorCheck",
              subject="Review of chemical anchors and post-installed reinforcement to AS 5216 / AS 3600")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="p", frames=[frame])])
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("t1", fontName=BOLD, fontSize=9, leading=13, leftIndent=0, textColor=NAVY),
                       ParagraphStyle("t2", fontName=FONT, fontSize=8, leading=11, leftIndent=12, textColor=colors.HexColor("#374151"))]
    story: List[Flowable] = cover(s, st)
    story += [Paragraph("Contents", st["toctitle"]), toc, PageBreak()]
    story += summary_section(s, st) + scope_section(s, st) + [Spacer(1, 6)] + standards_section(s, st) + [PageBreak()]
    story += extraction_section(s, st) + [PageBreak()] + spec_section(s, st) + [PageBreak()]
    story += calc_section(s, st) + findings_section(s, st) + appendices(s, st)
    doc.multiBuild(story, canvasmaker=NumberedCanvas)
    return path
