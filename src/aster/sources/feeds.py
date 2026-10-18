"""RSS and Atom feeds: lab blogs, journal feeds such as JMLR, and any feed the user adds.

Feeds come from the active fields and from the user's `feeds:` list. A feed's `kind` says what
its entries are: `paper` (a journal's feed) or `release` (posts, announcements). An
entry whose link is itself an arXiv, DOI, SSRN or NBER page takes that identifier, so it merges
with the same work from other sources; arXiv ids merely mentioned in a post stay in
`extra.related_arxiv`.
"""

from __future__ import annotations

from typing import Any

import feedparser

from aster.context import Context
from aster.fields import Feed
from aster.ids import canonical_url, ids_from_url
from aster.records import SourceRecord
from aster.sources._common import fetch_text, iso_date, related_arxiv, strip_markup, window_start
from aster.sources.base import FetchResult
from aster.utils.text import short_hash


def parse_feed(feed: Feed, text: str) -> list[SourceRecord]:
    out = []
    for e in feedparser.parse(text).entries:
        link = e.get("link") or e.get("id")
        title = strip_markup(e.get("title"))
        if not link or not title:
            continue
        url = canonical_url(link)
        summary = strip_markup(e.get("summary"))
        ids = {**ids_from_url(link), "url": url}
        out.append(
            SourceRecord(
                source="feeds",
                source_id=url,
                title=title,
                authors=[a.get("name") for a in e.get("authors") or [] if a.get("name")] or [feed.name],
                abstract=summary,
                ids=ids,
                venue=feed.name,
                status="published" if feed.kind == "paper" else "release",
                kind=feed.kind,
                url=link,
                released_date=iso_date(e.get("published") or e.get("updated")),
                fields=list(feed.fields),
                extra={"feed": feed.name, "related_arxiv": related_arxiv(summary)},
            )
        )
    return out


class FeedsCollector:
    name = "feeds"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        feeds = ctx.config.feeds()
        if not feeds:
            return FetchResult(
                self.name, "empty", checkpoint={**checkpoint, "since": ctx.run_date}, error="no feeds configured"
            )
        cfg = ctx.config.get("sources.feeds", {}) or {}
        start = window_start(
            checkpoint,
            ctx,
            overlap_days=int(cfg.get("overlap_days", 2)),
            first_run_days=int(cfg.get("first_run_days", 14)),
        )
        records: list[SourceRecord] = []
        errors: list[str] = []
        for feed in feeds:
            try:
                text = fetch_text(ctx, self.name, feed.url, fixture=f"feed_{short_hash(feed.url, n=8)}.xml")
                records += parse_feed(feed, text or "")
            except Exception as e:
                errors.append(f"{feed.name}: {e}")
        records = [r for r in records if not r.released_date or r.released_date >= start]
        if errors and len(errors) == len(feeds):
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if errors else ("ok" if records else "empty")
        return FetchResult(
            self.name,
            status,
            records=records,
            checkpoint={**checkpoint, "since": ctx.run_date},
            error="; ".join(errors)[:500] or None,
        )
