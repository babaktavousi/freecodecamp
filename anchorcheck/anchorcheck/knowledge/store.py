"""Local knowledge base for user-attached reference documents (AS standards, TDS, ETA, design guides).

"Training" here means *retrieval*: attached documents are chunked, indexed (BM25, plus optional Ollama
embeddings for hybrid search) and kept on this PC.  They are used to
  * parse product data (preferred over anything found on the internet),
  * cite the relevant passage of the attached standard in the report,
  * give a language model grounded context.
No model weights are changed and nothing leaves the machine except what the chosen LLM provider receives.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..config import data_dir
from ..pdfio import read_pdf

TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-/]*", re.I)
STOP = set("the of and to in a is for be as or by on with that this are at from an it not shall may which".split())
CODE_RE = re.compile(r"\bAS(?:\s*/\s*NZS)?\s*(\d{3,4}(?:\.\d{1,2})?)\s*(?:[-:]\s*(\d{4}))?", re.I)


def tokenize(text: str) -> List[str]:
    toks = [t.lower().strip(".-/") for t in TOKEN.findall(text or "")]
    return [t for t in toks if t and t not in STOP and len(t) > 1]


@dataclass
class Chunk:
    doc: str
    page: int
    text: str
    emb: Optional[List[float]] = None


@dataclass
class Hit:
    doc: str
    page: int
    text: str
    score: float
    kind: str = ""


@dataclass
class DocInfo:
    name: str
    kind: str                       # standard | tds | eta | guide | other
    pages: int
    codes: List[str] = field(default_factory=list)
    added: str = ""
    sha1: str = ""


def guess_kind(first_text: str, name: str = "") -> str:
    t = re.sub(r"\s+", " ", (first_text or "")[:6000].lower())
    n = name.lower()
    if "european technical assessment" in t or re.search(r"\beta[- ]?\d{2}/\d{4}", t):
        return "eta"
    if re.search(r"technical data sheet|\btds\b|product data sheet|datasheet", t + " " + n):
        return "tds"
    if re.search(r"standards australia|standards new zealand|\bas\s?\d{3,4}", t[:2500]) and \
            re.search(r"design of|standard|specification|requirements", t[:2500]):
        return "standard"
    if re.search(r"design guide|specifier|resource book|technical note|manual", t + " " + n):
        return "guide"
    return "other"


def detect_codes(text: str, limit: int = 3) -> List[str]:
    cnt: Counter = Counter()
    for m in CODE_RE.finditer((text or "")[:8000]):
        code = f"AS {m.group(1)}" + (f":{m.group(2)}" if m.group(2) else "")
        cnt[code] += 1
    return [c for c, _ in cnt.most_common(limit)]


class KnowledgeBase:
    def __init__(self, path: Optional[Path] = None, embed_fn: Optional[Callable[[Sequence[str]], List[List[float]]]] = None):
        self.path = Path(path) if path else data_dir() / "kb"
        self.path.mkdir(parents=True, exist_ok=True)
        self.index_file = self.path / "index.json"
        self.embed_fn = embed_fn
        self.docs: Dict[str, DocInfo] = {}
        self.chunks: List[Chunk] = []
        self.pages_text: Dict[str, List[str]] = {}
        self._bm25_ready = False
        self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> None:
        if not self.index_file.exists():
            return
        try:
            d = json.loads(self.index_file.read_text())
        except Exception:
            return
        self.docs = {n: DocInfo(**v) for n, v in d.get("docs", {}).items()}
        self.chunks = [Chunk(**c) for c in d.get("chunks", [])]
        self.pages_text = d.get("pages_text", {})

    def save(self) -> None:
        d = {"docs": {n: vars(v) for n, v in self.docs.items()},
             "chunks": [vars(c) for c in self.chunks], "pages_text": self.pages_text}
        self.index_file.write_text(json.dumps(d))

    # -------------------------------------------------------------------- adding
    def add_pages(self, name: str, pages: List[str], kind: Optional[str] = None) -> DocInfo:
        sha = hashlib.sha1("\n".join(pages).encode("utf-8", "ignore")).hexdigest()
        if name in self.docs and self.docs[name].sha1 == sha:
            return self.docs[name]
        self.remove(name, save=False)
        k = kind or guess_kind(pages[0] if pages else "", name)
        info = DocInfo(name=name, kind=k, pages=len(pages), codes=detect_codes(" ".join(pages[:2])),
                       added=time.strftime("%Y-%m-%d %H:%M"), sha1=sha)
        new_chunks: List[Chunk] = []
        for pno, txt in enumerate(pages, start=1):
            for piece in chunk_text(txt):
                new_chunks.append(Chunk(name, pno, piece))
        if self.embed_fn and new_chunks:
            try:
                embs = self.embed_fn([c.text for c in new_chunks])
                for c, e in zip(new_chunks, embs):
                    c.emb = e
            except Exception:
                pass       # keyword search still works
        self.docs[name] = info
        self.chunks.extend(new_chunks)
        self.pages_text[name] = pages
        self._bm25_ready = False
        self.save()
        return info

    def add_pdf(self, src, name: str, kind: Optional[str] = None) -> DocInfo:
        pages = [p.text for p in read_pdf(src)]
        if not any(len(p) > 30 for p in pages):
            raise ValueError(f"'{name}' has no extractable text layer (scanned PDF?). OCR it first.")
        return self.add_pages(name, pages, kind)

    def remove(self, name: str, save: bool = True) -> None:
        self.docs.pop(name, None)
        self.pages_text.pop(name, None)
        self.chunks = [c for c in self.chunks if c.doc != name]
        self._bm25_ready = False
        if save:
            self.save()

    # -------------------------------------------------------------------- search
    def _prep(self) -> None:
        self._tok = [tokenize(c.text) for c in self.chunks]
        self._df: Counter = Counter()
        for t in self._tok:
            self._df.update(set(t))
        self._avg = (sum(len(t) for t in self._tok) / len(self._tok)) if self._tok else 1.0
        self._bm25_ready = True

    def _bm25(self, q: List[str], i: int, k1: float = 1.5, b: float = 0.75) -> float:
        t = self._tok[i]
        if not t:
            return 0.0
        tf = Counter(t)
        n = len(self._tok)
        s = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (n - self._df[w] + 0.5) / (self._df[w] + 0.5))
            s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / self._avg))
        return s

    def search(self, query: str, k: int = 5, kinds: Optional[Sequence[str]] = None,
               docs: Optional[Sequence[str]] = None) -> List[Hit]:
        if not self.chunks:
            return []
        if not self._bm25_ready:
            self._prep()
        q = tokenize(query)
        scores = [self._bm25(q, i) for i in range(len(self.chunks))]
        mx = max(scores) or 1.0
        scores = [s / mx for s in scores]
        if self.embed_fn and any(c.emb for c in self.chunks):
            try:
                qe = self.embed_fn([query])[0]
                for i, c in enumerate(self.chunks):
                    if c.emb:
                        scores[i] = 0.5 * scores[i] + 0.5 * max(0.0, cosine(qe, c.emb))
            except Exception:
                pass
        order = sorted(range(len(self.chunks)), key=lambda i: -scores[i])
        out: List[Hit] = []
        for i in order:
            c = self.chunks[i]
            info = self.docs.get(c.doc)
            if kinds and info and info.kind not in kinds:
                continue
            if docs and c.doc not in docs:
                continue
            if scores[i] <= 0:
                break
            out.append(Hit(c.doc, c.page, c.text, round(scores[i], 3), info.kind if info else ""))
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------------- queries
    def list_docs(self) -> List[DocInfo]:
        return sorted(self.docs.values(), key=lambda d: d.name)

    def standards_summary(self) -> List[dict]:
        return [{"name": d.name, "codes": d.codes, "kind": d.kind} for d in self.docs.values()]

    def docs_mentioning(self, aliases: Sequence[str], kinds: Sequence[str] = ("tds", "eta", "guide", "other")) -> List[str]:
        """Names of attached documents that mention any alias (product-name matching)."""
        from ..products.registry import norm
        al = [norm(a) for a in aliases if a]
        out = []
        for name, pages in self.pages_text.items():
            if self.docs[name].kind not in kinds:
                continue
            blob = norm(" ".join(pages[:12]))
            if any(a and a in blob for a in al):
                out.append(name)
        return out

    def pages_of(self, name: str) -> List[str]:
        return self.pages_text.get(name, [])


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> List[str]:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if len(text) <= size:
        return [text] if len(text) > 20 else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0
