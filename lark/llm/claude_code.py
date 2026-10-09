"""Claude through the official Claude Code CLI in non-interactive mode (subscription auth).

Auth: CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or an existing local login.
ANTHROPIC_API_KEY is removed from the child env because it would take precedence over
subscription auth in -p mode; --bare is never used because it skips subscription OAuth.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from lark.llm.base import LLMRequest, LLMResult, extract_json
from lark.llm.errors import classify_error


class ClaudeCodeWorker:
    provider = "anthropic"

    def __init__(self, route: str, cfg: dict):
        self.route = route
        self.cfg = cfg
        self.model = cfg.get("model", "claude-haiku-5-5")
        self.billing = cfg.get("billing", "subscription")
        self.binary = cfg.get("binary", "claude")

    def available(self) -> tuple[bool, str]:
        if not shutil.which(self.binary):
            return False, f"`{self.binary}` not on PATH"
        if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
            return True, "CLAUDE_CODE_OAUTH_TOKEN set"
        home = Path.home()
        if (home / ".claude" / ".credentials.json").exists() or (home / ".claude.json").exists():
            return True, "local Claude login"
        return False, "no CLAUDE_CODE_OAUTH_TOKEN and no local login"

    def child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        return env

    def command(self, req: LLMRequest) -> list[str]:
        cmd = [
            self.binary, "-p",
            "--model", self.model,
            "--output-format", "json",
            "--tools", "",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--system-prompt", req.system,
        ]
        if req.schema:
            cmd += ["--json-schema", json.dumps(req.schema)]
        if effort := self.cfg.get("effort"):
            cmd += ["--effort", str(effort)]
        return cmd

    def run(self, req: LLMRequest) -> LLMResult:
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="lark-claude-") as tmp:
            try:
                proc = subprocess.run(
                    self.command(req), input=req.prompt, capture_output=True, text=True,
                    cwd=tmp, env=self.child_env(), timeout=req.timeout_s,
                )
            except subprocess.TimeoutExpired:
                return LLMResult(ok=False, error=f"timeout after {req.timeout_s}s", error_kind="timeout")
            except OSError as e:
                return LLMResult(ok=False, error=str(e), error_kind="unavailable")
        return self.parse(proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start, bool(req.schema))

    def parse(self, returncode: int, stdout: str, stderr: str, latency: float, want_json: bool) -> LLMResult:
        try:
            payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        text = payload.get("result") if isinstance(payload.get("result"), str) else stdout
        if returncode != 0 or payload.get("is_error") or not payload:
            msg = (payload.get("result") if payload else None) or stderr or stdout or f"exit {returncode}"
            status = payload.get("api_error_status")
            return LLMResult(ok=False, text=text or "", error=str(msg)[:500],
                             error_kind=classify_error(f"{status} {msg}"))
        usage_raw = payload.get("usage") or {}
        usage = {
            "input_tokens": int(usage_raw.get("input_tokens", 0))
            + int(usage_raw.get("cache_creation_input_tokens", 0))
            + int(usage_raw.get("cache_read_input_tokens", 0)),
            "output_tokens": int(usage_raw.get("output_tokens", 0)),
            "list_cost_usd": payload.get("total_cost_usd"),  # informational; subscription-funded
            "latency_s": round(latency, 2),
        }
        data = payload.get("structured_output")
        if not isinstance(data, dict):
            data = extract_json(text) if want_json else None
        return LLMResult(ok=True, text=text, data=data, usage=usage)
