"""Runtime settings.  Secrets (API keys) are read from the environment or the UI session and
are never written to disk by this module."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


def data_dir() -> Path:
    p = Path(os.environ.get("ANCHORCHECK_HOME", Path.home() / ".anchorcheck"))
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class LLMSettings:
    provider: str = "none"                  # none | ollama | anthropic | openai
    model: str = ""                         # text model
    vision_model: str = ""                  # model used for drawing images (defaults to `model`)
    host: str = "http://localhost:11434"    # Ollama
    base_url: str = "https://api.openai.com/v1"   # OpenAI-compatible (LM Studio, vLLM, OpenRouter...)
    api_key: str = ""                       # session only; env var used when empty
    temperature: float = 0.0
    timeout: int = 240
    num_ctx: int = 8192

    def key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY", "")
        if self.provider == "openai":
            return os.environ.get("OPENAI_API_KEY", "")
        return ""


@dataclass
class WebSettings:
    offline: bool = False
    engine: str = "duckduckgo"              # duckduckgo | brave | searxng
    brave_key: str = ""                     # or env BRAVE_API_KEY
    searxng_url: str = ""                   # or env SEARXNG_URL
    timeout: int = 25
    max_bytes: int = 30_000_000
    user_agent: str = ("Mozilla/5.0 (X11; Linux x86_64) AnchorCheck/0.1 "
                       "(engineering review tool; contact: local user)")

    def brave(self) -> str:
        return self.brave_key or os.environ.get("BRAVE_API_KEY", "")

    def searx(self) -> str:
        return self.searxng_url or os.environ.get("SEARXNG_URL", "")


@dataclass
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    web: WebSettings = field(default_factory=WebSettings)
    embed_model: str = ""                   # Ollama embedding model for the knowledge base (optional)
    project_ref: str = ""
    engineer: str = ""

    # ---- persistence (non-secret fields only)
    def to_json(self) -> str:
        d = asdict(self)
        d["llm"]["api_key"] = ""
        d["web"]["brave_key"] = ""
        return json.dumps(d, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        s = cls()
        for k, v in (d.get("llm") or {}).items():
            if hasattr(s.llm, k):
                setattr(s.llm, k, v)
        for k, v in (d.get("web") or {}).items():
            if hasattr(s.web, k):
                setattr(s.web, k, v)
        for k in ("embed_model", "project_ref", "engineer"):
            if k in d:
                setattr(s, k, d[k])
        return s

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or data_dir() / "settings.json"
        path.write_text(self.to_json())
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        path = path or data_dir() / "settings.json"
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text()))
            except Exception:
                pass
        return cls()
