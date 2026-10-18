"""What the agent found on the web: items to catalog and signals of discussion.

The agent searches the web (the CLI never does) and records its findings with `aster add`, so
they merge with collected works, count in ranking and are not reported twice. The payload:

    {"items": [{"title": ..., "url": ..., "authors": [...], "abstract": ..., "ids": {...},
                "venue": ..., "date": "YYYY-MM-DD", "kind": "paper|release",
                "status": "preprint|published|...", "fields": [...], "signals": [...]}],
     "signals": [{"url": ..., "platform": "x", "about": "<doi|arxiv id|url|work_id>",
                  "title": ..., "snippet": ..., "author": ..., "posted_at": ...,
                  "metrics": {"likes": 120}, "query": ...}]}

Metrics are recorded only as seen on the page; leave them out rather than estimate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aster.catalog import STRONG_IDS, find_work, link_signals, resolve
from aster.context import Context
from aster.ids import canonical_url, parse_ref
from aster.records import Signal, SignalOrigin, SourceRecord, Work, WorkKind, WorkStatus
from aster.sources._common import iso_date


class FoundSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    platform: str
    about: str | None = None
    title: str | None = None
    snippet: str | None = None
    author: str | None = None
    posted_at: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)
    query: str | None = None
    origin: SignalOrigin = "web_search"


class FoundItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    url: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    ids: dict[str, str] = Field(default_factory=dict)
    venue: str | None = None
    date: str | None = None
    kind: WorkKind = "paper"
    status: WorkStatus | None = None
    fields: list[str] = Field(default_factory=list)
    signals: list[FoundSignal] = Field(default_factory=list)


class Findings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[FoundItem] = Field(default_factory=list)
    signals: list[FoundSignal] = Field(default_factory=list)


def _ids(item: FoundItem) -> dict[str, str]:
    ids = parse_ref(item.url)
    for kind, value in item.ids.items():
        ids.update(parse_ref(f"{kind}:{value}") or {kind: value})
    return {k: v for k, v in ids.items() if v}


def _record(item: FoundItem, ids: dict[str, str], known_fields: set[str]) -> SourceRecord:
    primary = next((f"{t}:{ids[t]}" for t in STRONG_IDS if t != "url" and ids.get(t)), ids["url"])
    status = item.status or ("release" if item.kind == "release" else "unknown")
    return SourceRecord(
        source="web",
        source_id=primary,
        title=item.title.strip(),
        authors=item.authors,
        abstract=item.abstract,
        ids=ids,
        venue=item.venue,
        status=status,
        kind=item.kind,
        url=item.url,
        released_date=iso_date(item.date),
        fields=[f for f in item.fields if f in known_fields],
    )


def _signal(s: FoundSignal, about: dict[str, str], work_id: str | None) -> Signal:
    return Signal(
        url=canonical_url(s.url),
        platform=s.platform.strip().lower(),
        work_id=work_id,
        about=about,
        title=s.title,
        snippet=s.snippet,
        author=s.author,
        posted_at=iso_date(s.posted_at),
        metrics=s.metrics,
        query=s.query,
        origin=s.origin,
    )


def add_findings(ctx: Context, payload: dict[str, Any]) -> dict[str, Any]:
    """Store found items and signals; return what was stored and any warnings."""
    found = Findings.model_validate(payload)
    store = ctx.store
    known_fields = set(ctx.config.fields)
    warnings: list[str] = []
    for item in found.items:
        bad = [f for f in item.fields if f not in known_fields]
        if bad:
            warnings.append(f"{item.title[:60]}: unknown field(s) {', '.join(bad)} ignored")

    pairs = [(item, _ids(item)) for item in found.items]
    before = {id(item): find_work(store, ids) for item, ids in pairs}
    records = [_record(item, ids, known_fields) for item, ids in pairs]
    store.append_many(records)
    resolve(store, records)

    items_out = []
    signals: list[Signal] = []
    for (item, _), rec in zip(pairs, records, strict=True):
        items_out.append({"work_id": rec.work_id, "title": rec.title, "new": before[id(item)] is None})
        about = {"work_id": rec.work_id or ""}
        signals += [_signal(s, about, rec.work_id) for s in item.signals]
    for s in found.signals:
        about: dict[str, str] = {}
        work: Work | None = None
        if s.about:
            ref = s.about.strip()
            work = store.get(Work, ref) if ref.startswith("w_") else find_work(store, parse_ref(ref))
            about = {"work_id": ref} if ref.startswith("w_") else parse_ref(ref)
            if not about:
                warnings.append(f"signal {s.url}: could not read `about` {ref!r}")
        signals.append(_signal(s, about, work.work_id if work else None))
    store.append_many(signals)
    link_signals(store)
    return {
        "items": items_out,
        "signals": [{"url": s.url, "work_id": s.work_id} for s in signals],
        "warnings": warnings,
    }
