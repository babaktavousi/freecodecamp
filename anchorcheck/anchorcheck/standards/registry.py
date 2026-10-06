"""Australian Standards relevant to anchors / post-installed reinforcement.

Edition years are given only where they could be confirmed; otherwise the
entry says "current edition" and the report asks the reviewer to confirm the
edition and amendments.  Standards Australia texts are copyrighted: this
registry holds identification data only, never clause text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class StandardInfo:
    code: str                         # "AS 5216"
    title: str
    edition: str = ""                 # "2021" or "" if unknown
    role: str = ""                    # one-line role in this review
    scope_keywords: List[str] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)       # useful parts (verified ones only)
    supersedes: List[str] = field(default_factory=list)
    edition_confirmed: bool = False
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.code}:{self.edition}" if self.edition else f"{self.code} (current edition)"


REGISTRY: Dict[str, StandardInfo] = {s.code: s for s in [
    StandardInfo(
        "AS 5216", "Design of post-installed and cast-in fastenings in concrete", "2021",
        "Primary design standard for anchors and post-installed reinforcing bars in concrete",
        ["anchor", "chemical anchor", "adhesive", "dowel", "post-installed", "fastening", "fastener",
         "threaded rod", "starter bar", "resin", "epoxy", "cast-in"],
        ["Appendix B (installation, installer competence)", "Appendix D (post-installed reinforcing bars)",
         "Appendix E (redundant non-structural systems)", "Appendix F (seismic actions)"],
        ["AS 5216:2018", "SA TS 101:2015"], True,
        "Published 23 July 2021, replaces AS 5216:2018; referenced by NCC 2022. Requires the product to be "
        "pre-qualified (assessment to EAD 330499 for bonded anchors; EAD 330087 for post-installed rebar)."),
    StandardInfo(
        "AS 3600", "Concrete structures", "2018",
        "Concrete member design; development/lap length and cover of reinforcement (cl 13.1, 13.2); "
        "shear-friction at joints (cl 8.4); durability and fire resistance",
        ["concrete", "reinforcement", "rebar", "slab", "beam", "wall", "column", "development length",
         "lap", "cover", "starter"], ["cl 13.1 (development of reinforcement)", "cl 8.4 (shear friction)"],
        ["AS 3600:2009", "AS 3600:2001"], False,
        "Edition 2018 with amendments - confirm the current amendment status."),
    StandardInfo(
        "AS/NZS 1170.0", "Structural design actions - General principles", "2002",
        "Load combinations / ultimate limit state factored actions applied to the fastening",
        ["load", "ultimate", "ULS", "design action"], [], [], False,
        "Confirm current amendments (Amdt 5:2011 was the latest known to the author)."),
    StandardInfo(
        "AS/NZS 1170.1", "Structural design actions - Permanent, imposed and other actions", "2002",
        "Permanent and imposed actions", ["imposed", "dead load", "live load"], [], [], False),
    StandardInfo(
        "AS 1170.4", "Structural design actions - Earthquake actions in Australia", "",
        "Earthquake actions; with AS 5216 Appendix F (seismic performance category C1/C2) for fastenings",
        ["seismic", "earthquake", "restraint", "non-structural component"], [], ["AS 1170.4-1993"], False,
        "Confirm the current edition and the edition called up by the NCC for the project."),
    StandardInfo(
        "AS 4100", "Steel structures", "2020",
        "Base plates, brackets and attached steelwork (anchor design itself is to AS 5216)",
        ["base plate", "steel", "bracket", "cleat", "baseplate", "column base"], [], ["AS 1250"], False,
        "Confirm the current amendment status."),
    StandardInfo(
        "AS/NZS 4671", "Steel for the reinforcement of concrete", "2019",
        "Reinforcing-bar grade/strength (500N/500L/500E); f_sy basis for dowel design",
        ["500n", "500l", "500e", "reinforcing bar", "deformed bar", "rebar", "n12", "n16"], [], [], False),
    StandardInfo(
        "AS 4291.1", "Mechanical properties of fasteners - Bolts, screws and studs", "",
        "Property classes (4.6, 5.8, 8.8...) of threaded rod / bolts (ISO 898-1 equivalent)",
        ["threaded rod", "bolt", "stud", "8.8", "5.8", "4.6"], [], [], False),
    StandardInfo(
        "AS/NZS 4680", "Hot-dip galvanized (zinc) coatings on fabricated ferrous articles", "",
        "Corrosion protection of galvanised steel anchors / plates",
        ["galvanised", "galvanized", "hdg", "hot dip"], [], [], False),
    StandardInfo(
        "AS/NZS 2312.2", "Guide to the protection of structural steel against atmospheric corrosion", "",
        "Selection of corrosion protection / stainless anchors in aggressive environments",
        ["corrosion", "coastal", "stainless", "marine"], [], [], False),
    StandardInfo(
        "AS 5100.5", "Bridge design - Concrete", "2017",
        "Governs when the fastening is part of a road/rail bridge or related structure",
        ["bridge", "abutment", "pier", "girder", "culvert"], [], [], False,
        "Confirm edition; AS 5100.8 covers rehabilitation and strengthening of existing bridges."),
    StandardInfo(
        "AS 3700", "Masonry structures", "2018",
        "Anchors into masonry are NOT covered by AS 5216 (concrete only)",
        ["brick", "block", "masonry", "cmu"], [], [], False),
    StandardInfo(
        "AS 1720.1", "Timber structures - Design methods", "2010",
        "Fixings into timber are NOT covered by AS 5216",
        ["timber", "lvl", "glulam", "joist"], [], [], False),
    StandardInfo(
        "AS 1530.4", "Methods for fire tests on building materials - Fire-resistance tests of elements of construction",
        "", "Fire resistance of the fastening (manufacturer fire data / AS 3600 Section 5)",
        ["fire", "frl", "fire rated"], [], [], False),
]}


# patterns (regex fragments) -> (superseded label, replacement guidance)
SUPERSEDED = [
    (r"SA\s*TS\s*101", "SA TS 101:2015 is superseded by AS 5216 (2018, now 2021)."),
    (r"AS\s*5216\s*[-:]?\s*2018", "AS 5216:2018 is superseded by AS 5216:2021 (July 2021)."),
    (r"AS\s*3600\s*[-:]?\s*(2009|2001|1994)", "AS 3600:2009 and earlier are superseded by AS 3600:2018."),
    (r"AS\s*1250", "AS 1250 (steel structures) is withdrawn; AS 4100 applies."),
    (r"AS\s*1170\.4\s*[-:]?\s*1993", "AS 1170.4-1993 is superseded; use the current AS 1170.4."),
    (r"BCA", "BCA is now the NCC (National Construction Code) - check the edition cited."),
]


def info(code: str) -> Optional[StandardInfo]:
    return REGISTRY.get(code)
