"""Screening stage: retrieval union -> LLM screening -> read jobs + audit samples."""

from __future__ import annotations

from dataclasses import dataclass

from lark.context import RunContext
from lark.jobs import JobQueue
from lark.records import Screening, Work
from lark.screen import audit
from lark.screen.retrieve import Candidate, candidates
from lark.screen.screener import ScreenOutcome, screen

BASE_PRIORITY = {"high": 90, "medium": 60, "low": 30, "none": 10}


@dataclass
class ScreenStats:
    candidates: int = 0
    screened: int = 0
    deferred: int = 0
    to_read: int = 0
    insufficient: int = 0
    audits: int = 0


def read_priority(ctx: RunContext, s: Screening) -> int:
    """Screening priority scaled by the attention weight of the best-matching profile's bucket."""
    attention = ctx.config.get("attention", {}) or {}
    top = max(attention.values()) if attention else 1.0
    buckets = {p.id: p.bucket for p in ctx.config.profiles}
    weights = [attention.get(buckets[t], top) for t in s.topics if t in buckets] or [attention.get("exploration", top)]
    w = max(weights) / top if top else 1.0
    bonus = 5 if s.next_action == "full_read" else 0
    return int(round(BASE_PRIORITY.get(s.priority, 10) * w)) + bonus


def enqueue_reads(ctx: RunContext, screenings: list[Screening]) -> int:
    q = JobQueue(ctx.store)
    n = 0
    for s in screenings:
        if s.next_action in ("focused_read", "full_read"):
            q.enqueue("read", s.work_id, priority=read_priority(ctx, s),
                      payload={"depth": "full" if s.next_action == "full_read" else "focused", "profiles": s.topics})
            n += 1
    return n


def _backlog_candidates(ctx: RunContext, exclude: set[str]) -> list[Candidate]:
    out = []
    for job in JobQueue(ctx.store).pending(kind="screen"):
        if job.work_id and job.work_id not in exclude:
            p = job.payload
            out.append(Candidate(work_id=job.work_id, score=float(p.get("score", 0.0)), routes=set(p.get("routes", [])),
                                 profiles=set(p.get("profiles", [])), no_abstract=bool(p.get("no_abstract"))))
    return out


def run(ctx: RunContext, work_ids: list[str]) -> ScreenStats:
    works = [w for wid in work_ids if (w := ctx.store.get(Work, wid)) is not None]
    cands = candidates(ctx, works)
    chosen = {c.work_id for c in cands}
    # Earlier overflow competes with today's candidates on score.
    pool = sorted(cands + _backlog_candidates(ctx, chosen), key=lambda c: (-c.score, c.work_id))
    outcome = ScreenOutcome(screenings=[])
    done = screen(ctx, pool, outcome=outcome)
    stats = ScreenStats(candidates=len(cands), screened=len(done), deferred=outcome.deferred)
    stats.insufficient = sum(1 for s in done if s.next_action == "insufficient_info")
    stats.to_read = enqueue_reads(ctx, done)
    excluded = [w.work_id for w in works if w.work_id not in chosen]
    stats.audits = audit.enqueue(ctx, done, excluded)
    return stats


__all__ = ["Candidate", "ScreenStats", "audit", "candidates", "enqueue_reads", "read_priority", "run", "screen"]
