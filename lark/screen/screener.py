"""Abstract screening: a small, constrained relevance judgment per candidate."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from lark import budget
from lark.context import RunContext
from lark.jobs import JobQueue, job_id_for
from lark.llm.base import LLMRequest
from lark.llm.roles import NoRouteAvailable, call
from lark.records import Job, Screening, Work
from lark.screen.retrieve import Candidate

log = logging.getLogger(__name__)

PROMPT_VERSION = "screen-v1"

SYSTEM_PROMPT = """You triage newly released research for one quantitatively trained researcher \
(economics, statistics, machine learning, operations, finance). You see one item: its title \
and abstract, plus the researcher's research profiles.

Decide two separate things:
1. RELEVANCE - does this deserve the researcher's attention given the profiles?
   - high: directly addresses a profile question, or offers a method/result they could use now.
   - medium: clearly related; worth a focused read if capacity allows.
   - low: adjacent; at most a one-line mention.
   - none: not relevant, including work that only shares vocabulary with a profile
     (see each profile's "Not relevant" examples).
   Recognize relevance by substance, not wording: e.g. a paper on bootstrap inference for
   the maximum of estimated means matters to a winner's-curse profile even if it never
   says "winner's curse".
2. NEXT ACTION - what reading is needed to explain it faithfully:
   - focused_read: relevant; the key claims can be checked in a few sections.
   - full_read: relevant and the argument spans the paper (a theory paper, a new framework).
   - abstract_only: worth noting, and the abstract suffices.
   - skip: not worth attention.
   - insufficient_info: the abstract is missing or too vague to judge. Use this instead of
     skip whenever you cannot tell; never call an item irrelevant just because the abstract is missing.
   Relevance and reading difficulty are independent: a highly relevant empirical paper may
   need only a focused read; a peripheral theory paper is still low priority.

Only describe what the authors propose; do not assess whether it is correct.
Return JSON only, matching the schema. `topics` lists the ids of matching profiles (may be
empty). `reason` is one sentence. `information_missing` lists what you would need to decide
better (empty if nothing)."""

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
        "priority": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "next_action": {
            "type": "string",
            "enum": ["focused_read", "full_read", "abstract_only", "skip", "insufficient_info"],
        },
        "reason": {"type": "string"},
        "information_missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["topics", "priority", "next_action", "reason", "information_missing"],
}


@dataclass
class ScreenOutcome:
    screenings: list[Screening]
    deferred: int = 0
    skipped_existing: int = 0


def build_prompt(ctx: RunContext, work: Work, cand: Candidate | None) -> str:
    profiles = ctx.config.profiles
    matched = [p for p in profiles if cand and p.id in cand.profiles]
    others = [p for p in profiles if p not in matched]
    lines = ["RESEARCH PROFILES (most likely matches first):"]
    for p in matched or profiles:
        lines.append(p.brief())
        lines.append("")
    if matched and others:
        lines.append("Other profiles: " + "; ".join(f"[{p.id}] {p.name}" for p in others))
        lines.append("")
    lines += [
        "ITEM:",
        f"Title: {work.title}",
        f"Authors: {', '.join(work.authors[:12])}{' et al.' if len(work.authors) > 12 else ''}",
        f"Venue/status: {work.venue or 'unknown'} / {work.status}",
        f"Kind: {work.kind}",
        f"Abstract: {(work.abstract or '').strip() or '[MISSING]'}",
    ]
    if cand and cand.routes:
        lines.append(f"(Retrieved via: {', '.join(sorted(cand.routes))})")
    return "\n".join(lines)


def normalize(ctx: RunContext, data: dict, work: Work) -> dict:
    known = {p.id for p in ctx.config.profiles}
    out = dict(data)
    out["topics"] = [t for t in data.get("topics", []) if t in known]
    no_abstract = not (work.abstract or "").strip()
    if no_abstract and out.get("next_action") in ("skip", "abstract_only"):
        # Missing information is not evidence of irrelevance.
        out["next_action"] = "insufficient_info"
        out.setdefault("information_missing", [])
        if "abstract" not in out["information_missing"]:
            out["information_missing"] = [*out["information_missing"], "abstract"]
    if out.get("next_action") == "skip":
        out["priority"] = "none" if out.get("priority") in (None, "") else out["priority"]
    return out


def screen_one(ctx: RunContext, work: Work, cand: Candidate | None, *, role: str = "screener",
               profiles_version: str | None = None, audit: bool = False, job_id: str | None = None) -> Screening:
    req = LLMRequest(role=role, system=SYSTEM_PROMPT, prompt=build_prompt(ctx, work, cand), schema=SCHEMA,
                     max_output_tokens=int(ctx.config.get("screen.max_output_tokens", 400)), timeout_s=120,
                     job_id=job_id, prompt_version=PROMPT_VERSION)
    res = call(ctx, role, req)  # raises NoRouteAvailable
    data = normalize(ctx, res.data or {}, work)
    return Screening(
        work_id=work.work_id,
        profiles_version=profiles_version or ctx.config.profiles_version,
        topics=data.get("topics", []),
        priority=data.get("priority", "none"),
        next_action=data.get("next_action", "skip"),
        reason=str(data.get("reason", ""))[:500],
        information_missing=list(data.get("information_missing", []))[:10],
        retrieval_routes=sorted(cand.routes) if cand else [],
        retrieval_score=float(cand.score) if cand else 0.0,
        audit=audit,
        llm=res.call,
    )


def screened_today(ctx: RunContext) -> int:
    today_ts, _ = budget.period_starts()
    return ctx.store.count(Screening, "ts >= ? AND json_extract(json, '$.audit') = 0", (today_ts,))


def _defer(q: JobQueue, cand: Candidate, reason: str) -> None:
    job = q.enqueue("screen", cand.work_id, priority=int(20 + 60 * min(cand.score, 1.0)),
                    payload={"score": cand.score, "routes": sorted(cand.routes), "profiles": sorted(cand.profiles),
                             "no_abstract": cand.no_abstract})
    if job.state != "deferred":
        q.defer(job, reason)


def screen(ctx: RunContext, candidates: Iterable[Candidate], role: str = "screener",
           outcome: ScreenOutcome | None = None) -> list[Screening]:
    """Screen candidates up to the daily cap; the overflow becomes deferred `screen` jobs (never dropped)."""
    outcome = outcome or ScreenOutcome(screenings=[])
    pv = ctx.config.profiles_version
    q = JobQueue(ctx.store)
    todo: list[tuple[Candidate, Work]] = []
    for c in candidates:
        if ctx.store.get(Screening, f"{c.work_id}|{pv}") is not None:
            outcome.skipped_existing += 1
            continue
        w = ctx.store.get(Work, c.work_id)
        if w is not None:
            todo.append((c, w))

    remaining = max(int(ctx.config.get("limits.screen_per_day", 1000)) - screened_today(ctx), 0)
    now, overflow = todo[:remaining], todo[remaining:]
    for c, _ in overflow:
        _defer(q, c, "daily screening cap reached")
    outcome.deferred += len(overflow)

    stop = False

    def work_fn(item: tuple[Candidate, Work]) -> Screening | None:
        nonlocal stop
        c, w = item
        if stop:
            _defer(q, c, "no screening route available")
            return None
        job = ctx.store.get(Job, job_id_for("screen", c.work_id))
        try:
            s = screen_one(ctx, w, c, role=role, job_id=job.job_id if job else None)
        except NoRouteAvailable as e:
            stop = True
            log.warning("screening halted: %s", e)
            _defer(q, c, str(e)[:300])
            return None
        ctx.store.append(s)
        if job and job.state in ("queued", "deferred", "running"):
            q.done(job)
        return s

    workers = max(int(ctx.config.get("screen.concurrency", 4)), 1)
    if ctx.fake_llm:
        workers = 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work_fn, now))
    done = [s for s in results if s is not None]
    outcome.deferred += sum(1 for s in results if s is None)
    outcome.screenings.extend(done)
    return done

