"""Crossref: newly registered articles for the journals in <data-dir>/journals.yaml.

Uses `from-created-date` (DOI registration) rather than `from-index-date`: the index date
moves whenever Crossref re-indexes metadata, which returns decades-old articles.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from lark.context import RunContext
from lark.records import SourceRecord
from lark.sources._common import date_parts, fetch_json, ids_from_doi, since_date, strip_markup
from lark.sources.base import FetchResult

log = logging.getLogger(__name__)

API = "https://api.crossref.org/works"
SELECT = "DOI,title,author,abstract,container-title,issued,published-online,published-print,created,URL,type,link,ISSN"
SKIP_TITLES = re.compile(
    r"^(front matter|back matter|issue information|table of contents|cover|masthead|editorial board|index|frontmatter|backmatter)\b",
    re.I,
)


def load_journals(ctx: RunContext) -> list[dict]:
    """Entries may give `issn`, or `issn_print` / `issn_electronic` / `issn_other` (all are queried)."""
    raw = ctx.config.data_file("journals.yaml")
    items = raw.get("journals", []) if isinstance(raw, dict) else (raw or [])
    out = []
    for j in items:
        if not isinstance(j, dict):
            continue
        issns = [str(j[k]).strip() for k in ("issn", "issn_print", "issn_electronic", "issn_other") if j.get(k)]
        issns = list(dict.fromkeys(issns))
        if issns:
            out.append({**j, "issn": issns[0], "issns": issns})
    return out


def parse_item(item: dict, journal: dict | None = None) -> SourceRecord | None:
    doi = item.get("DOI")
    titles = item.get("title") or []
    title = strip_markup(titles[0]) if titles else None
    if not doi or not title or SKIP_TITLES.match(title):
        return None
    authors = []
    for a in item.get("author") or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name")
        if name:
            authors.append(name)
    pdf = next(
        (lk.get("URL") for lk in item.get("link") or [] if lk.get("content-type") == "application/pdf"),
        None,
    )
    online = date_parts(item.get("published-online"))
    printed = date_parts(item.get("published-print"))
    created = date_parts(item.get("created"))
    published = online if online and len(online) == 10 else (printed or date_parts(item.get("issued")))
    venue = (item.get("container-title") or [None])[0] or (journal or {}).get("name")
    ids = ids_from_doi(doi)
    return SourceRecord(
        source="crossref",
        source_id=doi.lower(),
        title=title,
        authors=authors,
        abstract=strip_markup(item.get("abstract")),
        ids=ids,
        venue=venue,
        status="published",
        url=item.get("URL"),
        pdf_url=pdf,
        released_date=online if online and len(online) == 10 else created,
        published_date=published,
        extra={k: v for k, v in {
            "journal": (journal or {}).get("name"),
            "field": (journal or {}).get("field"),
            "tier": (journal or {}).get("tier"),
            "issn": (journal or {}).get("issn"),
            "type": item.get("type"),
        }.items() if v},
    )


class CrossrefCollector:
    name = "crossref"

    def fetch(self, ctx: RunContext, checkpoint: dict[str, Any]) -> FetchResult:
        cfg = ctx.config.get("sources.crossref", {}) or {}
        journals = load_journals(ctx)
        if not journals:
            return FetchResult(self.name, "empty", checkpoint={**checkpoint, "since": ctx.run_date},
                               error="no journals in journals.yaml")
        frm = since_date(checkpoint, ctx, overlap_days=int(cfg.get("overlap_days", 3))).isoformat()
        rows = int(cfg.get("rows", 100))
        max_pages = int(cfg.get("max_pages", 5))
        mailto = os.getenv("LARK_CONTACT_EMAIL")
        records: list[SourceRecord] = []
        truncated = False
        errors: list[str] = []
        for j in journals:
            issn = j["issn"]
            issn_filter = ",".join(f"issn:{i}" for i in j["issns"])  # same-name filters are OR-ed
            cursor = "*"
            for page in range(max_pages):
                params = {"filter": f"{issn_filter},from-created-date:{frm}", "rows": rows, "cursor": cursor,
                          "select": SELECT}
                if mailto:
                    params["mailto"] = mailto
                try:
                    data = fetch_json(ctx, self.name, API, fixture=f"{issn}.json", params=params)
                except Exception as e:  # one journal failing should not lose the rest
                    errors.append(f"{issn}: {e}")
                    break
                msg = (data or {}).get("message") or {}
                items = msg.get("items") or []
                records += [r for r in (parse_item(it, j) for it in items) if r]
                nxt = msg.get("next-cursor")
                if ctx.offline or not items or len(items) < rows or not nxt or nxt == cursor:
                    break
                cursor = nxt
                if page == max_pages - 1:
                    truncated = True
        if errors and not records:
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if (truncated or errors) else ("ok" if records else "empty")
        return FetchResult(self.name, status, records=records, checkpoint={**checkpoint, "since": ctx.run_date},
                           truncated=truncated, error="; ".join(errors)[:500] or None)
