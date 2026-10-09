"""Model worker interface. Workers run one bounded job; routing and budgets live elsewhere."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from lark.records import LLMCall


@dataclass
class LLMRequest:
    role: str
    system: str
    prompt: str
    schema: dict[str, Any] | None = None  # JSON Schema the reply must satisfy
    max_output_tokens: int = 2000
    timeout_s: int = 300
    job_id: str | None = None
    prompt_version: str | None = None


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None
    error_kind: str | None = None  # auth | rate_limit | timeout | invalid_output | unavailable | other
    call: LLMCall | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class Worker(Protocol):
    route: str
    provider: str
    model: str
    billing: str

    def available(self) -> tuple[bool, str]:
        """Cheap local check (binary present, credentials configured). No model call."""
        ...

    def run(self, req: LLMRequest) -> LLMResult: ...


def extract_json(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model text, tolerating code fences and surrounding prose."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, flags=re.S)
    if m:
        t = m.group(1)
    try:
        val = json.loads(t)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            val = json.loads(t[start : end + 1])
            return val if isinstance(val, dict) else None
        except json.JSONDecodeError:
            return None
    return None
