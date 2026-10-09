"""NBER working papers from the "new this week" RSS feed (https://www.nber.org/rss/new.xml).

NBER announces weekly, and the feed carries no dates, so freshness is tracked through the
working-paper numbers seen. No new numbers within `stale_after_days` means `stale`, not `failed`.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import feedparser

from lark.context import RunContext
from lark.records import SourceRecord
from lark.sources._common import fetch_text, run_date, split_authors, strip_markup
from lark.sources.base import FetchResult

FEED = "https://www.nber.org/rss/new.xml"


def parse_feed(text: str) -> list[SourceRecord]:
    out = []
    for e in feedparser.parse(text).entries:
        link = (e.get("link") or "").split("#")[0]
        m = re.search(r"/papers/(w\d+)", link)
        if not m:
            continue
        wp = m.group(1)
        raw_title = e.get("title") or ""
        title, _, by = raw_title.partition(" -- by ")
        out.append(
            SourceRecord(
                source="nber",
                source_id=wp,
                title=re.sub(r"\s+", " ", title).strip(),
                authors=split_authors(by),
                abstract=strip_markup(e.get("summary")),
                ids={"nber": wp, "doi": f"10.3386/{wp}"},
                venue="NBER Working Paper",
                status="preprint",
                url=f"https://www.nber.org/papers/{wp}",
                pdf_url=f"https://www.nber.org/system/files/working_papers/{wp}/{wp}.pdf",
            )
        )
    return out


class NberCollector:
    name = "nber"

    def fetch(self, ctx: RunContext, checkpoint: dict[str, Any]) -> FetchResult:
        cfg = ctx.config.get("sources.nber", {}) or {}
        text = fetch_text(ctx, self.name, FEED, fixture="new.xml")
        if not text:
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="empty response")
        recs = parse_feed(text)
        max_seen = int(checkpoint.get("max_wp", 0))
        new = [r for r in recs if int(r.source_id[1:]) > max_seen]
        cp = dict(checkpoint)
        if new:
            cp["max_wp"] = max(int(r.source_id[1:]) for r in recs)
            cp["last_new"] = ctx.run_date
            for r in new:
                r.released_date = ctx.run_date  # first seen in the weekly announcement
            return FetchResult(self.name, "ok", records=new, checkpoint=cp)
        last_new = checkpoint.get("last_new")
        stale_after = int(cfg.get("stale_after_days", 9))
        if last_new and (run_date(ctx) - date.fromisoformat(last_new)).days > stale_after:
            return FetchResult(self.name, "stale", checkpoint=cp,
                               error=f"no new NBER papers since {last_new}")
        return FetchResult(self.name, "empty", checkpoint=cp)
