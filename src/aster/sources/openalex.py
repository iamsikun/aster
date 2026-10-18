"""OpenAlex: cross-disciplinary discovery from field keywords, interests and followed authors.

Uses `from_publication_date` with an overlap window (the created/updated-date filters are
part of OpenAlex's paid sync product). Keywords for one profile are OR-ed in a single
`title_and_abstract.search` filter; followed authors use `raw_author_name.search` or an
OpenAlex author id (A123...).
"""

from __future__ import annotations

import os
import re
from typing import Any

from aster.context import Context
from aster.ids import ids_from_doi
from aster.records import SourceRecord
from aster.sources._common import fetch_json, iso_date, since_date, strip_markup
from aster.sources.base import FetchResult
from aster.utils.http import contact_email
from aster.utils.text import short_hash

API = "https://api.openalex.org/works"
SELECT = (
    "id,doi,title,display_name,authorships,publication_date,primary_location,open_access,"
    "abstract_inverted_index,ids,type,best_oa_location"
)


def reconstruct_abstract(inv: dict[str, list[int]] | None) -> str | None:
    if not inv:
        return None
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos)) or None


def parse_work(w: dict) -> SourceRecord | None:
    wid = (w.get("id") or "").rsplit("/", 1)[-1]
    title = strip_markup(w.get("title") or w.get("display_name"))
    if not wid or not title:
        return None
    ids: dict[str, str] = {"openalex": wid}
    ids.update(ids_from_doi(w.get("doi")))
    for k, v in (w.get("ids") or {}).items():
        if k == "pmid" and v:
            ids["pmid"] = str(v).rsplit("/", 1)[-1]
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    oa = w.get("best_oa_location") or {}
    wtype = w.get("type") or ""
    is_preprint = wtype == "preprint" or "arxiv" in ids or "ssrn" in ids
    authors = [(a.get("author") or {}).get("display_name") for a in w.get("authorships") or []]
    return SourceRecord(
        source="openalex",
        source_id=wid,
        title=title,
        authors=[a for a in authors if a],
        abstract=reconstruct_abstract(w.get("abstract_inverted_index")),
        ids=ids,
        venue=src.get("display_name"),
        status="preprint" if is_preprint else "published",
        url=loc.get("landing_page_url") or w.get("doi"),
        pdf_url=loc.get("pdf_url") or oa.get("pdf_url"),
        released_date=iso_date(w.get("publication_date")),
        published_date=None if is_preprint else iso_date(w.get("publication_date")),
        extra={"type": wtype, "is_oa": (w.get("open_access") or {}).get("is_oa")},
    )


def _quote(term: str) -> str:
    term = term.replace("|", " ").replace(",", " ").strip()
    return f'"{term}"' if " " in term else term


def build_queries(ctx: Context) -> list[tuple[str, str]]:
    """(label, filter fragment) pairs from field keywords, interests and followed authors."""
    out: list[tuple[str, str]] = []
    for f in ctx.config.active_fields():
        kws = [k for k in f.keywords if k.strip()]
        if kws:
            out.append((f"field:{f.id}", "title_and_abstract.search:" + "|".join(_quote(k) for k in kws[:20])))
    if interests := ctx.config.interests:
        out.append(("interests", "title_and_abstract.search:" + "|".join(_quote(k) for k in interests[:20])))
    authors = ctx.config.follows.authors
    for a in dict.fromkeys(a.strip() for a in authors if a and a.strip()):
        if re.fullmatch(r"A\d+", a):
            out.append((f"author:{a}", f"authorships.author.id:{a}"))
        else:
            out.append((f"author:{a}", f"raw_author_name.search:{a.replace(',', ' ')}"))
    return out


class OpenAlexCollector:
    name = "openalex"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        cfg = ctx.config.get("sources.openalex", {}) or {}
        queries = build_queries(ctx)[: int(cfg.get("max_queries", 40))]
        if not queries:
            return FetchResult(
                self.name,
                "empty",
                checkpoint={**checkpoint, "since": ctx.run_date},
                error="no field keywords, interests or followed authors",
            )
        frm = since_date(checkpoint, ctx, overlap_days=int(cfg.get("overlap_days", 3))).isoformat()
        per_page = int(cfg.get("per_page", 100))
        max_pages = int(cfg.get("max_pages", 2))
        common: dict[str, str] = {"select": SELECT}
        if os.getenv("OPENALEX_API_KEY"):
            common["api_key"] = os.environ["OPENALEX_API_KEY"]
        if contact_email():
            common["mailto"] = contact_email()

        by_id: dict[str, SourceRecord] = {}
        truncated = False
        errors: list[str] = []
        for label, frag in queries:
            for page in range(1, max_pages + 1):
                params = {**common, "filter": f"{frag},from_publication_date:{frm}", "per_page": per_page, "page": page}
                fixture = f"{label.split(':')[0]}_{short_hash(label, n=8)}.json"
                try:
                    data = fetch_json(ctx, self.name, API, fixture=fixture, params=params)
                except Exception as e:
                    errors.append(f"{label}: {e}")
                    break
                if ctx.offline and data is None:
                    data = fetch_json(ctx, self.name, API, fixture="search.json") if page == 1 else None
                results = (data or {}).get("results") or []
                for w in results:
                    rec = parse_work(w)
                    if rec:
                        kept = by_id.setdefault(rec.source_id, rec)
                        kept.extra.setdefault("queries", []).append(label)
                        if label.startswith("field:") and label[6:] not in kept.fields:
                            kept.fields.append(label[6:])
                count = ((data or {}).get("meta") or {}).get("count") or 0
                if ctx.offline or len(results) < per_page or page * per_page >= count:
                    break
                if page == max_pages:
                    truncated = True
        records = list(by_id.values())
        if errors and not records:
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if (truncated or errors) else ("ok" if records else "empty")
        return FetchResult(
            self.name,
            status,
            records=records,
            checkpoint={**checkpoint, "since": ctx.run_date},
            truncated=truncated,
            error="; ".join(errors)[:500] or None,
        )
