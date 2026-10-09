"""Catalog: one canonical Work per piece of research, linked to all its source appearances.

Exact identifiers decide merges. Title/author similarity only *proposes* a merge
(MergeProposal) and never merges silently. When one record's identifiers point at two
different existing works, it attaches to the first and proposes merging the other.

Source records are linked through `Work.source_keys` ("source|source_id"); `resolve` sets
`work_id` on the in-memory records but does not re-append them (collect already stored them),
which keeps the committed log from doubling. Use `work_for_source` to look up the link.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lark.catalog.versions import classify_change, record_text, version_for
from lark.context import RunContext
from lark.records import MergeProposal, SourceRecord, Version, Work
from lark.utils.text import short_hash, text_hash, title_key
from lark.utils.timez import iso_now

log = logging.getLogger(__name__)

# Strong identifiers, in matching priority order.
STRONG_IDS = ("doi", "arxiv", "ssrn", "openreview", "nber", "repec", "openalex", "github", "hf", "blog")
STATUS_RANK = {"unknown": 0, "preprint": 1, "submitted": 2, "accepted": 3, "published": 4, "release": 1}

__all__ = ["ResolveStats", "resolve", "work_for_source", "classify_change", "STRONG_IDS"]


@dataclass
class ResolveStats:
    new_work_ids: list[str] = field(default_factory=list)
    updated_work_ids: list[str] = field(default_factory=list)
    substantive_update_ids: list[str] = field(default_factory=list)  # substantive/withdrawn revision or status upgrade
    proposals: list[MergeProposal] = field(default_factory=list)


def _surname(authors: list[str]) -> str:
    if not authors:
        return ""
    first = authors[0].strip()
    if "," in first:
        return first.split(",")[0].strip().lower()
    return (first.split() or [""])[-1].lower()


def new_work_id(rec: SourceRecord) -> str:
    for t in STRONG_IDS:
        if rec.ids.get(t):
            return "w_" + short_hash(f"{t}:{rec.ids[t].lower()}")
    return "w_" + short_hash("title", title_key(rec.title), _surname(rec.authors))


class _Index:
    def __init__(self, works: list[Work]):
        self.by_id: dict[tuple[str, str], str] = {}
        self.by_title: dict[str, list[tuple[str, str]]] = {}
        for w in works:
            self.add(w)

    def add(self, w: Work) -> None:
        for t, v in w.ids.items():
            if t in STRONG_IDS and v:
                self.by_id.setdefault((t, v.lower()), w.work_id)
        tk = title_key(w.title)
        if tk:
            entry = (w.work_id, _surname(w.authors))
            lst = self.by_title.setdefault(tk, [])
            if entry not in lst:
                lst.append(entry)

    def matches(self, rec: SourceRecord) -> list[str]:
        out: list[str] = []
        for t in STRONG_IDS:
            v = rec.ids.get(t)
            if v and (wid := self.by_id.get((t, v.lower()))) and wid not in out:
                out.append(wid)
        return out

    def fuzzy(self, rec: SourceRecord, exclude: str) -> list[str]:
        tk = title_key(rec.title)
        if len(tk) < 20:  # short titles ("Introduction", "Comment") are too ambiguous to propose
            return []
        sn = _surname(rec.authors)
        return [wid for wid, s in self.by_title.get(tk, []) if wid != exclude and s and sn and s == sn]


def _work_from(rec: SourceRecord, wid: str) -> Work:
    urls = {k: v for k, v in {"landing": rec.url, "pdf": rec.pdf_url, "html": rec.html_url}.items() if v}
    return Work(
        work_id=wid,
        title=rec.title,
        authors=list(rec.authors),
        abstract=rec.abstract,
        ids={t: v for t, v in rec.ids.items() if v},
        venue=rec.venue,
        status=rec.status,
        kind=rec.kind,
        urls=urls,
        categories=list(rec.categories),
        released_date=rec.released_date,
        published_date=rec.published_date,
        source_keys=[f"{rec.source}|{rec.source_id}"],
        current_version=None,
    )


def _merge(work: Work, rec: SourceRecord) -> tuple[bool, bool]:
    """Merge `rec` into `work` in place. Returns (changed, status_upgraded)."""
    before = work.model_dump(exclude={"last_seen"})
    for t, v in rec.ids.items():
        if v and t not in work.ids:
            work.ids[t] = v
    sk = f"{rec.source}|{rec.source_id}"
    if sk not in work.source_keys:
        work.source_keys.append(sk)
    for k, v in (("landing", rec.url), ("pdf", rec.pdf_url), ("html", rec.html_url)):
        if v and k not in work.urls:
            work.urls[k] = v
    upgraded = False
    if rec.status == "withdrawn" and rec.source == "arxiv":
        upgraded = work.status != "withdrawn"
        work.status = "withdrawn"
    elif work.status != "withdrawn" and STATUS_RANK.get(rec.status, 0) > STATUS_RANK.get(work.status, 0):
        upgraded = work.status != "unknown" and rec.status in ("accepted", "published")
        work.status = rec.status
        if rec.status in ("accepted", "published"):
            if rec.venue:
                work.venue = rec.venue
            if rec.url:
                work.urls["published"] = rec.url
    if rec.released_date and (not work.released_date or rec.released_date < work.released_date):
        work.released_date = rec.released_date
    if rec.published_date and not work.published_date:
        work.published_date = rec.published_date
    if rec.version and rec.source == "arxiv":
        # A new arXiv version carries the authors' current title/abstract.
        work.title = rec.title or work.title
        work.abstract = rec.abstract or work.abstract
    else:
        if not work.abstract and rec.abstract:
            work.abstract = rec.abstract
        if not work.title and rec.title:
            work.title = rec.title
    if not work.authors and rec.authors:
        work.authors = list(rec.authors)
    if not work.venue and rec.venue:
        work.venue = rec.venue
    for c in rec.categories:
        if c not in work.categories:
            work.categories.append(c)
    work.last_seen = iso_now()
    return work.model_dump(exclude={"last_seen"}) != before, upgraded


def resolve(ctx: RunContext, records: list[SourceRecord]) -> ResolveStats:
    store = ctx.store
    stats = ResolveStats()
    index = _Index(store.all(Work))
    touched: dict[str, Work] = {}
    new_ids: set[str] = set()
    versions_out: list[Version] = []
    known_versions: dict[str, set[str]] = {}
    last_hash: dict[str, str | None] = {}
    existing_props = {p.key() for p in store.all(MergeProposal)}
    props: dict[str, MergeProposal] = {}

    def load(wid: str) -> Work | None:
        if wid in touched:
            return touched[wid]
        return store.get(Work, wid)

    def versions_of(wid: str) -> set[str]:
        if wid not in known_versions:
            vs = store.all(Version, "json_extract(json, '$.work_id') = ?", (wid,))
            known_versions[wid] = {v.version_id for v in vs}
            last_hash[wid] = vs[-1].text_hash if vs else None
        return known_versions[wid]

    def propose(a: str, b: str, reason: str, score: float) -> None:
        a, b = sorted((a, b))
        p = MergeProposal(work_id=a, other_work_id=b, reason=reason, score=score)
        if p.key() not in existing_props and p.key() not in props:
            props[p.key()] = p

    for rec in records:
        if not rec.title:
            continue
        matches = index.matches(rec)
        if matches:
            wid = matches[0]
            for other in matches[1:]:
                propose(wid, other, f"identifier conflict via {rec.source}:{rec.source_id}", 1.0)
            work = load(wid)
        else:
            wid = new_work_id(rec)
            work = load(wid)  # deterministic id may already exist (e.g. same title-hash work)
        rec.work_id = wid

        if work is None:
            work = _work_from(rec, wid)
            v = version_for(None, rec, versions_of(wid), last_hash.get(wid))
            if v:
                v.work_id = wid
                versions_out.append(v)
                known_versions[wid].add(v.version_id)
                last_hash[wid] = v.text_hash
                work.current_version = v.version_id
            touched[wid] = work
            new_ids.add(wid)
            index.add(work)
            for other in index.fuzzy(rec, exclude=wid):
                propose(wid, other, "same normalized title and first-author surname", 0.9)
            continue

        v = version_for(work, rec, versions_of(wid), last_hash.get(wid))
        changed, upgraded = _merge(work, rec)
        if v:
            versions_out.append(v)
            known_versions[wid].add(v.version_id)
            last_hash[wid] = v.text_hash
            work.current_version = v.version_id
            changed = True
        touched[wid] = work
        index.add(work)
        if changed and wid not in new_ids and wid not in stats.updated_work_ids:
            stats.updated_work_ids.append(wid)
        substantive = upgraded or (v is not None and v.change in ("substantive", "withdrawn"))
        if substantive and wid not in new_ids and wid not in stats.substantive_update_ids:
            stats.substantive_update_ids.append(wid)

    store.append_many([*touched.values(), *versions_out, *props.values()])
    stats.new_work_ids = [w for w in touched if w in new_ids]
    stats.proposals = list(props.values())
    log.info(
        "catalog: %d new works, %d updated (%d substantive), %d merge proposals",
        len(stats.new_work_ids), len(stats.updated_work_ids), len(stats.substantive_update_ids), len(stats.proposals),
    )
    return stats


def work_for_source(ctx: RunContext, source: str, source_id: str) -> Work | None:
    """The Work a source record was resolved into."""
    key = f"{source}|{source_id}"
    hits = ctx.store.all(Work, "EXISTS (SELECT 1 FROM json_each(latest.json, '$.source_keys') je WHERE je.value = ?)", (key,))
    return hits[0] if hits else None


def text_of(work: Work) -> str:
    return record_text(work.title, work.abstract)


def work_text_hash(work: Work) -> str:
    return text_hash(text_of(work))
