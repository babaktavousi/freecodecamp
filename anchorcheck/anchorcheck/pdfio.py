"""PDF / HTML reading helpers (pypdfium2 - permissive licence, wheels for Windows/macOS/Linux)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional, Sequence, Tuple, Union

import pypdfium2 as pdfium

PdfSource = Union[str, bytes]


@dataclass
class PdfPage:
    number: int                         # 1-based
    text: str
    width_pt: float
    height_pt: float

    @property
    def has_text(self) -> bool:
        return len(self.text.strip()) > 30


def _open(src: PdfSource) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(src if isinstance(src, (bytes, bytearray)) else str(src))


def read_pdf(src: PdfSource, max_pages: int = 400) -> List[PdfPage]:
    """Extract text of every page (embedded text layer only; no OCR)."""
    pdf = _open(src)
    pages: List[PdfPage] = []
    try:
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            w, h = page.get_size()
            tp = page.get_textpage()
            try:
                text = tp.get_text_range() or ""
            finally:
                tp.close()
            page.close()
            pages.append(PdfPage(i + 1, _clean(text), w, h))
    finally:
        pdf.close()
    return pages


def _clean(t: str) -> str:
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t ]+", " ", t)
    return t.strip()


def render_page(src: PdfSource, index: int, scale: float = 2.0):
    """Render page `index` (0-based) to a PIL image."""
    pdf = _open(src)
    try:
        page = pdf[index]
        img = page.render(scale=scale).to_pil().convert("RGB")
        page.close()
        return img
    finally:
        pdf.close()


def render_tiles(src: PdfSource, index: int, grid: Tuple[int, int] = (2, 2), overlap: float = 0.08,
                 scale: float = 2.5):
    """Render a page as overlapping tiles for vision models.

    Returns list of dict(img=PIL, bbox_pt=(l,b,r,t), text=str) where `text` is the embedded text
    inside the tile (so the model gets both the picture and the exact characters).
    """
    pdf = _open(src)
    out = []
    try:
        page = pdf[index]
        w, h = page.get_size()
        tp = page.get_textpage()
        full = page.render(scale=scale).to_pil().convert("RGB")
        gx, gy = grid
        for iy in range(gy):
            for ix in range(gx):
                l = max(0.0, (ix / gx - overlap) * w)
                r = min(w, ((ix + 1) / gx + overlap) * w)
                top = h - max(0.0, (iy / gy - overlap) * h)
                bot = h - min(h, ((iy + 1) / gy + overlap) * h)
                crop = full.crop((int(l * scale), int((h - top) * scale), int(r * scale), int((h - bot) * scale)))
                try:
                    txt = tp.get_text_bounded(left=l, bottom=bot, right=r, top=top) or ""
                except Exception:
                    txt = ""
                out.append({"img": crop, "bbox_pt": (l, bot, r, top), "text": _clean(txt), "ix": ix, "iy": iy})
        tp.close()
        page.close()
    finally:
        pdf.close()
    return out


def image_to_png_bytes(img, max_side: int = 2000) -> bytes:
    w, h = img.size
    if max(w, h) > max_side:
        k = max_side / float(max(w, h))
        img = img.resize((int(w * k), int(h * k)))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------ HTML -> text
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "tr", "section", "article", "table"}

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href")
            if href and href.lower().split("?")[0].endswith(".pdf"):
                self.parts.append(f" [PDF: {href}] ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    txt = "".join(p.parts)
    txt = re.sub(r"[  ]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()


def pdf_links_in_html(html: str, base_url: str = "") -> List[str]:
    from urllib.parse import urljoin
    out = []
    for m in re.finditer(r'href=["\']([^"\']+?\.pdf[^"\']*)["\']', html, re.I):
        out.append(urljoin(base_url, m.group(1)))
    return list(dict.fromkeys(out))
