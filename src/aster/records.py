"""Persistent record types.

Every record is appended to a JSONL log (see aster.store) and indexed in SQLite.
`KIND` names the log; `KEY` names the field(s) identifying the record. For keyed
kinds the latest appended snapshot wins; append-only kinds keep every entry.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from aster.utils.timez import iso_now

WorkStatus = Literal["preprint", "submitted", "accepted", "published", "withdrawn", "release", "unknown"]
WorkKind = Literal["paper", "release"]
ChangeKind = Literal["new", "trivial", "substantive", "withdrawn", "unknown"]
FetchStatus = Literal["ok", "empty", "failed", "partial", "stale"]
SignalOrigin = Literal["web_search", "feed", "api", "user"]


class Record(BaseModel):
    model_config = ConfigDict(extra="ignore")

    KIND: ClassVar[str] = ""
    KEY: ClassVar[tuple[str, ...]] = ()

    def key(self) -> str | None:
        if not self.KEY:
            return None
        return "|".join(str(getattr(self, k)) for k in self.KEY)


class SourceRecord(Record):
    """One appearance of a work at one source, as normalized by a collector."""

    KIND: ClassVar[str] = "source_records"
    KEY: ClassVar[tuple[str, ...]] = ("source", "source_id")

    source: str  # e.g. "arxiv", "crossref", "feeds", "web"
    source_id: str  # stable id within the source, e.g. "2410.01234" or a DOI
    work_id: str | None = None  # filled in by the catalog
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    ids: dict[str, str] = Field(default_factory=dict)  # doi, arxiv, ssrn, openreview, repec, nber, github, hf, url
    venue: str | None = None
    status: WorkStatus = "unknown"
    kind: WorkKind = "paper"
    url: str | None = None
    pdf_url: str | None = None
    html_url: str | None = None
    released_date: str | None = None  # first public release (YYYY-MM-DD)
    published_date: str | None = None  # formal publication, if any
    version: str | None = None  # e.g. "v2"
    announce_type: str | None = None  # arXiv: new | cross | replace | replace-cross
    categories: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)  # research fields whose sources produced this record
    fetched_at: str = Field(default_factory=iso_now)
    extra: dict[str, Any] = Field(default_factory=dict)


class Work(Record):
    """The canonical record for one piece of research (or one release or post)."""

    KIND: ClassVar[str] = "works"
    KEY: ClassVar[tuple[str, ...]] = ("work_id",)

    work_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    ids: dict[str, str] = Field(default_factory=dict)
    venue: str | None = None
    status: WorkStatus = "unknown"
    kind: WorkKind = "paper"
    urls: dict[str, str] = Field(default_factory=dict)  # landing, pdf, html, published
    categories: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    released_date: str | None = None
    published_date: str | None = None
    first_seen: str = Field(default_factory=iso_now)
    last_seen: str = Field(default_factory=iso_now)
    source_keys: list[str] = Field(default_factory=list)  # "source|source_id"
    current_version: str | None = None


class MergeProposal(Record):
    """A suspected duplicate that was NOT merged automatically."""

    KIND: ClassVar[str] = "merge_proposals"
    KEY: ClassVar[tuple[str, ...]] = ("work_id", "other_work_id")

    work_id: str
    other_work_id: str
    reason: str
    score: float
    resolved: Literal["pending", "merged", "distinct"] = "pending"
    at: str = Field(default_factory=iso_now)


class Version(Record):
    KIND: ClassVar[str] = "versions"
    KEY: ClassVar[tuple[str, ...]] = ("work_id", "version_id")

    work_id: str
    version_id: str  # e.g. "arxiv:v2", "crossref:<hash>"
    text_hash: str | None = None
    fulltext_hash: str | None = None
    change: ChangeKind = "new"
    note: str | None = None
    seen_at: str = Field(default_factory=iso_now)


class Signal(Record):
    """Evidence that something is being discussed: a post, thread, upvote count or mention.

    `about` holds the identifiers of what is discussed (doi, arxiv, url, ...) so the signal can be
    linked to a work once that work is in the catalog. Metrics are only recorded when the source
    actually shows them; nothing is estimated.
    """

    KIND: ClassVar[str] = "signals"
    KEY: ClassVar[tuple[str, ...]] = ("url",)

    url: str
    platform: str  # x, bluesky, hackernews, reddit, blog, huggingface, ...
    work_id: str | None = None
    about: dict[str, str] = Field(default_factory=dict)
    title: str | None = None
    snippet: str | None = None
    author: str | None = None
    posted_at: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)  # likes, reposts, points, upvotes, comments
    query: str | None = None  # the search that found it
    origin: SignalOrigin = "web_search"
    observed_at: str = Field(default_factory=iso_now)


class Shown(Record):
    """A work that was shown to the user, so later briefs skip it."""

    KIND: ClassVar[str] = "shown"
    KEY: ClassVar[tuple[str, ...]] = ("work_id",)

    work_id: str
    context: str | None = None  # brief, buzz, sweep, gardener, ...
    at: str = Field(default_factory=iso_now)


class SourceHealth(Record):
    KIND: ClassVar[str] = "source_health"

    source: str
    status: FetchStatus
    n_records: int = 0
    truncated: bool = False
    error: str | None = None
    newest_item_date: str | None = None
    run_at: str = Field(default_factory=iso_now)


class Checkpoint(Record):
    KIND: ClassVar[str] = "checkpoints"
    KEY: ClassVar[tuple[str, ...]] = ("source",)

    source: str
    value: dict[str, Any] = Field(default_factory=dict)
    last_success: str | None = None
    at: str = Field(default_factory=iso_now)


ALL_RECORDS: tuple[type[Record], ...] = (
    SourceRecord,
    Work,
    MergeProposal,
    Version,
    Signal,
    Shown,
    SourceHealth,
    Checkpoint,
)
RECORD_BY_KIND: dict[str, type[Record]] = {r.KIND: r for r in ALL_RECORDS}
