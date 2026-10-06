"""Tolerant JSON extraction from LLM output (local models often add prose / code fences)."""
from __future__ import annotations

import json
import re
from typing import Any, Optional


def _balanced(text: str, start: int) -> Optional[str]:
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _repair(s: str) -> str:
    s = re.sub(r",\s*([}\]])", r"\1", s)                    # trailing commas
    s = re.sub(r"(?<!\\)'([A-Za-z0-9_ ]+)'\s*:", r'"\1":', s)    # single-quoted keys
    s = re.sub(r"\bNone\b", "null", s)
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    return s


def extract_json(text: str) -> Any:
    """Return the first JSON object/array found in `text`, or raise ValueError."""
    if not text:
        raise ValueError("empty response")
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    candidates = [fence.group(1)] if fence else []
    candidates.append(text)
    for cand in candidates:
        for m in re.finditer(r"[{\[]", cand):
            frag = _balanced(cand, m.start())
            if not frag:
                continue
            for attempt in (frag, _repair(frag)):
                try:
                    return json.loads(attempt)
                except Exception:
                    continue
    raise ValueError("no JSON found in model output")
