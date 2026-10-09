"""Route application roles (screener, reader, analyst, ...) to concrete model routes.

Config:
    routes.<name>: {worker, model, billing, daily_calls?, price?, ...}
    roles.<role>:  {routes: [name, ...], strategy: fallback | alternate}

`fallback` tries routes in order; `alternate` tries the route with the fewest calls
today first (so reader jobs split between, e.g., Haiku and Luna). Every attempt
reserves budget before dispatch. If nothing can run, NoRouteAvailable is raised and
the caller defers the job.
"""

from __future__ import annotations

import logging
import threading
import time

from lark import budget
from lark.context import RunContext
from lark.llm.base import LLMRequest, LLMResult, Worker
from lark.llm.claude_code import ClaudeCodeWorker
from lark.llm.codex import CodexWorker
from lark.llm.fake import FakeWorker
from lark.llm.gemini_api import GeminiApiWorker, estimate_cash
from lark.llm.openai_compat import OpenAICompatWorker
from lark.llm.schema import validate
from lark.records import LLMCall

log = logging.getLogger(__name__)

WORKER_TYPES = {
    "claude_code": ClaudeCodeWorker,
    "codex": CodexWorker,
    "gemini_api": GeminiApiWorker,
    "openai_compat": OpenAICompatWorker,
    "fake": FakeWorker,
}

_WORKERS: dict[str, Worker] = {}
_OVERRIDES: dict[str, Worker] = {}
_LOCK = threading.Lock()

FAKE_ROUTE = {"worker": "fake", "model": "fake-1", "billing": "none"}


class NoRouteAvailable(RuntimeError):
    """No configured route could run the job now (unavailable, failed, or over budget)."""


def register_worker(route: str, worker: Worker) -> None:
    """Force a specific worker instance for a route (tests, experiments)."""
    _OVERRIDES[route] = worker


def reset_workers() -> None:
    _WORKERS.clear()
    _OVERRIDES.clear()


def route_config(ctx: RunContext, name: str) -> dict:
    cfg = ctx.config.get(f"routes.{name}")
    if cfg is None:
        if name == "fake":
            return dict(FAKE_ROUTE)
        raise KeyError(f"unknown route '{name}'")
    return cfg


def get_worker(name: str, cfg: dict) -> Worker:
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    with _LOCK:
        if name not in _WORKERS:
            kind = cfg.get("worker", "fake")
            if kind not in WORKER_TYPES:
                raise KeyError(f"route '{name}': unknown worker '{kind}'")
            _WORKERS[name] = WORKER_TYPES[kind](name, cfg)
        return _WORKERS[name]


def role_routes(ctx: RunContext, role: str) -> tuple[list[str], str]:
    spec = ctx.config.get(f"roles.{role}")
    if spec is None:
        raise KeyError(f"unknown role '{role}'")
    if isinstance(spec, list):
        return list(spec), "fallback"
    return list(spec.get("routes", [])), spec.get("strategy", "fallback")


def _estimate_cash(cfg: dict, req: LLMRequest) -> float:
    tokens_in = (len(req.system) + len(req.prompt)) // 4 + 50
    return estimate_cash(cfg.get("price"), tokens_in, req.max_output_tokens)


def _repair_request(req: LLMRequest, previous: str, problems: list[str]) -> LLMRequest:
    prompt = (
        f"{req.prompt}\n\n---\nYour previous reply could not be used:\n"
        + "\n".join(f"- {p}" for p in problems[:10])
        + f"\n\nPrevious reply:\n{previous[:4000]}\n\nReturn only a JSON object that satisfies the schema."
    )
    return LLMRequest(role=req.role, system=req.system, prompt=prompt, schema=req.schema,
                      max_output_tokens=req.max_output_tokens, timeout_s=req.timeout_s, job_id=req.job_id,
                      prompt_version=req.prompt_version)


def _attempt(ctx: RunContext, name: str, cfg: dict, worker: Worker, req: LLMRequest) -> LLMResult | None:
    """One budgeted call. Returns None when the budget refuses the reservation."""
    rid = budget.reserve(ctx, name, cfg, _estimate_cash(cfg, req), req.job_id,
                         provider=worker.provider, model=worker.model)
    if rid is None:
        return None
    start = time.monotonic()
    try:
        res = worker.run(req)
    except Exception as e:  # a worker bug must not take down the pipeline
        log.exception("route %s crashed", name)
        res = LLMResult(ok=False, error=f"{type(e).__name__}: {e}", error_kind="other")
    if res.ok:
        budget.settle(ctx, rid, float(res.usage.get("cash_usd", 0.0) or 0.0), res.usage)
    else:
        budget.release(ctx, rid, f"{res.error_kind}: {(res.error or '')[:200]}")
    res.usage.setdefault("latency_s", round(time.monotonic() - start, 2))
    return res


def _run_route(ctx: RunContext, name: str, cfg: dict, worker: Worker, req: LLMRequest) -> LLMResult | None:
    res = _attempt(ctx, name, cfg, worker, req)
    if res is None or not res.ok or not req.schema:
        return res
    problems = ["reply was not a JSON object"] if res.data is None else validate(res.data, req.schema)
    if not problems:
        return res
    log.info("route %s returned invalid output (%s); repairing once", name, problems[:3])
    repaired = _attempt(ctx, name, cfg, worker, _repair_request(req, res.text, problems))
    if repaired is None:
        return LLMResult(ok=False, text=res.text, error="budget refused repair call", error_kind="invalid_output")
    if repaired.ok:
        problems = ["reply was not a JSON object"] if repaired.data is None else validate(repaired.data, req.schema)
        if not problems:
            return repaired
    return LLMResult(ok=False, text=repaired.text or res.text, error="; ".join(problems[:5]) or repaired.error,
                     error_kind="invalid_output")


def _order(ctx: RunContext, names: list[str], strategy: str) -> list[str]:
    if strategy != "alternate":
        return names
    counts = budget.calls_today(ctx)
    return sorted(names, key=lambda n: (counts.get(n, 0), names.index(n)))


def call(ctx: RunContext, role: str, req: LLMRequest, route: str | None = None) -> LLMResult:
    """Run `req` for `role`. `route` forces one route (still budgeted); fake_llm forces the fake route."""
    req.role = req.role or role
    if ctx.fake_llm:
        names, strategy = ["fake"], "fallback"
    elif route:
        names, strategy = [route], "fallback"
    else:
        names, strategy = role_routes(ctx, role)
    problems: list[str] = []
    for name in _order(ctx, names, strategy):
        cfg = route_config(ctx, name)
        worker = get_worker(name, cfg)
        ok, why = worker.available()
        if not ok:
            problems.append(f"{name}: unavailable ({why})")
            continue
        res = _run_route(ctx, name, cfg, worker, req)
        if res is None:
            problems.append(f"{name}: over budget/capacity")
            continue
        if res.ok:
            res.call = LLMCall(role=role, route=name, provider=worker.provider, model=worker.model,
                               billing=cfg.get("billing", "none"), prompt_version=req.prompt_version,
                               usage=res.usage, latency_s=res.usage.get("latency_s"))
            return res
        problems.append(f"{name}: {res.error_kind}: {(res.error or '')[:200]}")
        if res.error_kind in ("auth", "rate_limit"):
            budget.record_event(ctx, name, "auth_failed" if res.error_kind == "auth" else "rate_limited",
                                res.error, provider=worker.provider, billing=cfg.get("billing", "none"),
                                model=worker.model)
        log.warning("role %s: route %s failed (%s); trying next", role, name, res.error_kind)
    raise NoRouteAvailable(f"role '{role}': " + (" | ".join(problems) or "no routes configured"))


def route_status(ctx: RunContext) -> list[dict]:
    """Per-route availability and today's usage, for the report's budget section."""
    counts = budget.calls_today(ctx)
    events = budget.summary(ctx)["events_today"]
    out = []
    for name, cfg in (ctx.config.get("routes") or {}).items():
        if cfg.get("worker") == "fake":
            continue
        ok, why = get_worker(name, cfg).available()
        note = None if ok else why
        if events.get(name):
            note = f"{note + '; ' if note else ''}{', '.join(sorted(set(events[name])))} today"
        out.append({"route": name, "ok": ok, "calls_today": counts.get(name, 0), "note": note})
    return out


def doctor(ctx: RunContext, live: bool = False) -> list[dict]:
    """Check every route; with live=True make one tiny budgeted call each."""
    out = []
    probe_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    for name, cfg in (ctx.config.get("routes") or {}).items():
        worker = get_worker(name, cfg)
        ok, why = worker.available()
        row = {"route": name, "worker": cfg.get("worker"), "model": cfg.get("model"), "available": ok, "detail": why}
        if live and ok and cfg.get("worker") != "fake":
            req = LLMRequest(role="doctor", system="Return JSON only.", prompt='Reply with {"ok": true}',
                             schema=probe_schema, max_output_tokens=50, timeout_s=120)
            res = _attempt(ctx, name, cfg, worker, req)
            if res is None:
                row.update(live_ok=False, live_detail="over budget/capacity")
            else:
                row.update(live_ok=bool(res.ok and res.data and res.data.get("ok") is True),
                           live_detail=res.error or "ok")
        out.append(row)
    return out
