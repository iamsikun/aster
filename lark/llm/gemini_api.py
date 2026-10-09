"""Gemini through the API with GEMINI_API_KEY (funded by the Google AI Pro developer credit)."""

from __future__ import annotations

import os
import time

from lark.llm.base import LLMRequest, LLMResult, extract_json
from lark.llm.errors import classify_error


def estimate_cash(price: dict | None, tokens_in: int, tokens_out: int) -> float:
    if not price:
        return 0.0
    return (tokens_in * float(price.get("input_per_m", 0)) + tokens_out * float(price.get("output_per_m", 0))) / 1e6


class GeminiApiWorker:
    provider = "google"

    def __init__(self, route: str, cfg: dict):
        self.route = route
        self.cfg = cfg
        self.model = cfg.get("model", "gemini-flash-latest")
        self.billing = cfg.get("billing", "api_credit")
        self.api_key_env = cfg.get("api_key_env", "GEMINI_API_KEY")
        self._client = None

    def available(self) -> tuple[bool, str]:
        if not os.getenv(self.api_key_env):
            return False, f"{self.api_key_env} not set"
        return True, f"{self.api_key_env} set"

    def client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=os.environ[self.api_key_env])
        return self._client

    def run(self, req: LLMRequest) -> LLMResult:
        from google.genai import types

        conf = {"system_instruction": req.system, "max_output_tokens": req.max_output_tokens}
        if req.schema:
            conf["response_mime_type"] = "application/json"
            conf["response_json_schema"] = req.schema
        if (budget := self.cfg.get("thinking_budget")) is not None:
            conf["thinking_config"] = types.ThinkingConfig(thinking_budget=int(budget))
        start = time.monotonic()
        try:
            resp = self.client().models.generate_content(
                model=self.model, contents=req.prompt, config=types.GenerateContentConfig(**conf)
            )
        except Exception as e:  # SDK raises several error types; classify by message
            msg = f"{type(e).__name__}: {e}"
            return LLMResult(ok=False, error=msg[:500], error_kind=classify_error(msg))
        meta = getattr(resp, "usage_metadata", None)
        tin = int(getattr(meta, "prompt_token_count", 0) or 0)
        tout = int(getattr(meta, "candidates_token_count", 0) or 0) + int(getattr(meta, "thoughts_token_count", 0) or 0)
        text = resp.text or ""
        usage = {
            "input_tokens": tin,
            "output_tokens": tout,
            "cash_usd": round(estimate_cash(self.cfg.get("price"), tin, tout), 6),
            "latency_s": round(time.monotonic() - start, 2),
        }
        data = extract_json(text) if req.schema else None
        return LLMResult(ok=True, text=text, data=data, usage=usage)
