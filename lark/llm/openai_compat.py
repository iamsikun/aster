"""Any OpenAI-compatible chat endpoint; intended for Ollama or vLLM on the 4090 workstation."""

from __future__ import annotations

import os
import time

import requests

from lark.llm.base import LLMRequest, LLMResult, extract_json
from lark.llm.errors import classify_error


class OpenAICompatWorker:
    provider = "local"

    def __init__(self, route: str, cfg: dict):
        self.route = route
        self.cfg = cfg
        self.model = cfg.get("model", "gemma4:12b")
        self.billing = cfg.get("billing", "local")
        self.base_url = os.getenv("LARK_LOCAL_LLM_URL", cfg.get("base_url", "http://localhost:11434/v1")).rstrip("/")
        self.api_key = os.getenv(cfg.get("api_key_env", "LARK_LOCAL_LLM_KEY"), "")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def available(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=2)
        except requests.RequestException as e:
            return False, f"{self.base_url} unreachable ({type(e).__name__})"
        if r.status_code >= 400:
            return False, f"{self.base_url}/models -> HTTP {r.status_code}"
        return True, f"{self.base_url} reachable"

    def body(self, req: LLMRequest) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.prompt}],
            "max_tokens": req.max_output_tokens,
            "temperature": self.cfg.get("temperature", 0.1),
        }
        if req.schema:
            if self.cfg.get("json_mode") == "json_object":
                body["response_format"] = {"type": "json_object"}
            else:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "output", "schema": req.schema}}
        return body

    def run(self, req: LLMRequest) -> LLMResult:
        start = time.monotonic()
        try:
            r = requests.post(f"{self.base_url}/chat/completions", json=self.body(req), headers=self._headers(),
                              timeout=req.timeout_s)
        except requests.Timeout:
            return LLMResult(ok=False, error=f"timeout after {req.timeout_s}s", error_kind="timeout")
        except requests.RequestException as e:
            return LLMResult(ok=False, error=str(e)[:500], error_kind="unavailable")
        if r.status_code >= 400:
            msg = f"HTTP {r.status_code}: {r.text[:300]}"
            return LLMResult(ok=False, error=msg, error_kind=classify_error(msg))
        payload = r.json()
        text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        u = payload.get("usage") or {}
        usage = {
            "input_tokens": int(u.get("prompt_tokens", 0)),
            "output_tokens": int(u.get("completion_tokens", 0)),
            "latency_s": round(time.monotonic() - start, 2),
        }
        data = extract_json(text) if req.schema else None
        return LLMResult(ok=True, text=text, data=data, usage=usage)
