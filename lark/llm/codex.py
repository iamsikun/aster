"""OpenAI models through the Codex CLI (`codex exec`) signed in with ChatGPT.

Auth lives in $CODEX_HOME/auth.json (default ~/.codex), created by `codex login`.
The user's config.toml is ignored (--ignore-user-config) so personal defaults such as
reasoning effort, MCP servers, or plugins don't leak into pipeline jobs.
Model availability depends on the account: with a ChatGPT login some API models
(e.g. gpt-6-luna) are rejected, which surfaces here as error_kind="unavailable".
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
from lark.llm.schema import strictify


def codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", Path.home() / ".codex"))


def account_models() -> list[str] | None:
    """Model slugs Codex last fetched for this login, or None if unknown."""
    path = codex_home() / "models_cache.json"
    try:
        data = json.loads(path.read_text())
        return [m["slug"] for m in data.get("models", []) if m.get("slug")]
    except (OSError, ValueError, KeyError, TypeError):
        return None


class CodexWorker:
    provider = "openai"

    def __init__(self, route: str, cfg: dict):
        self.route = route
        self.cfg = cfg
        self.model = cfg.get("model", "gpt-5.6-luna")
        self.billing = cfg.get("billing", "subscription")
        self.binary = cfg.get("binary", "codex")
        self.effort = cfg.get("effort", "low")

    def available(self) -> tuple[bool, str]:
        if not shutil.which(self.binary):
            return False, f"`{self.binary}` not on PATH"
        if not (codex_home() / "auth.json").exists():
            return False, f"no {codex_home() / 'auth.json'} (run `codex login`)"
        models = account_models()
        if models and self.model not in models:
            return False, f"{self.model} not offered to this login (offered: {', '.join(models)})"
        return True, "codex auth.json present"

    def command(self, req: LLMRequest, workdir: str, last_path: str, schema_path: str | None) -> list[str]:
        cmd = [
            self.binary, "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "-s", "read-only",
            "-C", workdir,
            "-m", self.model,
            "-c", 'approval_policy="never"',
            "-c", f'model_reasoning_effort="{self.effort}"',
            "--json",
            "-o", last_path,
        ]
        if schema_path:
            cmd += ["--output-schema", schema_path]
        cmd.append("-")  # prompt from stdin
        return cmd

    @staticmethod
    def full_prompt(req: LLMRequest) -> str:
        # codex exec has no separate system-prompt flag.
        return f"<instructions>\n{req.system}\n</instructions>\n\n{req.prompt}"

    def run(self, req: LLMRequest) -> LLMResult:
        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="lark-codex-") as tmp:
            last_path = str(Path(tmp) / "last.txt")
            schema_path = None
            if req.schema:
                schema_path = str(Path(tmp) / "schema.json")
                Path(schema_path).write_text(json.dumps(strictify(req.schema)))
            try:
                proc = subprocess.run(
                    self.command(req, tmp, last_path, schema_path), input=self.full_prompt(req),
                    capture_output=True, text=True, timeout=req.timeout_s,
                )
            except subprocess.TimeoutExpired:
                return LLMResult(ok=False, error=f"timeout after {req.timeout_s}s", error_kind="timeout")
            except OSError as e:
                return LLMResult(ok=False, error=str(e), error_kind="unavailable")
            last = Path(last_path).read_text() if Path(last_path).exists() else ""
        return self.parse(proc.returncode, proc.stdout, proc.stderr, last, time.monotonic() - start, bool(req.schema))

    def parse(self, returncode: int, events: str, stderr: str, last: str, latency: float, want_json: bool) -> LLMResult:
        errors: list[str] = []
        usage: dict = {}
        for line in events.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "error":
                errors.append(str(ev.get("message", "")))
            elif etype == "turn.failed":
                errors.append(str((ev.get("error") or {}).get("message", "")))
            elif etype == "turn.completed":
                usage = ev.get("usage") or {}
        if returncode != 0 or (errors and not last.strip()):
            msg = " | ".join(e for e in errors if e) or stderr.strip() or f"exit {returncode}"
            return LLMResult(ok=False, text=last, error=msg[:500], error_kind=classify_error(msg))
        out_usage = {
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
            "cached_input_tokens": int(usage.get("cached_input_tokens", 0)),
            "latency_s": round(latency, 2),
        }
        data = extract_json(last) if want_json else None
        return LLMResult(ok=True, text=last, data=data, usage=out_usage)
