"""Read anchor / dowel details from drawing IMAGES with a vision-capable model.

This is how the app recognises a fastener from its SHAPE (threaded rod through a base plate,
straight starter bar into a wall, hooked bar, ...) as well as from the notes.  The model sees tiles of the
drawing together with the exact embedded text of each tile.

Guards against hallucination:
  * the model must return an `evidence` string for each detail;
  * numeric values that do not appear in the page's embedded text (when it has one) are demoted to
    low confidence and flagged "visual read only";
  * everything lands in the review step - nothing is calculated straight from model output.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional, Sequence

from ..llm.clients import LLMClient, LLMError
from ..pdfio import PdfPage, image_to_png_bytes, render_page, render_tiles
from .common import Candidate, DrawingExtraction, Evidence, LoadItem

SYSTEM = (
    "You are an assistant to a chartered structural engineer reviewing Australian structural drawings. "
    "You identify post-installed chemical anchors (threaded rod / stud set in adhesive) and post-installed "
    "reinforcing-bar dowels / starter bars. Report ONLY what is drawn or written. Never guess numbers: use null "
    "when a value is not shown. Reply with one JSON object and nothing else."
)

SCHEMA = """{
 "sheet": {"number": "", "title": ""},
 "details": [
  {"detail_ref": "e.g. 'Section A-A'",
   "shape": "threaded_rod_with_base_plate | rod_in_slab_or_wall | straight_starter_bar | hooked_or_bent_bar | rebar_dowel_across_joint | cast_in_bolt | mechanical_anchor | other",
   "fastener_type": "chemical_anchor_rod | post_installed_rebar | cast_in | mechanical | unknown",
   "size": "M16 or N16 etc, as written",
   "grade": "steel grade as written or null",
   "count": null,
   "rows": null, "columns": null,
   "spacing_mm": null,
   "embedment_mm": null,
   "edge_distance_mm": null,
   "member_thickness_mm": null,
   "concrete_mpa": null,
   "hole_diameter_mm": null,
   "adhesive_product": "name as written or null",
   "loads": [{"kind": "tension|shear", "value": 0, "unit": "kN|kN/m", "basis": "group|per_anchor|per_metre", "limit_state": "ULS|SLS|unknown"}],
   "notes": ["verbatim note lines that concern this detail"],
   "confidence": 0.0,
   "evidence": "what in the drawing supports this (label text, dimension, symbol)"}
 ],
 "general_notes": ["verbatim notes about anchors, concrete strength, standards, installation"]
}"""

PROMPT = ("Read this drawing region. Identify every chemical-anchor or post-installed-rebar detail.\n"
          "Embedded text found in this region (exact characters, may be partial):\n<<TEXT>>\n\n"
          "Return JSON with this shape (omit nothing, use null for unknown):\n" + SCHEMA)


def _grid_for(page: PdfPage) -> tuple:
    area = page.width_pt * page.height_pt
    if area > 2.2e6:        # >A2
        return (3, 2)
    if area > 8e5:          # >A4: A3 etc
        return (2, 2)
    return (1, 1)


def _num(v) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(str(v).replace(",", "."))
    except ValueError:
        return None


def _in_text(v: float, text: str) -> bool:
    forms = {f"{v:g}", f"{int(v)}"} if float(v).is_integer() else {f"{v:g}", f"{v:.1f}"}
    return any(re.search(r"(?<![\d.])" + re.escape(f) + r"(?![\d])", text) for f in forms)


def detail_to_candidate(det: Dict, page_no: int, page_text: str, has_text: bool) -> Optional[Candidate]:
    size = str(det.get("size") or "").strip()
    m = re.search(r"([MNRDY])\s?(\d{1,2})", size, re.I)
    ftype = (det.get("fastener_type") or "").lower()
    if not m and not ftype.startswith(("chemical", "post")):
        return None
    d = int(m.group(2)) if m else None
    kind = "rebar" if (ftype == "post_installed_rebar" or (m and m.group(1).upper() in "NRDY")) else "anchor"
    if d is None:
        return None
    if ftype in ("cast_in", "mechanical"):
        return None
    ev = str(det.get("evidence") or "")[:200]
    base_conf = float(det.get("confidence") or 0.5)
    c = Candidate(kind=kind, page=page_no, label=f"{size or ('N' if kind == 'rebar' else 'M') + str(d)} "
                                                f"{det.get('detail_ref') or ''} (vision p.{page_no})".strip(),
                  shape=str(det.get("shape") or ""))

    def put(key, val, quote=""):
        if val is None:
            return
        conf = min(0.7, base_conf)
        if isinstance(val, (int, float)) and has_text and not _in_text(float(val), page_text):
            conf = min(conf, 0.35)
            c.notes.append(f"{key} = {val:g} was read visually and does not appear in the embedded text - verify.")
        c.fields[key] = Evidence(val, page_no, quote or ev, conf, "vision")

    c.fields["size"] = Evidence(d, page_no, size, min(0.75, base_conf), "vision")
    g = det.get("grade")
    if g:
        c.fields["grade"] = Evidence(str(g), page_no, str(g), min(0.7, base_conf), "vision")
    for key, jkey in (("count", "count"), ("n_y", "rows"), ("n_x", "columns"), ("spacing", "spacing_mm"),
                      ("h_ef", "embedment_mm"), ("edge", "edge_distance_mm"), ("thickness", "member_thickness_mm"),
                      ("fc", "concrete_mpa"), ("d0", "hole_diameter_mm")):
        v = _num(det.get(jkey))
        if v is not None:
            put(key, v)
    if det.get("adhesive_product"):
        c.fields["adhesive"] = Evidence(str(det["adhesive_product"]), page_no, str(det["adhesive_product"]),
                                        min(0.7, base_conf), "vision")
    for l in det.get("loads") or []:
        v = _num(l.get("value"))
        if v is None:
            continue
        unit = str(l.get("unit") or "kN").lower()
        basis = l.get("basis") or ("per_metre" if "/m" in unit else "group")
        c.loads.append(LoadItem(str(l.get("kind") or "tension").lower(), v, basis,
                                str(l.get("limit_state") or "unknown").upper(), page_no, ev, min(0.6, base_conf), "vision"))
    c.notes += [str(n) for n in (det.get("notes") or [])][:6]
    return c


def extract_with_vision(pdf_source, pages: Sequence[PdfPage], client: LLMClient, max_pages: int = 6,
                        progress: Optional[Callable[[str], None]] = None) -> DrawingExtraction:
    prog = progress or (lambda m: None)
    ex = DrawingExtraction(methods=["vision"])
    for page in pages[:max_pages]:
        grid = _grid_for(page)
        prog(f"Vision: page {page.number} ({grid[0]}x{grid[1]} tiles)")
        tiles = render_tiles(pdf_source, page.number - 1, grid=grid)
        # plus a downsized overview of the whole page
        try:
            overview = image_to_png_bytes(render_page(pdf_source, page.number - 1, 1.2), 1600)
        except Exception:
            overview = None
        for idx, t in enumerate(tiles):
            imgs = [image_to_png_bytes(t["img"], 1900)]
            prompt = PROMPT.replace("<<TEXT>>", (t["text"] or "(no embedded text)")[:3500])
            try:
                data = client.ask_json(SYSTEM, prompt, images=imgs)
            except LLMError as e:
                ex.warnings.append(f"Vision read failed on page {page.number} tile {idx + 1}: {e}")
                continue
            if not isinstance(data, dict):
                continue
            for det in data.get("details") or []:
                c = detail_to_candidate(det, page.number, page.text, page.has_text)
                if c:
                    ex.candidates.append(c)
            ex.general_notes += [f"p.{page.number}: {n}" for n in (data.get("general_notes") or []) if isinstance(n, str)]
    ex.candidates = dedupe_candidates(ex.candidates)
    ex.general_notes = list(dict.fromkeys(ex.general_notes))
    return ex


def dedupe_candidates(cands: List[Candidate]) -> List[Candidate]:
    """Merge candidates describing the same fastener on the same page (overlapping tiles see it twice)."""
    out: List[Candidate] = []
    for c in cands:
        match = next((o for o in out if o.kind == c.kind and o.page == c.page and o.get("size") == c.get("size")), None)
        if match is None:
            out.append(c)
            continue
        for k, e in c.fields.items():
            if k not in match.fields or e.confidence > match.fields[k].confidence:
                match.fields[k] = e
        known = {(l.kind, l.value) for l in match.loads}
        match.loads += [l for l in c.loads if (l.kind, l.value) not in known]
        match.notes += [n for n in c.notes if n not in match.notes]
        match.shape = match.shape or c.shape
    return out
