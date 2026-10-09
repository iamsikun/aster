"""Audit samples: re-screen a share of exclusions with a second model to estimate what screening misses.

Two kinds of exclusion are sampled:
- screened items judged skip / priority none
- items retrieval never passed to the screener (no primary Screening at all)

Audit jobs carry tag "gpu" so the local model on the workstation drains them when it's
on. Audit results are stored with profiles_version "<pv>:audit" so they never overwrite
the primary screening.
"""

from __future__ import annotations

import logging
import math
import random

from lark.context import RunContext
from lark.jobs import JobQueue
from lark.llm.roles import NoRouteAvailable
from lark.records import Job, Screening, Work
from lark.screen.screener import screen_one

log = logging.getLogger(__name__)

AUDIT_SUFFIX = ":audit"
READ_ACTIONS = ("focused_read", "full_read")


def _sample(ids: list[str], fraction: float, seed: str, cap: int | None = None) -> list[str]:
    if not ids or fraction <= 0:
        return []
    n = min(len(ids), max(1, math.ceil(len(ids) * fraction)))
    if cap is not None:
        n = min(n, cap)
    return sorted(random.Random(seed).sample(sorted(ids), n))


def enqueue(ctx: RunContext, screenings: list[Screening], excluded_work_ids: list[str] | None = None) -> int:
    """Sample today's exclusions into `audit` jobs. Returns the number enqueued."""
    fraction = float(ctx.config.get("limits.audit_fraction", 0.05))
    q = JobQueue(ctx.store)
    skipped = [s.work_id for s in screenings if not s.audit and (s.next_action == "skip" or s.priority == "none")]
    picks = [(wid, "screened_skip") for wid in _sample(skipped, fraction, f"audit-s-{ctx.run_date}")]
    cap = int(ctx.config.get("limits.audit_unretrieved_cap", 50))
    picks += [(wid, "not_retrieved") for wid in
              _sample(list(excluded_work_ids or []), fraction, f"audit-r-{ctx.run_date}", cap)]
    for wid, origin in picks:
        q.enqueue("audit", wid, priority=20, tag="gpu", payload={"origin": origin})
    return len(picks)


def run_audit_jobs(ctx: RunContext, role: str = "local_screener", limit: int | None = None) -> dict:
    """Drain audit jobs by re-screening with `role`. Disagreements that warrant reading become read jobs."""
    q = JobQueue(ctx.store)
    pv = ctx.config.profiles_version
    stats = {"done": 0, "deferred": 0, "disagreements": 0}
    for job in q.pending(kind="audit", limit=limit):
        work = ctx.store.get(Work, job.work_id or "")
        if work is None:
            q.fail(q.start(job), "work not found")
            continue
        job = q.start(job)
        try:
            s = screen_one(ctx, work, None, role=role, profiles_version=pv + AUDIT_SUFFIX, audit=True, job_id=job.job_id)
        except NoRouteAvailable as e:
            job.attempts -= 1  # deferral is not a failed attempt
            q.defer(job, str(e)[:300])
            stats["deferred"] += 1
            # Same role for every remaining job; stop instead of deferring them one by one.
            break
        ctx.store.append(s)
        q.done(job)
        stats["done"] += 1
        primary = ctx.store.get(Screening, f"{work.work_id}|{pv}")
        primary_reads = primary is not None and primary.next_action in READ_ACTIONS
        if s.next_action in READ_ACTIONS and not primary_reads:
            stats["disagreements"] += 1
            q.enqueue("read", work.work_id, priority=55,
                      payload={"depth": "full" if s.next_action == "full_read" else "focused",
                               "profiles": s.topics, "origin": "audit"})
    return stats


def audit_disagreements(ctx: RunContext) -> list[dict]:
    """Audit screenings vs. primary screenings for the current profiles (for eval)."""
    pv = ctx.config.profiles_version
    out = []
    for a in ctx.store.all(Screening, "json_extract(json, '$.profiles_version') = ?", (pv + AUDIT_SUFFIX,)):
        p = ctx.store.get(Screening, f"{a.work_id}|{pv}")
        p_action = p.next_action if p else "not_retrieved"
        p_priority = p.priority if p else "none"
        if (a.next_action in READ_ACTIONS) != (p_action in READ_ACTIONS) or a.priority != p_priority:
            out.append({
                "work_id": a.work_id,
                "primary_action": p_action, "audit_action": a.next_action,
                "primary_priority": p_priority, "audit_priority": a.priority,
                "audit_reason": a.reason,
                "audit_route": a.llm.route if a.llm else None,
            })
    return out


def pending_audits(ctx: RunContext) -> list[Job]:
    return JobQueue(ctx.store).pending(kind="audit")
