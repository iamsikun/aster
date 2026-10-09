"""Backlog draining on the optional GPU workstation runner (tag "gpu").

The daily briefing never waits for this. When the workstation is on, it re-screens
audit samples with the local model, works through deferred reads with the local
reader, and backfills embeddings.
"""

from __future__ import annotations

import logging
import time

from lark.context import RunContext
from lark.jobs import JobQueue

log = logging.getLogger(__name__)


def run_drain(ctx: RunContext, tag: str = "gpu", limit: int = 200, max_minutes: float = 100) -> dict:
    deadline = time.monotonic() + max_minutes * 60
    from lark.llm import roles
    from lark.screen.audit import run_audit_jobs

    out: dict = {"tag": tag}
    ok = {r["route"]: r["ok"] for r in roles.route_status(ctx)}
    local_routes = ctx.config.get("roles.local_screener.routes", [])
    if not ctx.fake_llm and not any(ok.get(r) for r in local_routes):
        out["skipped"] = "local model not reachable"
        log.warning("drain: %s", out["skipped"])
        return out
    out["audit"] = run_audit_jobs(ctx, role="local_screener", limit=limit)

    queue = JobQueue(ctx.store)
    deferred_reads = [j for j in queue.pending("read") if j.state == "deferred"]
    if deferred_reads and ctx.config.get("drain.local_reads", True):
        from lark.read.reader import read_work

        done = 0
        for job in deferred_reads[: max(1, limit // 10)]:
            if time.monotonic() > deadline:
                out["stopped"] = "time limit"
                break
            queue.start(job)
            try:
                note = read_work(ctx, job.work_id, depth=job.payload.get("depth", "focused"), role="local_reader")
                queue.done(job) if note else queue.fail(job, "no note")
                done += bool(note)
            except Exception as e:
                queue.fail(job, str(e))
        out["local_reads"] = done
    return out
