"""The daily run: feedback → collect → catalog → screen → read → analyze → brief → deliver."""

from __future__ import annotations

import logging
import time

from lark.context import RunContext
from lark.jobs import JobQueue
from lark.records import ReadingNote, Screening, SourceHealth
from lark.report import contract as C
from lark.utils.timez import iso_now

log = logging.getLogger(__name__)


def _stage(name: str):
    def deco(fn):
        def wrapper(*a, **kw):
            t0 = time.monotonic()
            log.info("stage %s: start", name)
            out = fn(*a, **kw)
            log.info("stage %s: done in %.1fs", name, time.monotonic() - t0)
            return out

        return wrapper

    return deco


@_stage("feedback")
def sync_feedback(ctx: RunContext) -> int:
    from lark.feedback.issues import sync

    if ctx.offline:
        return 0
    try:
        return sync(ctx)
    except Exception as e:  # feedback must never block the briefing
        log.warning("feedback sync failed: %s", e)
        return 0


@_stage("collect")
def collect_and_catalog(ctx: RunContext, only: list[str] | None = None, limit: int | None = None):
    from lark.catalog import resolve
    from lark.sources import collect

    results = collect(ctx, only=only, limit=limit)
    records = [r for res in results for r in res.records]
    stats = resolve(ctx, records)
    health = [
        SourceHealth(source=r.source, status=r.status, n_records=len(r.records), truncated=r.truncated, error=r.error)
        for r in results
    ]
    return records, stats, health


@_stage("screen")
def screen_new(ctx: RunContext, work_ids: list[str]):
    from lark import screen

    queue = JobQueue(ctx.store)
    overflow = queue.pending("screen")
    ids = list(dict.fromkeys(work_ids + [j.work_id for j in overflow if j.work_id]))
    stats = screen.run(ctx, ids)
    pv = ctx.config.profiles_version
    for job in overflow:  # overflow jobs whose work has now been screened are done
        if job.work_id and ctx.store.get(Screening, f"{job.work_id}|{pv}"):
            queue.done(job)
    return stats


@_stage("read")
def read_selected(ctx: RunContext):
    from lark.read.reader import run_read_jobs

    return run_read_jobs(ctx, limit=int(ctx.config.get("limits.focused_reads_per_day", 20)))


@_stage("analyze")
def analyze_selected(ctx: RunContext):
    from lark import analyze

    return analyze.run(ctx)


@_stage("deliver")
def deliver(ctx: RunContext, report: C.Report):
    from lark.pipelines.brief import record_briefed
    from lark.report.channels import deliver_all

    results = deliver_all(ctx, report)
    if any(r.status == "sent" for r in results):
        record_briefed(ctx, report)
    return results


def run_daily(
    ctx: RunContext,
    *,
    sources: list[str] | None = None,
    collect_limit: int | None = None,
    skip_read: bool = False,
    skip_deliver: bool = False,
) -> C.Report:
    from lark.pipelines.brief import build_report

    started = iso_now()
    n_feedback = sync_feedback(ctx)
    records, rstats, health = collect_and_catalog(ctx, sources, collect_limit)
    to_screen = list(dict.fromkeys(rstats.new_work_ids + rstats.substantive_update_ids))
    sstats = screen_new(ctx, to_screen)
    n_read = n_analyzed = 0
    if not skip_read:
        read_selected(ctx)
        # Count readings beyond the abstract; abstract-only fallbacks are not "read".
        n_read = sum(1 for n in ctx.store.since(ReadingNote, started) if n.depth != "abstract")
        n_analyzed = len(analyze_selected(ctx) or [])
    stats = C.Stats(
        collected=len(records),
        new_works=len(rstats.new_work_ids),
        candidates=getattr(sstats, "candidates", 0),
        screened=getattr(sstats, "screened", 0),
        read=n_read,
        analyzed=n_analyzed,
    )
    report = build_report(ctx, "daily", health=health, stats=stats)
    if n_feedback:
        report.notes.append(f"Synced {n_feedback} feedback item{'s' if n_feedback != 1 else ''}.")
    if not skip_deliver:
        deliver(ctx, report)
    return report
