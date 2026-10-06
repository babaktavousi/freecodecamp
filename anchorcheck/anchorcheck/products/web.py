"""Internet search + document fetching (with on-disk cache) for adhesive specifications."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

from ..config import WebSettings, data_dir
from ..pdfio import html_to_text, pdf_links_in_html, read_pdf

LOW_TRUST = ("scribd.com", "pinterest.", "slideshare.", "studocu.", "facebook.", "youtube.", "reddit.",
             "alibaba.", "aliexpress.", "ebay.", "amazon.")
SPEC_HINTS = ("technical data", "tds", "data sheet", "datasheet", "eta", "assessment", "technical information",
              "specifier", "design guide", "product data", "approval")


class WebError(RuntimeError):
    pass


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    score: float = 0.0
    trust: str = "unknown"          # manufacturer | third-party | low


@dataclass
class FetchedDoc:
    url: str
    kind: str                       # pdf | html
    pages: List[str]
    pdf_links: List[str] = field(default_factory=list)
    fetched_at: str = ""
    from_cache: bool = False
    local_path: str = ""
    title: str = ""


class WebClient:
    def __init__(self, settings: WebSettings, cache_dir: Optional[Path] = None):
        self.s = settings
        self.cache = Path(cache_dir) if cache_dir else data_dir() / "web_cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sess = requests.Session()
        self.sess.headers["User-Agent"] = settings.user_agent

    # ------------------------------------------------------------------ search
    def search(self, query: str, n: int = 8) -> List[SearchHit]:
        if self.s.offline:
            raise WebError("Offline mode is enabled - web search disabled.")
        engine = self.s.engine
        try:
            if engine == "brave" and self.s.brave():
                return self._brave(query, n)
            if engine == "searxng" and self.s.searx():
                return self._searx(query, n)
            return self._ddg(query, n)
        except requests.RequestException as e:
            raise WebError(f"Search failed ({engine}): {e}") from e

    def _ddg(self, q: str, n: int) -> List[SearchHit]:
        r = self.sess.post("https://html.duckduckgo.com/html/", data={"q": q}, timeout=self.s.timeout)
        r.raise_for_status()
        return parse_ddg_html(r.text)[:n]

    def _brave(self, q: str, n: int) -> List[SearchHit]:
        r = self.sess.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": n},
                          headers={"X-Subscription-Token": self.s.brave(), "Accept": "application/json"},
                          timeout=self.s.timeout)
        r.raise_for_status()
        return [SearchHit(x.get("title", ""), x.get("url", ""), re.sub("<[^>]+>", "", x.get("description", "")), "brave")
                for x in r.json().get("web", {}).get("results", [])][:n]

    def _searx(self, q: str, n: int) -> List[SearchHit]:
        r = self.sess.get(self.s.searx().rstrip("/") + "/search", params={"q": q, "format": "json"},
                          timeout=self.s.timeout)
        r.raise_for_status()
        return [SearchHit(x.get("title", ""), x.get("url", ""), x.get("content", ""), "searxng")
                for x in r.json().get("results", [])][:n]

    # ------------------------------------------------------------------- fetch
    def _key(self, url: str) -> str:
        return hashlib.sha1(url.encode()).hexdigest()

    def fetch(self, url: str, max_age_days: float = 30.0) -> FetchedDoc:
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            raise WebError(f"Refusing non-http(s) URL: {url}")
        meta_p = self.cache / (self._key(url) + ".json")
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text())
                if time.time() - meta["ts"] < max_age_days * 86400:
                    return FetchedDoc(url=url, kind=meta["kind"], pages=meta["pages"], pdf_links=meta.get("pdf_links", []),
                                      fetched_at=meta["fetched_at"], from_cache=True, local_path=meta.get("local_path", ""),
                                      title=meta.get("title", ""))
            except Exception:
                pass
        if self.s.offline:
            raise WebError("Offline mode is enabled - cannot fetch " + url)
        try:
            r = self.sess.get(url, timeout=self.s.timeout, stream=True, allow_redirects=True)
            r.raise_for_status()
        except requests.RequestException as e:
            raise WebError(f"Fetch failed for {url}: {e}") from e
        clen = int(r.headers.get("content-length") or 0)
        if clen > self.s.max_bytes:
            raise WebError(f"Document too large ({clen} bytes).")
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > self.s.max_bytes:
                raise WebError("Document exceeds size limit.")
        ctype = (r.headers.get("content-type") or "").lower()
        is_pdf = "pdf" in ctype or body[:5] == b"%PDF-" or u.path.lower().endswith(".pdf")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if is_pdf:
            path = self.cache / (self._key(url) + ".pdf")
            path.write_bytes(body)
            pages = [p.text for p in read_pdf(body)]
            doc = FetchedDoc(url, "pdf", pages, [], now, False, str(path), title=Path(u.path).name)
        else:
            html = body.decode(r.encoding or r.apparent_encoding or "utf-8", errors="replace")
            tm = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            doc = FetchedDoc(url, "html", [html_to_text(html)], pdf_links_in_html(html, url), now, False, "",
                             title=re.sub(r"\s+", " ", tm.group(1)).strip() if tm else "")
        meta_p.write_text(json.dumps({"ts": time.time(), "kind": doc.kind, "pages": doc.pages, "pdf_links": doc.pdf_links,
                                      "fetched_at": doc.fetched_at, "local_path": doc.local_path, "title": doc.title}))
        return doc


def parse_ddg_html(html: str) -> List[SearchHit]:
    hits: List[SearchHit] = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if "uddg=" in href:
            qs = parse_qs(urlparse(href if href.startswith("http") else "https:" + href).query)
            href = unquote(qs.get("uddg", [href])[0])
        snip = ""
        tail = html[m.end(): m.end() + 1800]
        sm = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', tail, re.S)
        if sm:
            snip = re.sub(r"<[^>]+>", "", sm.group(1))
        hits.append(SearchHit(re.sub(r"\s+", " ", title).strip(), href, re.sub(r"\s+", " ", snip).strip(), "duckduckgo"))
    return hits


def rank_hits(hits: List[SearchHit], product: str, manufacturer_domain: str = "") -> List[SearchHit]:
    toks = [t for t in re.split(r"\W+", product.lower()) if len(t) > 1]
    out = []
    for h in hits:
        blob = f"{h.title} {h.url} {h.snippet}".lower()
        dom = urlparse(h.url).netloc.lower()
        sc = 0.0
        if h.url.lower().split("?")[0].endswith(".pdf"):
            sc += 3
        if manufacturer_domain and manufacturer_domain in dom:
            sc += 3
            h.trust = "manufacturer"
        elif any(x in dom for x in LOW_TRUST):
            sc -= 4
            h.trust = "low"
        else:
            h.trust = "third-party"
        sc += 2 * any(k in blob for k in SPEC_HINTS)
        if toks:
            sc += 3.0 * sum(t in blob for t in toks) / len(toks)
        h.score = round(sc, 2)
        out.append(h)
    return sorted(out, key=lambda h: -h.score)
