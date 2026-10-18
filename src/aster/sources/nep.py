"""RePEc NEP subject reports (edited weekly-ish lists), e.g. https://nep.repec.org/rss/nep-ecm.rss.xml.

Items link to RePEc handles; arXiv- and NBER-backed handles map to native ids so they merge
with records from those collectors.
"""

from __future__ import annotations

from typing import Any

import feedparser

from aster.context import Context
from aster.fields import nep_index
from aster.ids import ids_from_repec, repec_handle_from_url
from aster.records import SourceRecord
from aster.sources._common import fetch_text, iso_date, split_authors, strip_markup
from aster.sources.base import FetchResult

FEED = "https://nep.repec.org/rss/{report}.rss.xml"


def parse_feed(text: str, report: str) -> list[SourceRecord]:
    out = []
    for e in feedparser.parse(text).entries:
        link = e.get("link") or e.get("id") or ""
        handle = repec_handle_from_url(link)
        if not handle:
            continue
        ids = ids_from_repec(handle)
        authors: list[str] = []
        for a in e.get("authors") or []:
            authors += split_authors(a.get("name"))
        out.append(
            SourceRecord(
                source="nep",
                source_id=handle,
                title=strip_markup(e.get("title")) or "",
                authors=authors,
                abstract=strip_markup(e.get("summary")),
                ids=ids,
                status="preprint",
                url=link,
                released_date=iso_date(e.get("updated") or e.get("published")),
                categories=[report],
                extra={"nep_report": report},
            )
        )
    return [r for r in out if r.title]


class NepCollector:
    name = "nep"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        index = nep_index(ctx.config.active_fields())
        reports = list(index)
        if not reports:
            return FetchResult(self.name, "empty", checkpoint=checkpoint, error="no NEP reports in active fields")
        seen: dict[str, str] = dict(checkpoint.get("seen", {}))  # report -> issue date (dc:date)
        records: list[SourceRecord] = []
        errors: list[str] = []
        for rep in reports:
            try:
                text = fetch_text(ctx, self.name, FEED.format(report=rep), fixture=f"{rep}.rss.xml")
            except Exception as e:
                errors.append(f"{rep}: {e}")
                continue
            if not text:
                continue
            issue = feedparser.parse(text).feed.get("updated") or ""
            if issue and seen.get(rep) == issue:
                continue  # this report issue was already ingested
            for r in parse_feed(text, rep):
                r.fields = list(index[rep])
                records.append(r)
            if issue:
                seen[rep] = issue
        if errors and not records and len(errors) == len(reports):
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        # The same paper may appear in several reports: keep one record, union the report names.
        by_id: dict[str, SourceRecord] = {}
        for r in records:
            if r.source_id in by_id:
                kept = by_id[r.source_id]
                kept.categories = sorted(set(kept.categories) | set(r.categories))
                kept.fields = list(dict.fromkeys([*kept.fields, *r.fields]))
            else:
                by_id[r.source_id] = r
        status = "partial" if errors else ("ok" if by_id else "empty")
        return FetchResult(
            self.name,
            status,
            records=list(by_id.values()),
            checkpoint={**checkpoint, "seen": seen, "since": ctx.run_date},
            error="; ".join(errors)[:500] or None,
        )
