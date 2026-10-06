"""Find and parse the best available specification for an adhesive product.

Priority:  1) documents the user attached (knowledge base)   2) the engineer's saved library
           3) the internet (manufacturer sites first)         4) manual entry (UI)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..config import Settings
from ..models import AdhesiveSpec, Provenance
from .library import merge_specs
from .registry import ProductInfo, find_product_by_name, norm
from .tds import ParseResult, llm_fill_missing, parse_spec_text
from .web import FetchedDoc, SearchHit, WebClient, WebError, rank_hits


@dataclass
class LookupAttempt:
    url: str
    title: str
    ok: bool
    note: str = ""
    score: float = 0.0
    trust: str = ""


@dataclass
class LookupResult:
    spec: Optional[AdhesiveSpec]
    attempts: List[LookupAttempt] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    source: str = "none"          # attached | web | library | none
    missing: List[str] = field(default_factory=list)


def _score(res: ParseResult) -> float:
    return res.n_bond_rows * 1.0 + 4 * sum(res.found.values())


def spec_from_documents(name: str, docs: List[dict], llm=None, kind: str = "anchor") -> LookupResult:
    """Parse user-attached documents (each: {"name":..., "pages":[...], "kind": "TDS"|"ETA"|...})."""
    out = LookupResult(spec=None, source="attached")
    best: Optional[AdhesiveSpec] = None
    for d in docs:
        res = parse_spec_text(d["pages"], name=name, doc_name=d["name"], source="attached document")
        if llm is not None and not res.spec.bond:
            out.notes += llm_fill_missing(res, d["pages"], llm, d["name"])
        out.attempts.append(LookupAttempt(d["name"], d["name"], bool(res.spec.bond or res.found.get("approvals")),
                                          f"bond entries: {len(res.spec.bond)}; found: "
                                          f"{', '.join(k for k, v in res.found.items() if v)}", _score(res)))
        if best is None:
            best = res.spec
            best.name = name or best.name
        else:
            merge_specs(best, res.spec)
    out.spec = best
    if best is not None:
        out.missing = ParseResult(best).missing_for_design(kind)
    return out


def search_queries(name: str, product: Optional[ProductInfo]) -> List[str]:
    man = product.manufacturer if product else ""
    base = f"{man} {name}".strip()
    q = [f"{base} technical data sheet pdf", f"{base} European Technical Assessment ETA characteristic bond resistance",
         f"{base} AS 5216 design guide anchor"]
    if product and product.website:
        q.insert(0, f"site:{product.website} {name} technical data sheet")
    return q


def lookup_on_web(name: str, settings: Settings, llm=None, kind: str = "anchor", max_docs: int = 4,
                  progress: Optional[Callable[[str], None]] = None, client: Optional[WebClient] = None) -> LookupResult:
    prog = progress or (lambda m: None)
    out = LookupResult(spec=None, source="web")
    if settings.web.offline:
        out.notes.append("Offline mode: no web lookup performed.")
        return out
    client = client or WebClient(settings.web)
    prod = find_product_by_name(name)
    domain = prod.website if prod else ""
    hits: List[SearchHit] = []
    seen = set()
    for q in search_queries(name, prod):
        prog(f"Searching: {q}")
        try:
            for h in client.search(q, 8):
                if h.url not in seen:
                    seen.add(h.url)
                    hits.append(h)
        except WebError as e:
            out.notes.append(str(e))
            break
    ranked = rank_hits(hits, name, domain)
    if not ranked:
        out.notes.append("No search results (check the internet connection / search engine setting).")
        return out
    best: Optional[AdhesiveSpec] = None
    best_score = -1.0
    tried = 0
    for h in ranked:
        if tried >= max_docs or h.trust == "low":
            continue
        tried += 1
        prog(f"Fetching: {h.url}")
        try:
            doc = client.fetch(h.url)
        except WebError as e:
            out.attempts.append(LookupAttempt(h.url, h.title, False, str(e), h.score, h.trust))
            continue
        docs = [doc]
        # HTML product pages: follow PDF links that look like specs
        if doc.kind == "html":
            for link in doc.pdf_links[:6]:
                if any(k in link.lower() for k in ("tds", "data", "eta", "technical", "spec")):
                    try:
                        docs.append(client.fetch(link))
                    except WebError:
                        pass
        for d in docs:
            res = parse_spec_text(d.pages, name=name, doc_name=d.title or d.url, source="web", url=d.url)
            for p in list(res.spec.provenance.values()) + [b.prov for b in res.spec.bond]:
                p.note = (p.note + " " if p.note else "") + f"retrieved {d.fetched_at}"
            if llm is not None and not res.spec.bond and d.kind == "pdf":
                out.notes += llm_fill_missing(res, d.pages, llm, d.url)
            sc = _score(res)
            out.attempts.append(LookupAttempt(d.url, d.title, sc > 0, f"bond entries {len(res.spec.bond)}", sc, h.trust))
            if best is None:
                best, best_score = res.spec, sc
                best.name = name
                best.website = domain
            else:
                if sc > 0:
                    merge_specs(best, res.spec)
                best_score = max(best_score, sc)
    out.spec = best
    if best is not None:
        out.missing = ParseResult(best).missing_for_design(kind)
        if not best.bond:
            out.notes.append("Documents were found but no bond-resistance table could be read automatically - "
                             "attach the TDS/ETA PDF or enter values manually.")
    return out


def lookup_from_url(name: str, url: str, settings: Settings, llm=None, kind: str = "anchor",
                    client: Optional[WebClient] = None) -> LookupResult:
    """Fetch and parse ONE document at a user-supplied link (independent of any search engine)."""
    out = LookupResult(spec=None, source="web (user link)")
    if settings.web.offline:
        out.notes.append("Offline mode: cannot fetch the link.")
        return out
    client = client or WebClient(settings.web)
    try:
        doc = client.fetch(url)
    except WebError as e:
        out.attempts.append(LookupAttempt(url, "", False, str(e)))
        out.notes.append(str(e))
        return out
    docs = [doc]
    if doc.kind == "html":
        for link in doc.pdf_links[:6]:
            if any(k in link.lower() for k in ("tds", "data", "eta", "technical", "spec")):
                try:
                    docs.append(client.fetch(link))
                except WebError:
                    pass
    best: Optional[AdhesiveSpec] = None
    for d in docs:
        res = parse_spec_text(d.pages, name=name, doc_name=d.title or d.url, source="web (user link)", url=d.url)
        for p in list(res.spec.provenance.values()) + [b.prov for b in res.spec.bond]:
            p.note = (p.note + " " if p.note else "") + f"retrieved {d.fetched_at}"
        if llm is not None and not res.spec.bond and d.kind == "pdf":
            out.notes += llm_fill_missing(res, d.pages, llm, d.url)
        out.attempts.append(LookupAttempt(d.url, d.title, bool(res.spec.bond or res.spec.eta), f"bond entries {len(res.spec.bond)}",
                                          _score(res), "user-supplied"))
        if best is None:
            best = res.spec
            best.name = name
        elif _score(res) > 0:
            merge_specs(best, res.spec)
    out.spec = best
    if best is not None:
        out.missing = ParseResult(best).missing_for_design(kind)
    return out
