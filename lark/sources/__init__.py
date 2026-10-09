"""Collectors and the `collect` driver.

`collect` runs each enabled collector in isolation: one failing source becomes a `failed`
SourceHealth entry and never stops the others. Checkpoints are saved only on non-failed runs,
so a failed day is retried from the previous checkpoint (collectors also use overlap windows).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from lark.context import RunContext
from lark.records import Checkpoint, SourceHealth, SourceRecord
from lark.sources.arxiv import ArxivCollector
from lark.sources.base import Collector, FetchResult
from lark.sources.crossref import CrossrefCollector
from lark.sources.nber import NberCollector
from lark.sources.nep import NepCollector
from lark.sources.openalex import OpenAlexCollector
from lark.sources.openreview import OpenReviewCollector
from lark.sources.releases import ReleasesCollector
from lark.sources.ssrn_alerts import SsrnAlertsCollector
from lark.utils.timez import iso_now

log = logging.getLogger(__name__)

COLLECTORS: dict[str, Callable[[], Collector]] = {
    "arxiv": ArxivCollector,
    "crossref": CrossrefCollector,
    "openalex": OpenAlexCollector,
    "nber": NberCollector,
    "nep": NepCollector,
    "openreview": OpenReviewCollector,
    "ssrn_alerts": SsrnAlertsCollector,
    "releases": ReleasesCollector,
}


def _newest(records: list[SourceRecord]) -> str | None:
    dates = [r.released_date or r.published_date for r in records if r.released_date or r.published_date]
    return max(dates) if dates else None


def _unchanged(ctx: RunContext, rec: SourceRecord) -> bool:
    """True if an identical record (ignoring fetch time and catalog link) is already stored."""
    old = ctx.store.get(SourceRecord, rec.key() or "")
    if old is None:
        return False
    skip = {"fetched_at", "work_id"}
    return old.model_dump(exclude=skip) == rec.model_dump(exclude=skip)


def collect(ctx: RunContext, only: list[str] | None = None, limit: int | None = None) -> list[FetchResult]:
    """Run enabled collectors; persist records, checkpoints, and one SourceHealth per source."""
    results: list[FetchResult] = []
    for name, factory in COLLECTORS.items():
        if only and name not in only:
            continue
        if not only and not ctx.config.get(f"sources.{name}.enabled", False):
            continue
        cp_rec = ctx.store.get(Checkpoint, name)
        checkpoint = dict(cp_rec.value) if cp_rec else {}
        try:
            res = factory().fetch(ctx, checkpoint)
        except Exception as e:
            log.exception("collector %s failed", name)
            res = FetchResult(source=name, status="failed", checkpoint=checkpoint, error=f"{type(e).__name__}: {e}")
        if limit is not None and len(res.records) > limit:
            res.records = res.records[:limit]
            res.truncated = True
            if res.status == "ok":
                res.status = "partial"
        ctx.store.append_many([r for r in res.records if not _unchanged(ctx, r)])
        if res.status != "failed":
            ctx.store.append(Checkpoint(source=name, value=res.checkpoint, last_success=iso_now()))
        ctx.store.append(
            SourceHealth(
                source=name,
                status=res.status,
                n_records=len(res.records),
                truncated=res.truncated,
                error=res.error,
                newest_item_date=_newest(res.records),
            )
        )
        log.info("source %s: %s (%d records)%s", name, res.status, len(res.records),
                 f" — {res.error}" if res.error else "")
        results.append(res)
    return results


__all__ = ["COLLECTORS", "collect", "FetchResult"]
