"""Persistent record types.

Every record is appended to a JSONL log (see lark.store) and indexed in SQLite.
`KIND` names the log; `KEY` names the field(s) identifying the record. For keyed
kinds the latest appended snapshot wins; append-only kinds keep every entry.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from lark.utils.timez import iso_now

WorkStatus = Literal["preprint", "submitted", "accepted", "published", "withdrawn", "release", "unknown"]
WorkKind = Literal["paper", "release"]
Priority = Literal["high", "medium", "low", "none"]
NextAction = Literal["focused_read", "full_read", "abstract_only", "skip", "insufficient_info"]
ReadingDepth = Literal["abstract", "focused", "full", "technical"]
ChangeKind = Literal["new", "trivial", "substantive", "withdrawn", "unknown"]
FetchStatus = Literal["ok", "empty", "failed", "partial", "stale"]
JobState = Literal["queued", "running", "done", "failed", "deferred"]
JobKind = Literal["screen", "read", "analyze", "audit", "parse", "embed", "deep", "synthesize"]
FeedbackLabel = Literal["useful", "peripheral", "known", "wrong_topic", "missed_key", "deep_read"]


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

    source: str  # e.g. "arxiv", "crossref", "openreview", "ssrn_alerts", "github"
    source_id: str  # stable id within the source, e.g. "2410.01234" or a DOI
    work_id: str | None = None  # filled in by the catalog
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    ids: dict[str, str] = Field(default_factory=dict)  # doi, arxiv, ssrn, openreview, repec, nber, github, hf
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
    fetched_at: str = Field(default_factory=iso_now)
    extra: dict[str, Any] = Field(default_factory=dict)


class Work(Record):
    """The canonical record for one piece of research (or one release event)."""

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
    urls: dict[str, str] = Field(default_factory=dict)  # landing, pdf, html, code, model
    categories: list[str] = Field(default_factory=list)
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
    version_id: str  # e.g. "arxiv:v2", "doi", "ssrn:2026-10-01"
    text_hash: str | None = None  # hash of title+abstract, or full text when available
    fulltext_hash: str | None = None
    change: ChangeKind = "new"
    note: str | None = None
    seen_at: str = Field(default_factory=iso_now)


class LLMCall(BaseModel):
    """Provenance for any model output."""

    model_config = ConfigDict(extra="ignore")

    role: str
    route: str  # configured route name, e.g. "claude_haiku"
    provider: str  # anthropic | openai | google | local | fake
    model: str  # resolved model id
    billing: str  # subscription | api_credit | api_paid | local | none
    prompt_version: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_s: float | None = None


class Screening(Record):
    KIND: ClassVar[str] = "screenings"
    KEY: ClassVar[tuple[str, ...]] = ("work_id", "profiles_version")

    work_id: str
    profiles_version: str
    topics: list[str] = Field(default_factory=list)  # profile ids
    priority: Priority = "none"
    next_action: NextAction = "skip"
    reason: str = ""
    information_missing: list[str] = Field(default_factory=list)
    retrieval_routes: list[str] = Field(default_factory=list)  # bm25, embedding, watchlist, exploration
    retrieval_score: float = 0.0
    audit: bool = False  # selected as an audit sample from exclusions
    llm: LLMCall | None = None
    at: str = Field(default_factory=iso_now)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quote: str
    location: str | None = None  # "Section 3.2", "p. 7", "Table 2", "abstract"
    url: str | None = None
    verified: bool | None = None  # None = not checked; True = quote found in source text


class Claim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    kind: Literal["paper_result", "author_interpretation"] = "paper_result"
    evidence: list[Evidence] = Field(default_factory=list)
    verified: bool | None = None


class Connection(BaseModel):
    """Lark's own inference about relevance to the user's research. Never a paper finding."""

    model_config = ConfigDict(extra="ignore")

    profile: str
    text: str


class ReadingNote(Record):
    KIND: ClassVar[str] = "readings"
    KEY: ClassVar[tuple[str, ...]] = ("work_id", "version_id", "depth")

    work_id: str
    version_id: str | None = None
    depth: ReadingDepth
    full_text_obtained: bool = False
    sections_read: list[str] = Field(default_factory=list)
    question: str | None = None
    obstacle: str | None = None
    idea: str | None = None
    mechanism: str | None = None
    main_result: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    connection: Connection | None = None
    escalate: list[str] = Field(default_factory=list)  # specific questions for a stronger model
    verification: dict[str, Any] = Field(default_factory=dict)  # summary from lark.read.verify
    llm: LLMCall | None = None
    at: str = Field(default_factory=iso_now)


class Analysis(Record):
    KIND: ClassVar[str] = "analyses"
    KEY: ClassVar[tuple[str, ...]] = ("work_id", "question_id")

    work_id: str
    question_id: str
    question: str
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence_note: str | None = None
    llm: LLMCall | None = None
    at: str = Field(default_factory=iso_now)


class Job(Record):
    KIND: ClassVar[str] = "jobs"
    KEY: ClassVar[tuple[str, ...]] = ("job_id",)

    job_id: str
    kind: JobKind
    work_id: str | None = None
    priority: int = 50  # higher runs first
    tag: Literal["any", "gpu"] = "any"
    state: JobState = "queued"
    attempts: int = 0
    max_attempts: int = 3
    not_before: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    last_error: str | None = None
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)


class LedgerEntry(Record):
    """Cash and capacity accounting. Reservations are made before dispatch."""

    KIND: ClassVar[str] = "ledger"

    entry_id: str
    job_id: str | None = None
    route: str
    provider: str
    model: str | None = None
    billing: str
    event: Literal["reserve", "settle", "release", "rate_limited", "auth_failed"]
    cash_usd: float = 0.0  # positive = spend (reserve/settle); release negates a reservation
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    note: str | None = None
    at: str = Field(default_factory=iso_now)


class SourceHealth(Record):
    KIND: ClassVar[str] = "source_health"

    source: str
    status: FetchStatus
    n_records: int = 0
    n_new_works: int = 0
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


class Delivery(Record):
    KIND: ClassVar[str] = "deliveries"
    KEY: ClassVar[tuple[str, ...]] = ("report_id", "channel")

    report_id: str
    channel: str
    status: Literal["sent", "failed", "skipped"]
    detail: str | None = None
    at: str = Field(default_factory=iso_now)


class Feedback(Record):
    KIND: ClassVar[str] = "feedback"
    KEY: ClassVar[tuple[str, ...]] = ("feedback_id",)

    feedback_id: str
    work_id: str | None = None
    report_id: str | None = None
    label: FeedbackLabel
    profile: str | None = None
    note: str | None = None
    origin: str = "github_issue"  # github_issue | cli | email
    at: str = Field(default_factory=iso_now)


class Briefed(Record):
    """A work appearing in a delivered report; used to avoid repeats and detect updates."""

    KIND: ClassVar[str] = "briefed"
    KEY: ClassVar[tuple[str, ...]] = ("work_id", "report_id")

    work_id: str
    report_id: str
    version_id: str | None = None
    depth: ReadingDepth = "abstract"
    at: str = Field(default_factory=iso_now)


ALL_RECORDS: tuple[type[Record], ...] = (
    SourceRecord,
    Work,
    MergeProposal,
    Version,
    Screening,
    ReadingNote,
    Analysis,
    Job,
    LedgerEntry,
    SourceHealth,
    Checkpoint,
    Delivery,
    Feedback,
    Briefed,
)
RECORD_BY_KIND: dict[str, type[Record]] = {r.KIND: r for r in ALL_RECORDS}
