"""Decide which Australian Standards govern the review, with reasons.

The decision is rule-based and fully explainable.  Each returned entry says WHY it
was selected and from what evidence (drawing text, fastener type, user-attached
reference documents).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .registry import REGISTRY, SUPERSEDED, StandardInfo

CITE_RE = re.compile(r"\bAS(?:\s*/\s*NZS)?\s*(\d{3,4}(?:\.\d{1,2})?)(?:\s*[-:]\s*(\d{4}))?", re.I)

MATERIAL_KEYWORDS = {
    "masonry": r"\b(brick|brickwork|blockwork|block\s*wall|masonry|cmu|hollow\s*block|besser)\b",
    "timber": r"\b(timber|lvl|glulam|joist|bearer|stud\s*wall)\b",
    "concrete": r"\b(concrete|conc\.|slab|footing|pier|reinforced|r\.?c\.?\s|n\s?\d{2}\s*concrete|mpa|f'?c)\b",
    "steel": r"\b(steel\s*beam|rhs|shs|ub\b|uc\b|pfc)\b",
}


@dataclass
class StandardRef:
    code: str
    label: str
    title: str
    role: str                      # "Primary design", "Supporting", "Check", "Not applicable"
    applies: bool
    reasons: List[str] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)
    confidence: float = 0.8
    attached: List[str] = field(default_factory=list)       # attached reference files matching
    note: str = ""


@dataclass
class StandardsDecision:
    primary: str
    refs: List[StandardRef]
    issues: List[str] = field(default_factory=list)          # superseded / mismatched citations
    cited_on_drawing: List[str] = field(default_factory=list)
    base_material: str = "concrete"
    summary: str = ""


def cited_standards(text: str) -> List[str]:
    out = []
    for m in CITE_RE.finditer(text or ""):
        code = f"AS {m.group(1)}"
        if m.group(2):
            code += f":{m.group(2)}"
        if code not in out:
            out.append(code)
    # AS/NZS variants as written
    for m in re.finditer(r"\bAS\s*/\s*NZS\s*(\d{3,4}(?:\.\d)?)(?:\s*[-:]\s*(\d{4}))?", text or "", re.I):
        code = f"AS/NZS {m.group(1)}" + (f":{m.group(2)}" if m.group(2) else "")
        if code not in out:
            out.append(code)
    return out


def detect_base_material(text: str) -> str:
    t = (text or "").lower()
    scores = {k: len(re.findall(p, t, re.I)) for k, p in MATERIAL_KEYWORDS.items()}
    # concrete dominates unless masonry/timber are explicitly the *substrate*
    best = max(scores, key=scores.get)
    if scores["masonry"] > 0 and scores["masonry"] >= scores["concrete"]:
        return "masonry"
    if scores["timber"] > 0 and scores["timber"] >= scores["concrete"]:
        return "timber"
    return "concrete" if scores["concrete"] or best == "concrete" else "unknown"


def identify_standards(
    text: str,
    case_kinds: Iterable[str] = ("anchor",),
    flags: Optional[Dict[str, bool]] = None,
    attached_docs: Optional[Sequence[dict]] = None,
) -> StandardsDecision:
    """`flags`: seismic, fire, bridge, steel_plate, galvanised, coastal, non_structural, fatigue.

    `attached_docs`: [{"name":..., "codes": ["AS 5216:2021", ...]}] from the knowledge base.
    """
    flags = dict(flags or {})
    kinds = set(case_kinds)
    t = (text or "").lower()
    base = detect_base_material(text)
    flags.setdefault("seismic", bool(re.search(r"seismic|earthquake", t)))
    flags.setdefault("fire", bool(re.search(r"\bfire\b|\bfrl\b", t)))
    flags.setdefault("bridge", bool(re.search(r"\bbridge|abutment|girder\b", t)))
    flags.setdefault("steel_plate", bool(re.search(r"base\s*plate|baseplate|cleat|bracket|plate", t)))
    flags.setdefault("galvanised", bool(re.search(r"galv", t)))
    flags.setdefault("coastal", bool(re.search(r"coastal|marine|salt|stainless|316", t)))

    refs: List[StandardRef] = []

    def add(code: str, role: str, applies: bool, reasons: List[str], conf: float = 0.85, note: str = "",
            sections: Optional[List[str]] = None):
        si: StandardInfo = REGISTRY[code]
        refs.append(StandardRef(code=code, label=si.label, title=si.title, role=role, applies=applies,
                                reasons=reasons, sections=sections if sections is not None else si.sections,
                                confidence=conf, note=note or si.note))

    summary = ""
    if base == "concrete":
        why = []
        if "anchor" in kinds:
            why.append("Chemical (adhesive) anchors / threaded rods are post-installed fastenings in concrete.")
        if "rebar" in kinds:
            why.append("Post-installed reinforcing-bar dowels are covered by AS 5216:2021 Appendix D, which "
                       "refers to AS 3600 for stress development.")
        if not why:
            why.append("Fastening to concrete.")
        add("AS 5216", "Primary design standard", True, why, 0.95)
        primary = REGISTRY["AS 5216"].label
        summary = (f"{primary} governs the anchor / dowel design" +
                   ("; AS 3600 governs the reinforcement development length and member checks." if "rebar" in kinds else "."))
        add("AS 3600", "Supporting - concrete member, reinforcement detailing",
            True, ["Host member is concrete; AS 3600 provides concrete strength, cover, development/lap length "
                   "(cl 13.1/13.2) and shear-friction (cl 8.4) provisions, and AS 5216 App. D calls it up for "
                   "post-installed bars."] if "rebar" in kinds else
            ["Host member is concrete; AS 3600 provides concrete grade, cover/detailing, and crack-control basis "
             "used to decide cracked vs uncracked design."], 0.9)
    elif base == "masonry":
        add("AS 5216", "Not applicable to this substrate", False,
            ["AS 5216 covers anchorage in concrete only."], 0.8)
        add("AS 3700", "Governs masonry substrate", True,
            ["Masonry is indicated on the drawing; use manufacturer's masonry anchor data with AS 3700."], 0.7)
        primary = "AS 3700"
        summary = ("Substrate appears to be masonry - AS 5216 does not apply; manual engineering judgement "
                   "and manufacturer masonry data required.")
    elif base == "timber":
        add("AS 5216", "Not applicable to this substrate", False, ["AS 5216 covers anchorage in concrete only."], 0.8)
        add("AS 1720.1", "Governs timber fixings", True, ["Timber is indicated on the drawing."], 0.7)
        primary = "AS 1720.1"
        summary = "Substrate appears to be timber - AS 5216 does not apply."
    else:
        add("AS 5216", "Primary design standard (assumed)", True,
            ["Substrate not stated - concrete assumed because chemical anchors / dowels are specified. "
             "Confirm the base material."], 0.55)
        primary = REGISTRY["AS 5216"].label
        summary = "Base material not stated; AS 5216 (concrete) assumed - confirm."

    add("AS/NZS 1170.0", "Supporting - design actions", True,
        ["Design loads on the fastening must be ULS factored actions from the AS/NZS 1170 series."], 0.9)
    if "rebar" in kinds or base == "concrete":
        add("AS/NZS 4671", "Supporting - reinforcing steel", "rebar" in kinds,
            ["Reinforcing-bar strength (f_sy = 500 MPa for 500N/L/E)."] if "rebar" in kinds else
            ["Not directly needed unless dowels are reinforcing bars."], 0.8)
    if "anchor" in kinds:
        add("AS 4291.1", "Supporting - threaded rod / stud property class", True,
            ["Steel grade of the threaded rod determines f_yk and f_uk used in steel-failure checks."], 0.75)
    if flags.get("steel_plate"):
        add("AS 4100", "Supporting - attached steelwork", True,
            ["A base plate / bracket is shown; plate bending, bearing and weld design are to AS 4100 "
             "(outside this tool's scope)."], 0.7)
    if flags.get("seismic"):
        add("AS 1170.4", "Applies - seismic", True,
            ["Seismic actions are mentioned; AS 5216:2021 Appendix F requires a seismic performance category "
             "(C1/C2) based on crack width / AS 1170.4 hazard factors. The static check in this tool does "
             "not replace the seismic design."], 0.8)
    if flags.get("fire"):
        add("AS 1530.4", "Check - fire", True,
            ["Fire resistance mentioned: use the manufacturer's fire data and AS 3600 Section 5."], 0.7)
    if flags.get("bridge"):
        add("AS 5100.5", "Applies - bridge structure", True,
            ["Bridge elements mentioned; AS 5100.5 loads/concrete provisions apply (AS 5216 is referenced "
             "by AS 5100 for fasteners)."], 0.65)
    if flags.get("galvanised"):
        add("AS/NZS 4680", "Supporting - corrosion protection", True, ["Galvanised steel is specified."], 0.7)
    if flags.get("coastal"):
        add("AS/NZS 2312.2", "Check - durability", True, ["Aggressive / coastal environment indicated."], 0.6)

    # cited on drawing + superseded detection
    cites = cited_standards(text)
    issues: List[str] = []
    for pat, msg in SUPERSEDED:
        if re.search(pat, text or "", re.I):
            issues.append(msg)
    cited_codes = {c.split(":")[0].replace("/NZS", "/NZS") for c in cites}
    if "AS 5216" not in cited_codes and base == "concrete":
        issues.append("The drawing does not cite AS 5216 for the anchorage design/installation; recommend adding "
                      "'Post-installed fastenings to be designed, qualified and installed to AS 5216:2021'.")

    # match attached reference docs
    for d in attached_docs or []:
        for code in d.get("codes", []):
            base_code = code.split(":")[0]
            for r in refs:
                if r.code == base_code and d["name"] not in r.attached:
                    r.attached.append(d["name"])

    return StandardsDecision(primary=primary, refs=refs, issues=issues, cited_on_drawing=cites,
                             base_material=base, summary=summary)
