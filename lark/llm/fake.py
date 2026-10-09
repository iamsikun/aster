"""Deterministic offline worker for tests and `--fake-llm` runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import ClassVar

from lark.llm.base import LLMRequest, LLMResult
from lark.llm.schema import synthesize


class FakeWorker:
    provider = "fake"

    # role -> callable(req) -> dict. Register in tests or offline runs for realistic output.
    handlers: ClassVar[dict[str, Callable[[LLMRequest], dict]]] = {}

    def __init__(self, route: str = "fake", cfg: dict | None = None):
        cfg = cfg or {}
        self.route = route
        self.cfg = cfg
        self.model = cfg.get("model", "fake-1")
        self.billing = cfg.get("billing", "none")
        self.calls: list[LLMRequest] = []

    def available(self) -> tuple[bool, str]:
        return True, "fake"

    def run(self, req: LLMRequest) -> LLMResult:
        self.calls.append(req)
        handler = self.handlers.get(req.role)
        data = handler(req) if handler else synthesize(req.schema)
        if not isinstance(data, dict):
            data = {"value": data}
        text = json.dumps(data)
        usage = {"input_tokens": len(req.system + req.prompt) // 4, "output_tokens": len(text) // 4, "latency_s": 0.0}
        return LLMResult(ok=True, text=text, data=data if req.schema else None, usage=usage)
