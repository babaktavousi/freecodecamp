"""Free-tier AI model check. Works with any OpenAI-compatible chat endpoint.

Presets (all have a free tier / are free to run):
  pollinations : no API key needed (default)
  groq         : free API key  -> https://console.groq.com
  openrouter   : free ':free' models, free key
  gemini       : free API key from Google AI Studio (OpenAI-compatible endpoint)
  ollama       : local models, no key, no internet
Configuration comes from config.json (next to main.py) or environment variables
BPSIM_AI_PROVIDER / BPSIM_AI_KEY / BPSIM_AI_MODEL / BPSIM_AI_URL.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog as C
from .fixes import OPS, apply_action
from .model import PlantModel
from .rules import Fix, Issue, verify_rules

PRESETS = {
    "pollinations": {"base_url": "https://text.pollinations.ai/openai", "model": "openai", "key_required": False},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "key_required": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "meta-llama/llama-3.3-70b-instruct:free", "key_required": True},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.0-flash", "key_required": True},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1", "key_required": False},
}
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

SYSTEM = """You are a senior process engineer who audits Visio models of concrete/cement batching plants.
Plant logic: storage (aggregate_bin, cement_silo, water_tank, admixture_tank) -> optional transfer equipment
(belt_conveyor for aggregate, screw_conveyor for cement, pump for water/admixture) -> weigh_hopper (one per material
family) -> mixer -> optional discharge_hopper -> loadout (truck). A controller (PLC) is linked by control signals to the
mixer and weigh hoppers; a dust_collector is optional. Allowed material-flow connections (from -> to):
%s
A model is COMPLETE only if there is at least one aggregate source, cement source, water source, mixer, loadout and
controller, every material reaches a mixer through a weigh hopper, the mixer reaches a loadout, nothing is dangling or
looped, and every connection is physically valid. Answer with ONE JSON object and nothing else:
{"complete": true|false, "summary": "...",
 "issues": [{"severity": "error|warning", "message": "...", "equipment": ["ID"]}],
 "steps": [{"title": "...", "why": "...", "actions": [
    {"op": "add_equipment", "type": "<type>", "id": "<NEW-ID>"} |
    {"op": "add_connection", "from": "ID", "to": "ID"} | {"op": "remove_connection", "from": "ID", "to": "ID"} |
    {"op": "reverse_connection", "from": "ID", "to": "ID"} | {"op": "insert_between", "from": "ID", "to": "ID", "type": "<type>"} |
    {"op": "set_type", "id": "ID", "type": "<type>"} | {"op": "remove_equipment", "id": "ID"}]}]}
Valid types: %s. List steps in the order they must be performed; each step must be small and concrete.
If the model is complete, return an empty steps list.
Reasoning: low. Keep every text field under 25 words."""


@dataclass
class AIConfig:
    provider: str = "pollinations"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 60

    @classmethod
    def load(cls) -> "AIConfig":
        data: dict = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        env = os.environ
        cfg = cls(provider=env.get("BPSIM_AI_PROVIDER", data.get("provider", "pollinations")),
                  base_url=env.get("BPSIM_AI_URL", data.get("base_url", "")),
                  api_key=env.get("BPSIM_AI_KEY", data.get("api_key", "")),
                  model=env.get("BPSIM_AI_MODEL", data.get("model", "")),
                  timeout=int(data.get("timeout", 60)))
        preset = PRESETS.get(cfg.provider, {})
        cfg.base_url = cfg.base_url or preset.get("base_url", "")
        cfg.model = cfg.model or preset.get("model", "")
        return cfg

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


@dataclass
class AIResult:
    available: bool = False
    complete: bool | None = None
    summary: str = ""
    issues: list[Issue] = field(default_factory=list)
    steps: list[Fix] = field(default_factory=list)
    error: str = ""
    raw: str = ""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in reply")
    return json.loads(text[a:b + 1])


class AIClient:
    def __init__(self, cfg: AIConfig | None = None) -> None:
        self.cfg = cfg or AIConfig.load()

    def chat(self, system: str, user: str) -> str:
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        if self.cfg.provider == "pollinations":
            url = self.cfg.base_url.rstrip("/")
        body = {"model": self.cfg.model, "temperature": 0, "max_tokens": 4000,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        headers = {"Content-Type": "application/json", "User-Agent": "bpsim/1.0"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        req = urllib.request.Request(url, json.dumps(body).encode(), headers)
        ctx = ssl.create_default_context()
        bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        if bundle and os.path.exists(bundle):
            ctx.load_verify_locations(bundle)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout, context=ctx) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (402, 429):
                raise RuntimeError(f"AI service HTTP {e.code}: the free quota/rate limit is used up for now. Wait a minute, "
                                  f"or choose another free provider with your own free key (Settings > AI: Groq, Gemini, "
                                  f"OpenRouter) or a local Ollama model.") from e
            raise RuntimeError(f"AI service HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"AI service unreachable: {e}") from e
        try:
            ch = data["choices"][0]
            content = ch["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise RuntimeError(f"unexpected AI reply: {str(data)[:200]}") from e
        if not content:
            raise RuntimeError(f"AI returned no answer (finish_reason={ch.get('finish_reason')}); try again or pick another model")
        return content

    # -- model audit ---------------------------------------------------------
    def analyze_model(self, m: PlantModel, rule_issues: list[Issue]) -> AIResult:
        res = AIResult()
        if PRESETS.get(self.cfg.provider, {}).get("key_required") and not self.cfg.api_key:
            res.error = f"provider '{self.cfg.provider}' needs an API key (Settings → AI)."
            return res
        allowed = "\n".join(f"  {a} -> {', '.join(sorted(b)) or '(none)'}" for a, b in C.ALLOWED.items())
        system = SYSTEM % (allowed, ", ".join(C.TYPES))
        user = json.dumps({"model": {"equipment": [{"id": e.id, "type": e.type, "label": e.label, "material": e.material}
                                                   for e in m.equipment.values()],
                                     "connections": [{"from": c.src, "to": c.dst, "kind": c.kind} for c in m.connections]},
                           "rule_engine_findings": [f"{i.severity}: {i.message}" for i in rule_issues]})
        data = None
        for attempt in range(2):
            try:
                res.raw = self.chat(system, user)
                data = _extract_json(res.raw)
                break
            except (RuntimeError, ValueError) as e:
                res.error = str(e)
        if data is None:
            return res
        res.error = ""
        res.available = True
        res.complete = bool(data.get("complete"))
        res.summary = str(data.get("summary", ""))
        for it in data.get("issues", []) or []:
            if isinstance(it, dict) and it.get("message"):
                sev = it.get("severity", "warning")
                res.issues.append(Issue("AI", "error" if sev == "error" else "warning", str(it["message"]),
                                        [str(x) for x in it.get("equipment", []) or []], origin="ai"))
        res.steps = validate_ai_steps(m, data.get("steps", []))
        return res


def validate_ai_steps(m: PlantModel, steps) -> list[Fix]:
    """Keep only AI steps that apply cleanly to the model and do not introduce new rule errors."""
    good: list[Fix] = []
    work = m.copy()
    for st in steps if isinstance(steps, list) else []:
        if not isinstance(st, dict) or not isinstance(st.get("actions"), list):
            continue
        acts = [a for a in st["actions"] if isinstance(a, dict) and a.get("op") in OPS and a.get("op") != "manual"]
        if not acts:
            continue
        trial = work.copy()
        before = sum(i.severity == "error" for i in verify_rules(trial))
        if not all(apply_action(trial, a)[0] for a in acts):
            continue
        after = sum(i.severity == "error" for i in verify_rules(trial))
        if after > before + 1:
            continue
        work = trial
        good.append(Fix(str(st.get("title", "AI suggestion")), str(st.get("why", "")), acts, origin="ai"))
    return good
