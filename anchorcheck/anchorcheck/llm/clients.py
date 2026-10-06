"""LLM provider adapters: Ollama (local), Anthropic, OpenAI-compatible.

All adapters expose the same tiny interface:  chat(system, prompt, images=None, json_mode=False) -> str
Images are PNG bytes.  Network errors raise LLMError with an actionable message.
"""
from __future__ import annotations

import base64
from typing import Any, List, Optional, Sequence, Tuple

import requests

from ..config import LLMSettings
from .jsonutil import extract_json


class LLMError(RuntimeError):
    pass


class LLMClient:
    name = "none"
    supports_vision = False

    def __init__(self, s: LLMSettings):
        self.s = s

    def chat(self, system: str, prompt: str, images: Optional[Sequence[bytes]] = None,
             json_mode: bool = False, model: Optional[str] = None) -> str:
        raise LLMError("No LLM provider configured.")

    def available(self) -> Tuple[bool, str]:
        return False, "No LLM provider configured (rule-based extraction only)."

    # convenience ---------------------------------------------------------
    def ask_json(self, system: str, prompt: str, images: Optional[Sequence[bytes]] = None,
                 model: Optional[str] = None, retries: int = 1) -> Any:
        last: Optional[Exception] = None
        for attempt in range(retries + 1):
            p = prompt if attempt == 0 else prompt + "\n\nReturn ONLY one valid JSON object. No prose, no code fences."
            out = self.chat(system, p, images=images, json_mode=True, model=model)
            try:
                return extract_json(out)
            except ValueError as e:
                last = e
        raise LLMError(f"Model did not return valid JSON: {last}")


class NullClient(LLMClient):
    pass


# --------------------------------------------------------------------------- Ollama
class OllamaClient(LLMClient):
    name = "ollama"
    supports_vision = True

    def _url(self, path: str) -> str:
        return self.s.host.rstrip("/") + path

    def list_models(self) -> List[str]:
        try:
            r = requests.get(self._url("/api/tags"), timeout=8)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception as e:
            raise LLMError(f"Cannot reach Ollama at {self.s.host}: {e}. Is 'ollama serve' running?") from e

    def available(self) -> Tuple[bool, str]:
        try:
            models = self.list_models()
        except LLMError as e:
            return False, str(e)
        want = self.s.model
        if want and not any(m == want or m.startswith(want + ":") or m.split(":")[0] == want for m in models):
            return False, f"Ollama is running but model '{want}' is not pulled. Run: ollama pull {want}"
        return True, f"Ollama OK ({len(models)} models)"

    def chat(self, system, prompt, images=None, json_mode=False, model=None):
        use_model = model or (self.s.vision_model if images and self.s.vision_model else self.s.model)
        if not use_model:
            raise LLMError("No Ollama model selected.")
        msg = {"role": "user", "content": prompt}
        if images:
            msg["images"] = [base64.b64encode(i).decode() for i in images]
        body: dict = {
            "model": use_model, "stream": False,
            "messages": ([{"role": "system", "content": system}] if system else []) + [msg],
            "options": {"temperature": self.s.temperature, "num_ctx": self.s.num_ctx},
        }
        if json_mode:
            body["format"] = "json"
        try:
            r = requests.post(self._url("/api/chat"), json=body, timeout=self.s.timeout)
        except requests.RequestException as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
        return (r.json().get("message") or {}).get("content", "")

    def embed(self, texts: Sequence[str], model: str) -> List[List[float]]:
        out = []
        for t in texts:
            try:
                r = requests.post(self._url("/api/embeddings"), json={"model": model, "prompt": t},
                                  timeout=self.s.timeout)
                r.raise_for_status()
                out.append(r.json()["embedding"])
            except Exception as e:
                raise LLMError(f"Ollama embedding failed: {e}") from e
        return out


# ------------------------------------------------------------------------ Anthropic
class AnthropicClient(LLMClient):
    name = "anthropic"
    supports_vision = True
    URL = "https://api.anthropic.com/v1/messages"

    def available(self):
        if not self.s.key():
            return False, "ANTHROPIC_API_KEY not set."
        if not self.s.model:
            return False, "No Anthropic model selected."
        return True, "Anthropic key present"

    def chat(self, system, prompt, images=None, json_mode=False, model=None):
        key = self.s.key()
        if not key:
            raise LLMError("ANTHROPIC_API_KEY not set.")
        content: List[dict] = []
        for img in images or []:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                        "data": base64.b64encode(img).decode()}})
        content.append({"type": "text", "text": prompt})
        body = {"model": model or self.s.model, "max_tokens": 4096, "temperature": self.s.temperature,
                "messages": [{"role": "user", "content": content}]}
        if system:
            body["system"] = system
        try:
            r = requests.post(self.URL, json=body, timeout=self.s.timeout,
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"})
        except requests.RequestException as e:
            raise LLMError(f"Anthropic request failed: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


# ------------------------------------------------------------------- OpenAI-compatible
class OpenAICompatClient(LLMClient):
    name = "openai"
    supports_vision = True

    def available(self):
        if not self.s.model:
            return False, "No model selected."
        if "openai.com" in self.s.base_url and not self.s.key():
            return False, "OPENAI_API_KEY not set."
        return True, "OpenAI-compatible endpoint configured"

    def chat(self, system, prompt, images=None, json_mode=False, model=None):
        content: Any
        if images:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(i).decode()}}
                for i in images]
        else:
            content = prompt
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body: dict = {"model": model or self.s.model, "messages": msgs, "temperature": self.s.temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"content-type": "application/json"}
        if self.s.key():
            headers["authorization"] = "Bearer " + self.s.key()
        try:
            r = requests.post(self.s.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers,
                              timeout=self.s.timeout)
        except requests.RequestException as e:
            raise LLMError(f"OpenAI-compatible request failed: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"] or ""


def make_client(s: LLMSettings) -> LLMClient:
    return {"ollama": OllamaClient, "anthropic": AnthropicClient, "openai": OpenAICompatClient}.get(
        s.provider, NullClient)(s)
