"""Types shared by the drawing-extraction layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    value: Any
    page: Optional[int] = None
    quote: str = ""
    confidence: float = 0.5
    method: str = "regex"          # regex | vision | user | default

    def short(self) -> str:
        q = f' "{self.quote[:60]}"' if self.quote else ""
        p = f" p.{self.page}" if self.page else ""
        return f"{self.method}{p}{q}"


@dataclass
class LoadItem:
    kind: str                      # tension | shear
    value: float                   # kN (or kN/m when basis == per_metre)
    basis: str = "group"           # group | per_anchor | per_metre
    limit_state: str = "unknown"   # ULS | SLS | unknown
    page: Optional[int] = None
    quote: str = ""
    confidence: float = 0.5
    method: str = "regex"


@dataclass
class Candidate:
    """One anchor/dowel group read from a drawing (before engineer review)."""

    kind: str = "anchor"           # anchor | rebar
    page: Optional[int] = None
    label: str = ""
    fields: Dict[str, Evidence] = field(default_factory=dict)
    loads: List[LoadItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    shape: str = ""                # as described by the vision model (threaded_rod_with_base_plate, ...)
    source: str = ""               # drawing file name

    def get(self, key: str, default=None):
        e = self.fields.get(key)
        return e.value if e is not None else default


@dataclass
class PageInfo:
    number: int
    has_text: bool
    chars: int
    title: str = ""
    sheet_no: str = ""


@dataclass
class DrawingExtraction:
    source_name: str = ""
    pages: List[PageInfo] = field(default_factory=list)
    candidates: List[Candidate] = field(default_factory=list)
    adhesive_mentions: List[Any] = field(default_factory=list)      # products.registry.ProductMatch
    general_notes: List[str] = field(default_factory=list)
    standards_cited: List[str] = field(default_factory=list)
    flags: Dict[str, Evidence] = field(default_factory=dict)         # seismic, fire, overhead, ...
    missing_notes: List[str] = field(default_factory=list)           # review comments (what the drawing omits)
    warnings: List[str] = field(default_factory=list)
    text: str = ""
    methods: List[str] = field(default_factory=list)
