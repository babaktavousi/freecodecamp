"""Rule-based reading of drawing notes (embedded PDF text).

Understands the notation typical of Australian structural drawings:
    N16 @ 300 CTS  |  M16 GR 8.8 CHEMICAL ANCHORS  |  150 MIN EMBEDMENT  |  f'c = 40 MPa
    "TENSION 45 kN ULS"  |  "V* = 12kN"  |  "HIT-RE 500 V3"  |  "edge distance 100"
Every value keeps the quoted source line and page so reviewers can trace it.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..pdfio import PdfPage
from ..products.registry import chemistry_hint, match_products
from ..standards.identify import cited_standards
from .common import Candidate, DrawingExtraction, Evidence, LoadItem, PageInfo

BAR_SIZES = {10, 12, 16, 20, 24, 28, 32, 36, 40}
ROD_SIZES = {8, 10, 12, 16, 20, 24, 27, 30, 36}
CONCRETE_MPA = {20, 25, 32, 40, 50, 65}

ROD_CTX = re.compile(r"rod|stud|bolt|anchor|\bhd\b|threaded|chem|resin|epoxy|adhesive|set\s*in|grouted|"
                     r"holding\s*down|cast[- ]?in", re.I)
BAR_CTX = re.compile(r"dowel|starter|bar|reo|\bcts\b|ctrs|c/c|centres|crs|@|epoxy|chem|embed|drill|post[- ]?install|"
                     r"grout|tie|lap|reinforc", re.I)
GRADE_RE = re.compile(r"\b(?:gr(?:ade)?\.?\s*)?(4\.6|4\.8|5\.6|5\.8|6\.8|8\.8|10\.9|A4[- ]?(?:70|80)|A2[- ]?70|316|304)\b", re.I)

RE_ROD = re.compile(r"\bM\s?(\d{1,2})\b(?!\s*[xX]\s*\d{1,2}\s*(?:mm\s*)?(?:dia|ø))")
RE_BAR = re.compile(r"\b(?:D500[NLE]?[- ]?)?(?:N|Y|D)\s?(\d{2})\b")
RE_SPACING = re.compile(r"@\s*(\d{2,4})\s*(?:mm)?\s*(?:cts?|c/c|crs|ctrs|centres?|spacing)?", re.I)
RE_EMBED = [
    re.compile(r"(\d{2,4})\s*(?:mm)?\s*(?:min\.?|minimum)?\s*(?:embed(?:ment|ded)?|deep|depth|into)\b", re.I),
    re.compile(r"(?:embed(?:ment)?|depth|hef|h\s*ef|anchorage|anchor\s*length)\s*(?:depth|length|=|:|of|min\.?)?\s*[=:]?\s*(\d{2,4})\s*(?:mm)?", re.I),
]
RE_FC = [
    re.compile(r"f\s*['’′]?\s*c\s*=?\s*(\d{2})\s*(?:mpa)?", re.I),
    re.compile(r"\b(\d{2})\s*mpa\b", re.I),
    re.compile(r"\b[Nn]\s?(20|25|32|40|50|65)\b"),
]
RE_EDGE = [re.compile(r"edge\s*dist(?:ance)?\.?\s*(?:min\.?|>=|≥|=|:|of)?\s*(\d{2,4})", re.I),
           re.compile(r"(\d{2,4})\s*(?:mm)?\s*(?:min\.?)?\s*(?:to|from)?\s*(?:slab|wall|member)?\s*edge", re.I),
           re.compile(r"(?<![A-Za-z])e\.?d\.?\s*[=:]\s*(\d{2,4})\b", re.I)]
RE_SPACE_ANCHOR = re.compile(r"(?:anchor\s*)?spacing\s*[=:]?\s*(\d{2,4})", re.I)
RE_THICK = [re.compile(r"(\d{3,4})\s*(?:mm)?\s*(?:thk|thick|thickness|slab|wall|deep\s*slab)\b", re.I),
            re.compile(r"(?:thk|thickness|slab\s*thickness|wall\s*thickness)\s*[=:]?\s*(\d{3,4})", re.I)]
RE_HOLE = re.compile(r"(\d{2})\s*(?:mm)?\s*(?:dia\.?|ø|φ|diameter)?\s*hole", re.I)
RE_COUNT = re.compile(r"\b(\d{1,2})\s*(?:no\.?|nos|off|x)?\s*(?:chem(?:ical)?\s*)?(?:anchors?|bolts?|rods?|dowels?)\b", re.I)
RE_COUNT2 = re.compile(r"\b(\d{1,2})\s*(?:no\.?|nos\.?|off)\s*M\s?\d{1,2}\b", re.I)
RE_GRID = re.compile(r"\b(\d)\s*[xX×]\s*(\d)\b")
RE_KN = r"(\d+(?:[.,]\d+)?)\s*kN(?:\s*/\s*(m|anchor|bolt|bar))?"
RE_TENSION = re.compile(r"(?:N\s*\*|N\s*ed|N\s*d\b|tension|tensile|pull[- ]?out|uplift|axial|T\s*\*)[^\d\n]{0,25}?" + RE_KN, re.I)
RE_SHEAR = re.compile(r"(?:V\s*\*|V\s*ed|V\s*d\b|shear|lateral|horizontal)[^\d\n]{0,25}?" + RE_KN, re.I)

FLAG_PATTERNS: Dict[str, str] = {
    "seismic": r"seismic|earthquake|AS\s*1170\.4",
    "fire": r"\bfire\b|\bFRL\b|fire[- ]?rated",
    "overhead": r"overhead|upward|ceiling|soffit|underside",
    "sustained": r"sustained|permanent|dead\s*load|long[- ]?term",
    "cracked": r"(?<!un)(?<!non[- ])(?<!non)cracked",
    "uncracked": r"un[- ]?cracked|non[- ]?cracked",
    "flooded": r"flooded|water[- ]?filled|submerged|underwater",
    "diamond": r"diamond\s*(?:core|drill)|core\s*drill",
    "galvanised": r"galv|hdg|hot[- ]?dip",
    "stainless": r"stainless|316|A4[- ]?70",
    "coastal": r"coastal|marine|salt|aggressive",
    "bridge": r"\bbridge|abutment|girder",
}

# "good drawing practice" items the reviewer looks for - reported as missing when absent
CHECKLIST = [
    ("hole_cleaning", r"clean|brush|blow|compressed air", "hole preparation / cleaning to the manufacturer's instructions"),
    ("installer", r"AEFAC|certified|competent|trained\s*install|installer", "installer competence (AS 5216 Appendix B - e.g. AEFAC certification)"),
    ("proof_test", r"proof|pull[- ]?test|pull[- ]?out\s*test|test\s*load|torque", "site proof/pull testing regime"),
    ("scan", r"\bscan|\bgpr\b|ground\s*penetrating|locate\s*(?:existing\s*)?(?:reo|reinforc)|\bferro", "scanning/locating existing reinforcement before drilling"),
    ("temperature", r"temperature|°\s*c|cure|curing", "installation temperature and cure time before loading"),
    ("approved_equiv", r"approved\s*equivalent|or\s*equal|equivalent", "approval process for equivalent products"),
    ("standard", r"AS\s*5216", "reference to AS 5216 for qualification/installation of the fastenings"),
    ("embedment", r"embed|depth|hef", "embedment/hole depth"),
    ("concrete_grade", r"MPa|f\s*['’′]?\s*c|\bN\s?(?:20|25|32|40|50)\b|concrete\s*grade", "concrete strength of the host member"),
]


def _first(regs, text: str):
    for r in regs:
        m = r.search(text)
        if m:
            return m, r
    return None, None


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def sheet_title_info(text: str) -> Tuple[str, str]:
    m = re.search(r"\b(?:SHEET|DWG|DRAWING)\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[-\s]?\d{2,4}[A-Z]?)\b", text, re.I)
    sheet = m.group(1).upper() if m else ""
    tm = re.search(r"(?:TITLE|DRAWING\s*TITLE)\s*[:\-]?\s*([^\n]{5,70})", text, re.I)
    title = tm.group(1).strip() if tm else ""
    return title, sheet


def extract_from_pages(pages: List[PdfPage], source_name: str = "") -> DrawingExtraction:
    ex = DrawingExtraction(source_name=source_name, methods=["regex"])
    ex.text = "\n".join(f"[page {p.number}]\n{p.text}" for p in pages)
    for p in pages:
        title, sheet = sheet_title_info(p.text)
        ex.pages.append(PageInfo(p.number, p.has_text, len(p.text), title, sheet))
    if not any(p.has_text for p in pages):
        ex.warnings.append("No embedded text layer was found in the PDF (scanned or outlined drawing). "
                           "Text extraction is empty - configure a vision-capable model (Ollama/Claude) so the "
                           "drawing can be read from the image, or enter the data manually.")
    ex.standards_cited = cited_standards(ex.text)
    ex.adhesive_mentions = match_products(ex.text)

    for p in pages:
        if not p.has_text:
            continue
        for c in _candidates_on_page(p):
            ex.candidates.append(c)
    _flags(ex)
    _checklist(ex)
    # general anchor-related notes
    for p in pages:
        for line in p.text.split("\n"):
            if re.search(r"anchor|dowel|chem|embed|epoxy|adhesive|resin|starter|post[- ]?install", line, re.I) and 8 < len(line) < 220:
                ex.general_notes.append(f"p.{p.number}: {line.strip()}")
    ex.general_notes = list(dict.fromkeys(ex.general_notes))[:60]
    return ex


def _candidates_on_page(p: PdfPage) -> List[Candidate]:
    lines = [l.strip() for l in p.text.split("\n") if l.strip()]
    page_products = match_products(p.text)

    def ctx(i: int, w: int = 1) -> str:
        return " ".join(lines[max(0, i - w): i + w + 1])

    mentions: Dict[Tuple[str, int], List[int]] = {}
    # ---- rebar dowels / starters
    for i, line in enumerate(lines):
        for m in RE_BAR.finditer(line):
            d = int(m.group(1))
            if d not in BAR_SIZES:
                continue
            near = ctx(i)
            if re.search(r"concrete|mpa|grade", line, re.I) and not re.search(r"@|dowel|starter|bar", line, re.I):
                continue                                   # "N32 concrete" is not a bar
            if not (BAR_CTX.search(near) and re.search(r"dowel|starter|post[- ]?install|epoxy|chem|embed|drill|grout|adhesive|resin", near, re.I)):
                continue
            mentions.setdefault(("rebar", d), []).append(i)
    # ---- threaded rods / chemical anchors
    for i, line in enumerate(lines):
        for m in RE_ROD.finditer(line):
            d = int(m.group(1))
            if d not in ROD_SIZES or not ROD_CTX.search(ctx(i)):
                continue
            mentions.setdefault(("anchor", d), []).append(i)

    out: List[Candidate] = []
    for (kind, d), idxs in mentions.items():
        label = f"N{d} dowel (p.{p.number})" if kind == "rebar" else f"M{d} chemical anchor (p.{p.number})"
        c = Candidate(kind=kind, page=p.number, label=label)
        i0 = idxs[0]
        c.fields["size"] = Evidence(d, p.number, lines[i0], 0.8)
        near_all = " ".join(ctx(i) for i in idxs)
        if kind == "rebar":
            c.fields["grade"] = Evidence("500N", p.number, lines[i0], 0.5)
            for i in idxs:
                sm = RE_SPACING.search(lines[i]) or RE_SPACING.search(ctx(i, 2))
                if sm:
                    c.fields["spacing"] = Evidence(float(sm.group(1)), p.number, sm.group(0), 0.75)
                    break
        else:
            gm = GRADE_RE.search(near_all) or GRADE_RE.search(p.text)
            if gm:
                g = gm.group(1).upper().replace(" ", "-")
                g = {"316": "A4-70", "304": "A2-70"}.get(g, g)
                c.fields["grade"] = Evidence(g, p.number, gm.group(0), 0.75 if GRADE_RE.search(near_all) else 0.55)
            cm = RE_COUNT2.search(p.text) or RE_COUNT.search(near_all)
            if cm:
                c.fields["count"] = Evidence(int(cm.group(1)), p.number, cm.group(0), 0.6)
            gr = RE_GRID.search(near_all)
            if gr:
                c.fields["n_x"] = Evidence(int(gr.group(1)), p.number, gr.group(0), 0.55)
                c.fields["n_y"] = Evidence(int(gr.group(2)), p.number, gr.group(0), 0.55)
        _page_fields(c, p, lines, i0)
        if page_products:
            pm = page_products[0]
            c.fields["adhesive"] = Evidence(pm.name, p.number, pm.quote, pm.confidence, "regex")
            if len(page_products) > 1:
                c.notes.append("Several adhesive products are named on this sheet: " +
                               ", ".join(m.name for m in page_products) + " - confirm which applies.")
        low = p.text.lower()
        if re.search(r"diamond\s*(?:core|drill)|core\s*drill", low):
            c.fields["drilling"] = Evidence("diamond", p.number, "diamond core drilling", 0.6)
        elif re.search(r"hammer|rotary\s*percussion|carbide", low):
            c.fields["drilling"] = Evidence("hammer", p.number, "hammer drilling", 0.6)
        if re.search(r"flooded|water[- ]?filled|submerged", low):
            c.fields["hole_condition"] = Evidence("flooded", p.number, "flooded / water-filled hole", 0.6)
        out.append(c)
    return out


def _page_fields(c: Candidate, p: PdfPage, lines: List[str], i: int) -> None:
    """Page-level attributes: embedment, concrete, edge, thickness, loads."""
    near = " ".join(lines[max(0, i - 3): i + 4])
    text = p.text
    for scope, conf in ((near, 0.75), (text, 0.55)):
        if "h_ef" not in c.fields:
            m, _ = _first(RE_EMBED, scope)
            if m and 40 <= int(m.group(1)) <= 1200:
                c.fields["h_ef"] = Evidence(float(m.group(1)), p.number, m.group(0), conf)
        if "fc" not in c.fields:
            for r in RE_FC:
                for m in r.finditer(scope):
                    v = int(m.group(1))
                    window = scope[max(0, m.start() - 40): m.end() + 40]
                    if v in CONCRETE_MPA and re.search(r"concrete|conc\b|f\s*['’′]?\s*c|mpa|grade|slab|wall|footing|column|beam", window, re.I):
                        if r is RE_FC[2] and re.search(r"@|dowel|starter|bar", window, re.I) and not re.search(r"concrete|mpa", window, re.I):
                            continue
                        c.fields["fc"] = Evidence(float(v), p.number, m.group(0), conf)
                        break
                if "fc" in c.fields:
                    break
        if "edge" not in c.fields:
            m, _ = _first(RE_EDGE, scope)
            if m:
                c.fields["edge"] = Evidence(float(m.group(1)), p.number, m.group(0), conf)
        if "thickness" not in c.fields:
            m, _ = _first(RE_THICK, scope)
            if m and 80 <= int(m.group(1)) <= 3000:
                c.fields["thickness"] = Evidence(float(m.group(1)), p.number, m.group(0), conf)
        if "s_anchor" not in c.fields:
            m = RE_SPACE_ANCHOR.search(scope)
            if m:
                c.fields["s_anchor"] = Evidence(float(m.group(1)), p.number, m.group(0), conf)
        if "d0" not in c.fields:
            m = RE_HOLE.search(scope)
            if m and 8 <= int(m.group(1)) <= 60:
                c.fields["d0"] = Evidence(float(m.group(1)), p.number, m.group(0), conf)
    # loads (page level)
    for line in lines:
        for reg, kind in ((RE_TENSION, "tension"), (RE_SHEAR, "shear")):
            m = reg.search(line)
            if m:
                val = _num(m.group(1))
                unit = (m.group(2) or "").lower()
                basis = "per_metre" if unit == "m" else ("per_anchor" if unit in ("anchor", "bolt", "bar") else "group")
                if basis == "group" and re.search(r"per\s*(?:anchor|bolt|bar|rod)|each|/\s*(?:anchor|bolt|bar)", line, re.I):
                    basis = "per_anchor"
                ls = "SLS" if re.search(r"\bSLS\b|service|working|unfactored|characteristic", line, re.I) else \
                    ("ULS" if re.search(r"\bULS\b|ultimate|factored|design\s*load|\*", line, re.I) else "unknown")
                c.loads.append(LoadItem(kind, val, basis, ls, p.number, line[:160], 0.65))
    # drop duplicate loads
    uniq, seen = [], set()
    for l in c.loads:
        k = (l.kind, l.value, l.basis)
        if k not in seen:
            seen.add(k)
            uniq.append(l)
    c.loads = uniq


def _flags(ex: DrawingExtraction) -> None:
    for name, pat in FLAG_PATTERNS.items():
        m = re.search(pat, ex.text, re.I)
        if m:
            s = ex.text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
            ex.flags[name] = Evidence(True, None, s.strip(), 0.7)


def _checklist(ex: DrawingExtraction) -> None:
    low = ex.text
    for key, pat, desc in CHECKLIST:
        if not re.search(pat, low, re.I):
            ex.missing_notes.append(f"No note found for {desc}.")
