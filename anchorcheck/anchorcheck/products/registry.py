"""Registry of commercial chemical-anchor / post-installed rebar adhesives sold in Australia.

IDENTITY data only (names, aliases, chemistry, manufacturer websites).  NO design
resistances are stored here: those must come from an attached TDS/ETA, from the
manufacturer's website (fetched at run time with provenance), or from the engineer.

Entries marked `verified_source` were read in the manufacturer documents named there.
Others come from general knowledge of the Australian market and are only used to
*recognise names*; the matched product is always confirmed against a fetched document.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProductInfo:
    id: str
    manufacturer: str
    name: str
    aliases: List[str]                   # lower-case phrases, spaces/hyphens/trademark marks normalised
    chemistry: str = ""                  # epoxy | vinylester | hybrid | polyester | cementitious
    website: str = ""                    # manufacturer domain (search hint, not a guarantee)
    known_eta: List[str] = field(default_factory=list)
    rebar_use: Optional[bool] = None
    seismic: List[str] = field(default_factory=list)
    verified_source: str = ""
    note: str = ""


def _p(**kw) -> ProductInfo:
    return ProductInfo(**kw)


PRODUCTS: List[ProductInfo] = [
    # ---------------- Ramset (ITW) - verified in Ramset public documents -----------------
    _p(id="ramset-reo502-xtrem", manufacturer="Ramset", name="ChemSet Reo 502 Xtrem",
       aliases=["chemset reo 502 xtrem", "reo 502 xtrem", "reo502 xtrem", "reo502x", "creo502x", "reo 502x"],
       chemistry="epoxy", website="ramset.com.au", known_eta=["ETA-25/0648"], rebar_use=True,
       seismic=["C1", "C2"], verified_source="Ramset SARB excerpt 2026 (cdn.ramset.com.au)"),
    _p(id="ramset-reo502-plus", manufacturer="Ramset", name="ChemSet Reo 502 PLUS",
       aliases=["chemset reo 502 plus", "reo 502 plus", "reo502 plus", "chemset reo 502", "reo 502", "reo502"],
       chemistry="epoxy", website="ramset.com.au", known_eta=["ETA-18/0675"], rebar_use=True,
       verified_source="Ramset 'AS 5216:2021 compliance' TDS (cdn.ramset.com.au)"),
    _p(id="ramset-801-xtrem-xc2", manufacturer="Ramset", name="ChemSet 801 Xtrem XC2",
       aliases=["chemset 801 xtrem xc2", "chemset 801 xtrem", "801 xtrem xc2", "chemset 801", "c801x600"],
       chemistry="vinylester", website="ramset.com.au", known_eta=["ETA-18/0045"], rebar_use=True,
       verified_source="Ramset SARB / AS 5216:2021 compliance TDS"),
    _p(id="ramset-epcon-c8-xtrem", manufacturer="Ramset", name="Epcon C8 Xtrem",
       aliases=["epcon c8 xtrem", "epcon c8", "chemset epcon c8"], chemistry="epoxy",
       website="ramset.com.au", known_eta=["ETA-10/0309", "ETA-07/0189"],
       verified_source="Ramset AS 5216:2021 compliance TDS"),
    _p(id="ramset-epcon-g5-xtrem", manufacturer="Ramset", name="ChemSet Epcon G5 Xtrem (NZ)",
       aliases=["epcon g5 xtrem", "epcon g5", "chemset epcon g5"], chemistry="epoxy",
       website="ramset.co.nz", rebar_use=True, verified_source="Ramset SARB (New Zealand range)"),
    _p(id="ramset-maxima", manufacturer="Ramset", name="ChemSet Maxima",
       aliases=["chemset maxima", "maxima"], chemistry="", website="ramset.com.au",
       known_eta=["ETA-18/0197"], verified_source="Ramset AS 5216:2021 compliance TDS"),
    _p(id="ramset-101-plus", manufacturer="Ramset", name="ChemSet 101 PLUS",
       aliases=["chemset 101 plus", "chemset 101", "ultrafix plus", "ultrafix+"], chemistry="",
       website="ramset.com.au", known_eta=["ETA-13/0681"],
       verified_source="Ramset AS 5216:2021 compliance TDS"),
    # ---------------- Hilti ----------------------------------------------------------------
    _p(id="hilti-hit-re-500-v3", manufacturer="Hilti", name="HIT-RE 500 V3",
       aliases=["hit re 500 v3", "hit-re 500 v3", "hitre500v3", "re 500 v3", "hit re 500", "re500 v3"],
       chemistry="epoxy", website="hilti.com.au", rebar_use=True),
    _p(id="hilti-hit-hy-200", manufacturer="Hilti", name="HIT-HY 200-A / HIT-HY 200-R V3",
       aliases=["hit hy 200 a", "hit hy 200 r", "hit hy 200", "hy 200 a", "hy 200 r", "hy200"],
       chemistry="hybrid", website="hilti.com.au", rebar_use=True),
    _p(id="hilti-hit-hy-270", manufacturer="Hilti", name="HIT-HY 270",
       aliases=["hit hy 270", "hy 270"], chemistry="hybrid", website="hilti.com.au"),
    _p(id="hilti-hit-re-100", manufacturer="Hilti", name="HIT-RE 100",
       aliases=["hit re 100", "re 100"], chemistry="epoxy", website="hilti.com.au"),
    _p(id="hilti-hit-hy-170", manufacturer="Hilti", name="HIT-HY 170",
       aliases=["hit hy 170", "hy 170"], chemistry="hybrid", website="hilti.com.au"),
    # ---------------- Sika ------------------------------------------------------------------
    _p(id="sika-anchorfix-3001", manufacturer="Sika", name="Sika AnchorFix-3001",
       aliases=["anchorfix 3001", "anchorfix-3001", "sika anchorfix 3001"], chemistry="epoxy",
       website="sika.com.au"),
    _p(id="sika-anchorfix-1", manufacturer="Sika", name="Sika AnchorFix-1",
       aliases=["anchorfix 1", "anchorfix-1", "sika anchorfix 1"], chemistry="polyester", website="sika.com.au"),
    _p(id="sika-anchorfix-2", manufacturer="Sika", name="Sika AnchorFix-2",
       aliases=["anchorfix 2", "anchorfix-2", "sika anchorfix 2"], chemistry="", website="sika.com.au"),
    # ---------------- fischer ---------------------------------------------------------------
    _p(id="fischer-fis-em-plus", manufacturer="fischer", name="fischer FIS EM Plus",
       aliases=["fis em plus", "fis em", "fischer fis em"], chemistry="epoxy", website="fischer.com.au",
       rebar_use=True),
    _p(id="fischer-fis-v", manufacturer="fischer", name="fischer FIS V (360 S)",
       aliases=["fis v 360", "fis v", "fis v zero"], chemistry="vinylester", website="fischer.com.au"),
    # ---------------- Simpson Strong-Tie ----------------------------------------------------
    _p(id="simpson-set-3g", manufacturer="Simpson Strong-Tie", name="SET-3G",
       aliases=["set 3g", "set-3g", "simpson set 3g"], chemistry="epoxy", website="strongtie.com.au"),
    _p(id="simpson-at-3g", manufacturer="Simpson Strong-Tie", name="AT-3G",
       aliases=["at 3g", "at-3g"], chemistry="", website="strongtie.com.au"),
    # ---------------- Powers / DEWALT -------------------------------------------------------
    _p(id="powers-pure110", manufacturer="Powers / DEWALT", name="Pure110+",
       aliases=["pure110", "pure 110"], chemistry="epoxy", website="dewalt.com.au"),
    _p(id="powers-ac100", manufacturer="Powers / DEWALT", name="AC100+ Gold",
       aliases=["ac100 gold", "ac100+", "ac 100"], chemistry="", website="dewalt.com.au"),
]

MANUFACTURERS = {
    "ramset": "ramset.com.au", "hilti": "hilti.com.au", "sika": "sika.com.au", "fischer": "fischer.com.au",
    "fischer": "fischer.com.au", "simpson": "strongtie.com.au", "powers": "dewalt.com.au", "dewalt": "dewalt.com.au",
    "mungo": "mungo.com.au", "rawlplug": "rawlplug.com", "allthread": "allthread.com.au", "chemset": "ramset.com.au",
}

GENERIC_CHEMISTRY = {
    "epoxy": r"\bepoxy\b|\bepcon\b|\bpure epoxy\b",
    "vinylester": r"\bvinyl\s?ester\b",
    "polyester": r"\bpolyester\b",
    "hybrid": r"\bhybrid\b",
    "cementitious": r"\bcementitious\b|\bgrout\b",
}


def norm(text: str) -> str:
    t = (text or "").replace("\u2122", " ").replace("\u00ae", " ").replace("\u00a9", " ")   # TM / R / C marks first:
    t = unicodedata.normalize("NFKD", t)                                                       # NFKD would turn TM into "TM"
    t = re.sub(r"[̀-ͯ]", "", t)
    t = t.lower()
    t = re.sub(r"[-_/,:;()\[\]]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


@dataclass
class ProductMatch:
    product: Optional[ProductInfo]
    name: str                             # as matched (registry name or raw text)
    confidence: float
    alias: str = ""
    quote: str = ""
    registered: bool = True
    manufacturer_hint: str = ""
    chemistry_hint: str = ""


def match_products(text: str) -> List[ProductMatch]:
    """Find adhesive products mentioned in drawing text.  Longest alias wins per product."""
    nt = norm(text)
    found: Dict[str, ProductMatch] = {}
    for p in PRODUCTS:
        best_alias = ""
        for al in p.aliases:
            pat = r"(?<![a-z0-9])" + re.escape(norm(al)) + r"(?![a-z0-9])"
            if re.search(pat, nt) and len(al) > len(best_alias):
                best_alias = al
        if best_alias:
            conf = 0.6 + min(0.35, len(best_alias) / 60.0)
            if norm(p.manufacturer) in nt:
                conf = min(0.99, conf + 0.1)
            m = re.search(re.escape(norm(best_alias)), nt)
            q = nt[max(0, m.start() - 25): m.end() + 25] if m else ""
            found[p.id] = ProductMatch(p, p.name, round(conf, 2), best_alias, q, True, p.manufacturer, p.chemistry)
    # drop shorter aliases that are substrings of a longer matched product (e.g. "reo 502" vs "reo 502 xtrem")
    out = list(found.values())
    out.sort(key=lambda m: -len(m.alias))
    keep: List[ProductMatch] = []
    for m in out:
        if any(m.alias != k.alias and norm(m.alias) in norm(k.alias) for k in keep):
            continue
        keep.append(m)
    # unregistered "<Manufacturer> <Model>" mentions
    for man, dom in MANUFACTURERS.items():
        for mm in re.finditer(rf"\b{man}\b\s+([A-Za-z0-9][A-Za-z0-9\-\+ ]{{2,28}})", text or "", re.I):
            cand = mm.group(0).strip()
            if not any(norm(cand).find(norm(k.alias)) >= 0 for k in keep if k.alias):
                if re.search(r"anchor|chem|resin|epoxy|inject|adhesive|\d", cand, re.I):
                    keep.append(ProductMatch(None, cand, 0.35, "", cand, False, man.title(), ""))
    return sorted(keep, key=lambda m: -m.confidence)


def chemistry_hint(text: str) -> str:
    for k, pat in GENERIC_CHEMISTRY.items():
        if re.search(pat, text or "", re.I):
            return k
    return ""


def get_product(pid: str) -> Optional[ProductInfo]:
    return next((p for p in PRODUCTS if p.id == pid), None)


def find_product_by_name(name: str) -> Optional[ProductInfo]:
    ms = match_products(name)
    return ms[0].product if ms and ms[0].product else None
