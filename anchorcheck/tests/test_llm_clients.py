"""LLM adapters: verify the exact HTTP request each provider receives (no network, no model needed)."""
import base64
import json
from unittest import mock

import pytest

from anchorcheck.config import LLMSettings, Settings, WebSettings
from anchorcheck.llm import clients
from anchorcheck.llm.clients import AnthropicClient, LLMError, NullClient, OllamaClient, OpenAICompatClient, make_client

PNG = b"\x89PNG\r\n\x1a\nfake"


class Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise clients.requests.HTTPError(str(self.status_code))


def test_factory_picks_provider_and_defaults_to_null():
    assert isinstance(make_client(LLMSettings(provider="ollama")), OllamaClient)
    assert isinstance(make_client(LLMSettings(provider="anthropic")), AnthropicClient)
    assert isinstance(make_client(LLMSettings(provider="openai")), OpenAICompatClient)
    nc = make_client(LLMSettings())
    assert isinstance(nc, NullClient) and nc.available()[0] is False
    with pytest.raises(LLMError):
        nc.chat("s", "p")


def test_ollama_chat_request_shape_with_image_and_json_mode():
    s = LLMSettings(provider="ollama", model="llama3.1:8b", vision_model="qwen2.5vl:7b", host="http://localhost:11434/")
    with mock.patch.object(clients.requests, "post", return_value=Resp({"message": {"content": '{"ok": true}'}})) as post:
        out = OllamaClient(s).chat("sys", "read this", images=[PNG], json_mode=True)
    assert out == '{"ok": true}'
    url, kw = post.call_args[0][0], post.call_args[1]
    body = kw["json"]
    assert url == "http://localhost:11434/api/chat"
    assert body["model"] == "qwen2.5vl:7b"                       # vision model used when images are sent
    assert body["format"] == "json" and body["stream"] is False and body["options"]["temperature"] == 0.0
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1]["images"] == [base64.b64encode(PNG).decode()]


def test_ollama_text_call_uses_text_model_and_no_images():
    s = LLMSettings(provider="ollama", model="llama3.1:8b", vision_model="qwen2.5vl:7b")
    with mock.patch.object(clients.requests, "post", return_value=Resp({"message": {"content": "hi"}})) as post:
        OllamaClient(s).chat("", "hello")
    body = post.call_args[1]["json"]
    assert body["model"] == "llama3.1:8b" and "images" not in body["messages"][-1] and "format" not in body


def test_ollama_unreachable_gives_actionable_error():
    with mock.patch.object(clients.requests, "get", side_effect=clients.requests.ConnectionError("refused")):
        ok, msg = OllamaClient(LLMSettings(provider="ollama", model="x")).available()
    assert not ok and "ollama serve" in msg


def test_ollama_missing_model_message():
    with mock.patch.object(clients.requests, "get", return_value=Resp({"models": [{"name": "llama3.1:8b"}]})):
        ok, msg = OllamaClient(LLMSettings(provider="ollama", model="qwen2.5vl")).available()
    assert not ok and "ollama pull qwen2.5vl" in msg


def test_ollama_embeddings_request():
    with mock.patch.object(clients.requests, "post", return_value=Resp({"embedding": [0.1, 0.2]})) as post:
        out = OllamaClient(LLMSettings(provider="ollama")).embed(["a", "b"], "nomic-embed-text")
    assert out == [[0.1, 0.2], [0.1, 0.2]] and post.call_args[0][0].endswith("/api/embeddings")


def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    s = LLMSettings(provider="anthropic", model="claude-sonnet-5-5")
    reply = {"content": [{"type": "text", "text": "{\"a\":1}"}]}
    with mock.patch.object(clients.requests, "post", return_value=Resp(reply)) as post:
        out = AnthropicClient(s).chat("sys", "describe", images=[PNG])
    assert out == '{"a":1}'
    kw = post.call_args[1]
    assert kw["headers"]["x-api-key"] == "sk-test" and kw["headers"]["anthropic-version"] == "2023-06-01"
    body = kw["json"]
    assert body["model"] == "claude-sonnet-5-5" and body["system"] == "sys"
    blocks = body["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == base64.b64encode(PNG).decode() and blocks[-1] == {"type": "text", "text": "describe"}


def test_anthropic_without_key_fails_cleanly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = AnthropicClient(LLMSettings(provider="anthropic", model="m"))
    assert c.available()[0] is False
    with pytest.raises(LLMError):
        c.chat("", "x")


def test_openai_compat_request_shape_for_local_server():
    s = LLMSettings(provider="openai", model="local-vlm", base_url="http://localhost:1234/v1")
    with mock.patch.object(clients.requests, "post", return_value=Resp({"choices": [{"message": {"content": "{}"}}]})) as post:
        OpenAICompatClient(s).chat("sys", "p", images=[PNG], json_mode=True)
    kw = post.call_args[1]
    assert post.call_args[0][0] == "http://localhost:1234/v1/chat/completions"
    assert "authorization" not in kw["headers"]                      # local server: no key required
    body = kw["json"]
    assert body["response_format"] == {"type": "json_object"}
    parts = body["messages"][1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_http_error_is_wrapped():
    with mock.patch.object(clients.requests, "post", return_value=Resp({"error": "bad"}, 500)):
        with pytest.raises(LLMError):
            OllamaClient(LLMSettings(provider="ollama", model="m")).chat("", "x")


def test_ask_json_retries_once_then_raises():
    calls = []

    class C(clients.LLMClient):
        def chat(self, system, prompt, images=None, json_mode=False, model=None):
            calls.append(prompt)
            return "not json" if len(calls) < 2 else '{"ok": 1}'
    assert C(LLMSettings()).ask_json("s", "p") == {"ok": 1} and len(calls) == 2
    calls.clear()

    class D(C):
        def chat(self, *a, **k):
            calls.append(1)
            return "never json"
    with pytest.raises(LLMError):
        D(LLMSettings()).ask_json("s", "p")
    assert len(calls) == 2


def test_settings_never_persist_secrets(tmp_path):
    s = Settings()
    s.llm.api_key, s.web.brave_key = "SECRET1", "SECRET2"
    p = s.save(tmp_path / "settings.json")
    text = p.read_text()
    assert "SECRET1" not in text and "SECRET2" not in text
    assert Settings.load(p).llm.api_key == ""
