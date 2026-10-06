"""End-to-end orchestration:  drawings -> extraction -> cases -> spec resolution -> checks -> findings."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import Settings
from .design import DesignBasis, check_case
from .extract.build import build_cases, merge_extractions
from .extract.common import DrawingExtraction
from .extract.patterns import extract_from_pages
from .extract.vision import extract_with_vision
from .knowledge.store import KnowledgeBase
from .llm.clients import LLMClient, LLMError, make_client
from .models import AdhesiveSpec, CaseResult, DesignCase
from .pdfio import PdfPage, read_pdf
from .products import library
from .products.compare import AlternativeRow, SpecRow, compare_spec, screen_alternatives
from .products.lookup import LookupResult, lookup_on_web, spec_from_documents
from .products.registry import find_product_by_name, norm, PRODUCTS
from .review import Finding, FixSuggestion, build_findings, suggest_fixes, verdict
from .standards.identify import StandardsDecision, identify_standards

Progress = Callable[[str], None]

CITATION_TOPICS = {
    "anchor": [("Steel failure in tension", "steel failure tension characteristic resistance fuk stress area"),
               ("Combined pull-out and concrete cone failure", "combined pull-out and concrete cone failure bonded anchor bond resistance"),
               ("Concrete cone failure", "concrete cone failure characteristic resistance critical edge distance spacing"),
               ("Concrete edge failure in shear", "concrete edge failure shear resistance"),
               ("Concrete pry-out failure", "concrete pry-out failure shear"),
               ("Combined tension and shear", "combined tension and shear interaction")],
    "rebar": [("Post-installed reinforcing bars", "post-installed reinforcing bar development length Appendix D"),
              ("Design bond strength f_bd", "design ultimate bond strength fbd reinforcing bar")],
}


@dataclass
class ProjectInfo:
    name: str = "Untitled project"
    reference: str = ""
    client: str = ""
    engineer: str = ""
    checker: str = ""
    revision: str = "A"
    date: str = field(default_factory=lambda: time.strftime("%d %B %Y"))


@dataclass
class Session:
    project: ProjectInfo = field(default_factory=ProjectInfo)
    files: List[str] = field(default_factory=list)
    extraction: DrawingExtraction = field(default_factory=DrawingExtraction)
    cases: List[DesignCase] = field(default_factory=list)
    rfis: List[str] = field(default_factory=list)
    standards: Optional[StandardsDecision] = None
    specs: Dict[str, Optional[AdhesiveSpec]] = field(default_factory=dict)
    spec_source: Dict[str, str] = field(default_factory=dict)
    lookups: Dict[str, LookupResult] = field(default_factory=dict)
    results: Dict[str, CaseResult] = field(default_factory=dict)
    comparisons: Dict[str, List[SpecRow]] = field(default_factory=dict)
    alternatives: Dict[str, List[AlternativeRow]] = field(default_factory=dict)
    fixes: Dict[str, List[FixSuggestion]] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    verdict: Tuple[str, str] = ("NOT RUN", "")
    citations: List[dict] = field(default_factory=list)
    ai_summary: str = ""
    basis: DesignBasis = field(default_factory=DesignBasis)
    log: List[str] = field(default_factory=list)
    page_images: Dict[str, bytes] = field(default_factory=dict)       # "file:page" -> PNG (for report figures)
    kb_docs: List[dict] = field(default_factory=list)


# ------------------------------------------------------------------------------- extraction
def extract_drawings(files: Sequence[Tuple[str, bytes]], settings: Settings, use_vision: bool = False,
                     kb: Optional[KnowledgeBase] = None, progress: Optional[Progress] = None,
                     project: Optional[ProjectInfo] = None, defaults: Optional[dict] = None) -> Session:
    prog = progress or (lambda m: None)
    s = Session(project=project or ProjectInfo(name="", engineer=settings.engineer, reference=settings.project_ref))
    llm = make_client(settings.llm)
    exs: List[DrawingExtraction] = []
    for name, data in files:
        prog(f"Reading {name}")
        pages = read_pdf(data)
        ex = extract_from_pages(pages, name)
        for c in ex.candidates:
            c.source = name
            c.label = f"{c.label} [{name}]" if len(files) > 1 else c.label
        if use_vision:
            ok, msg = llm.available()
            if ok:
                try:
                    vx = extract_with_vision(data, pages, llm, progress=prog)
                    for c in vx.candidates:
                        c.source = name
                    ex = merge_extractions(ex, vx)
                except LLMError as e:
                    ex.warnings.append(f"Vision model unavailable: {e}")
            else:
                ex.warnings.append(f"Vision reading skipped: {msg}")
        exs.append(ex)
        s.files.append(name)
        try:
            from .pdfio import image_to_png_bytes, render_page
            for i in range(min(len(pages), 4)):
                s.page_images[f"{name}:{i + 1}"] = image_to_png_bytes(render_page(data, i, 1.4), 1500)
        except Exception:
            pass
    ex = exs[0]
    for other in exs[1:]:
        ex.candidates += other.candidates
        ex.pages += other.pages
        ex.adhesive_mentions += other.adhesive_mentions
        ex.general_notes += other.general_notes
        ex.standards_cited = list(dict.fromkeys(ex.standards_cited + other.standards_cited))
        ex.flags.update(other.flags)
        ex.warnings += other.warnings
        ex.text += "\n" + other.text
        ex.missing_notes = [m for m in ex.missing_notes if m in other.missing_notes]
    s.extraction = ex
    s.cases, s.rfis = build_cases(ex, defaults)
    refresh_standards(s, kb)
    return s


def refresh_standards(s: Session, kb: Optional[KnowledgeBase] = None) -> None:
    kinds = {c.kind for c in s.cases} or {"anchor"}
    flags = {}
    if any(c.seismic for c in s.cases):
        flags["seismic"] = True
    if any(c.fire for c in s.cases):
        flags["fire"] = True
    s.standards = identify_standards(s.extraction.text, kinds, flags or None, kb.standards_summary() if kb else None)


# -------------------------------------------------------------------------- spec resolution
def _alias_list(name: str) -> List[str]:
    p = find_product_by_name(name)
    return ([name] + p.aliases) if p else [name]


def resolve_spec(name: str, kind: str, settings: Settings, kb: Optional[KnowledgeBase] = None,
                 llm: Optional[LLMClient] = None, allow_web: bool = True, explicit_docs: Optional[List[str]] = None,
                 progress: Optional[Progress] = None) -> Tuple[Optional[AdhesiveSpec], str, Optional[LookupResult]]:
    """Return (spec, source, lookup).  source in {library, attached, web, none}."""
    prog = progress or (lambda m: None)
    lib = library.load_specs()
    # 1. engineer-confirmed library entry
    for lname, ls in lib.items():
        if norm(lname) == norm(name) and ls.verified:
            return ls, "library (engineer-confirmed)", None
    # 2. attached documents
    if kb is not None:
        names = list(explicit_docs or []) or kb.docs_mentioning(_alias_list(name))
        if names:
            docs = [{"name": n, "pages": kb.pages_of(n)} for n in names]
            res = spec_from_documents(name, docs, llm=llm if llm and llm.available()[0] else None, kind=kind)
            if res.spec and (res.spec.bond or res.spec.eta or res.spec.eads):
                res.spec.website = (find_product_by_name(name).website if find_product_by_name(name) else "")
                return res.spec, "attached documents", res
    # 3. earlier (unconfirmed) library entry
    for lname, ls in lib.items():
        if norm(lname) == norm(name) and ls.bond:
            return ls, "library (not confirmed)", None
    # 4. internet
    if allow_web and not settings.web.offline:
        res = lookup_on_web(name, settings, llm=llm if llm and llm.available()[0] else None, kind=kind, progress=prog)
        if res.spec is not None and (res.spec.bond or res.spec.eta):
            return res.spec, "internet", res
        return res.spec, "internet (incomplete)" if res.spec else "none", res
    return None, "none", None


def resolve_all_specs(s: Session, settings: Settings, kb: Optional[KnowledgeBase] = None, allow_web: bool = True,
                      progress: Optional[Progress] = None, explicit_docs: Optional[Dict[str, List[str]]] = None) -> None:
    llm = make_client(settings.llm)
    cache: Dict[str, tuple] = {}
    for c in s.cases:
        name = c.adhesive_name
        if not name:
            s.specs[c.id], s.spec_source[c.id] = None, "none"
            continue
        key = f"{norm(name)}|{c.kind}"
        if key not in cache:
            cache[key] = resolve_spec(name, c.kind, settings, kb, llm, allow_web, (explicit_docs or {}).get(name), progress)
        spec, src, look = cache[key]
        s.specs[c.id], s.spec_source[c.id] = spec, src
        if look is not None:
            s.lookups[c.id] = look


# ------------------------------------------------------------------------------- analysis
def kb_citations(kb: Optional[KnowledgeBase], kinds: Sequence[str]) -> List[dict]:
    out: List[dict] = []
    if kb is None or not kb.docs:
        return out
    for k in set(kinds):
        for title, q in CITATION_TOPICS.get(k, []):
            hits = kb.search(q, k=1, kinds=["standard", "guide"])
            if hits:
                h = hits[0]
                snippet = " ".join(h.text.split())[:300]
                out.append({"topic": title, "doc": h.doc, "page": h.page, "snippet": snippet, "score": h.score})
    return out


def run_analysis(s: Session, kb: Optional[KnowledgeBase] = None, library_alternatives: bool = True) -> Session:
    s.results, s.comparisons, s.alternatives, s.fixes = {}, {}, {}, {}
    lib = library.load_specs() if library_alternatives else {}
    for c in s.cases:
        spec = s.specs.get(c.id)
        res = check_case(c, spec, s.basis)
        s.results[c.id] = res
        s.comparisons[c.id] = compare_spec(c, spec)
        pool = {n: sp for n, sp in lib.items() if sp.bond}
        if spec is not None and spec.name not in pool and spec.bond:
            pool[spec.name] = spec
        if len(pool) > 1:
            s.alternatives[c.id] = screen_alternatives(c, pool, s.basis)
        if res.status == "FAIL" and spec is not None:
            s.fixes[c.id] = suggest_fixes(c, spec, s.basis)
    ordered = [s.results[c.id] for c in s.cases]
    s.verdict = verdict(ordered, s.specs)
    s.findings = build_findings(ordered, s.specs, s.comparisons, s.standards, s.rfis,
                                s.extraction.missing_notes, s.extraction.warnings, s.fixes)
    s.citations = kb_citations(kb, {c.kind for c in s.cases})
    s.kb_docs = [{"name": d.name, "kind": d.kind, "codes": d.codes, "pages": d.pages} for d in kb.list_docs()] if kb else []
    s.log.append(f"analysis run {time.strftime('%H:%M:%S')}")
    return s


def ai_executive_summary(s: Session, llm: LLMClient) -> str:
    """Optional: let an LLM phrase a short summary of the COMPUTED results (it may not add numbers)."""
    facts = {"verdict": s.verdict[0], "reason": s.verdict[1],
             "cases": [{"id": c.id, "type": c.kind, "size": c.fastener_d, "embed_mm": c.h_ef,
                        "status": s.results[c.id].status, "max_utilisation": s.results[c.id].max_utilisation,
                        "governing": s.results[c.id].governing, "adhesive": c.adhesive_name}
                       for c in s.cases if c.id in s.results],
             "top_findings": [f"{f.severity}: {f.title}" for f in s.findings[:8]]}
    prompt = ("Write a concise (max 120 words) executive summary for a structural engineering review report using ONLY "
              "the JSON facts below. Do not introduce any number, standard clause or product property that is not in the "
              "facts. Plain professional tone, no markdown.\n" + json.dumps(facts, default=str))
    try:
        return llm.chat("You write precise engineering report summaries.", prompt).strip()
    except LLMError as e:
        return ""
