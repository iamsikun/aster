"""On-request deep reading of one paper, delivered as a single-item report."""

from __future__ import annotations

from lark.context import RunContext
from lark.pipelines.brief import Selected, build_report
from lark.records import Analysis, Work
from lark.report import contract as C


def run_deep(ctx: RunContext, target: str, *, depth: str = "full", skip_deliver: bool = False) -> C.Report:
    from lark import analyze
    from lark.read.deep import deep_read

    note = deep_read(ctx, target, depth=depth)
    if note is None:
        raise RuntimeError(f"Could not read {target!r}")
    analyze.run(ctx, work_ids=[note.work_id])
    work = ctx.store.get(Work, note.work_id)
    analyses = ctx.store.all(Analysis, "json_extract(json, '$.work_id') = ?", (note.work_id,))
    report = build_report(
        ctx,
        "deep",
        picked=[Selected(work=work, screening=None, note=note, analyses=analyses)],
        report_id=f"deep-{ctx.run_date}-{note.work_id}",
        title=f"Lark deep read — {work.title}",
    )
    if not skip_deliver:
        from lark.pipelines.daily import deliver

        deliver(ctx, report)
    return report
