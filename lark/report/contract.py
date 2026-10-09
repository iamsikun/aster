"""The report contract (v1).

Every delivery channel consumes a `Report`. Channels may render it however suits the
medium, but must not invent content outside it. The JSON Schema is exported to
docs/report-contract/v1.schema.json (`lark contract export`), so non-Python consumers
can validate against it too.

Rules the contract encodes:
- `reading_depth` states what was actually read; "full text obtained" is not "full paper read".
- A `Claim` is either a paper result or the authors' interpretation, with evidence anchors.
- `connection` is always Lark's inference about relevance, never a finding of the paper.
- A quiet day is valid: `items` may be empty, with `quiet_day_reason` set.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

ReportKind = Literal["daily", "weekly", "deep"]
Depth = Literal["abstract", "focused", "full", "technical"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Link(_M):
    label: str  # "arXiv", "PDF", "DOI", "Code", "Model"
    url: str


class WorkRef(_M):
    work_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    status: Literal["preprint", "submitted", "accepted", "published", "withdrawn", "release", "unknown"] = "unknown"
    kind: Literal["paper", "release"] = "paper"
    released_date: str | None = None
    ids: dict[str, str] = Field(default_factory=dict)
    links: list[Link] = Field(default_factory=list)


class EvidenceAnchor(_M):
    quote: str
    location: str | None = None
    url: str | None = None
    verified: bool | None = None  # True: quote found verbatim in the source text Lark holds


class Claim(_M):
    text: str
    kind: Literal["paper_result", "author_interpretation"]
    evidence: list[EvidenceAnchor] = Field(default_factory=list)


class Contribution(_M):
    question: str | None = None
    obstacle: str | None = None
    idea: str | None = None
    main_result: str | None = None


class Connection(_M):
    profile: str
    text: str
    label: Literal["lark_inference"] = "lark_inference"


class FeedbackAction(_M):
    label: str  # "Useful for selection-inference", "Already knew", ...
    url: str
    value: Literal["useful", "peripheral", "known", "wrong_topic", "missed_key", "deep_read"]


class ReportItem(_M):
    item_id: str
    work: WorkRef
    profiles: list[str] = Field(default_factory=list)
    priority: Literal["high", "medium", "low"] = "medium"
    reading_depth: Depth
    sections_read: list[str] = Field(default_factory=list)
    is_update_of: str | None = None  # report_id where this work last appeared, if this is a meaningful update
    update_note: str | None = None
    why_now: str  # one line: why this deserves attention today
    contribution: Contribution = Field(default_factory=Contribution)
    claims: list[Claim] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    connection: Connection | None = None
    analysis: str | None = None  # stronger-model answer to a targeted question, if any
    feedback_actions: list[FeedbackAction] = Field(default_factory=list)


class Theme(_M):
    """Weekly synthesis: a development connecting several works."""

    title: str
    text: str
    work_ids: list[str] = Field(default_factory=list)


class SourceStatus(_M):
    source: str
    status: Literal["ok", "empty", "failed", "partial", "stale"]
    n_records: int = 0
    note: str | None = None


class RouteStatus(_M):
    route: str
    ok: bool
    calls_today: int = 0
    note: str | None = None


class BudgetSummary(_M):
    cash_today_usd: float = 0.0
    cash_month_usd: float = 0.0
    monthly_cap_usd: float = 0.0
    routes: list[RouteStatus] = Field(default_factory=list)


class Stats(_M):
    collected: int = 0
    new_works: int = 0
    candidates: int = 0
    screened: int = 0
    read: int = 0
    analyzed: int = 0
    backlog: int = 0


class Report(_M):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    report_id: str  # e.g. "daily-2026-10-09"
    kind: ReportKind
    title: str
    generated_at: str
    period_start: str
    period_end: str
    summary: str  # 1–3 sentences
    quiet_day_reason: str | None = None
    items: list[ReportItem] = Field(default_factory=list)
    themes: list[Theme] = Field(default_factory=list)
    also_noted: list[WorkRef] = Field(default_factory=list)  # one-line mentions that didn't make the cut
    sources: list[SourceStatus] = Field(default_factory=list)
    budget: BudgetSummary = Field(default_factory=BudgetSummary)
    stats: Stats = Field(default_factory=Stats)
    notes: list[str] = Field(default_factory=list)  # operational warnings (auth expired, fallbacks used)


def json_schema() -> dict:
    schema = Report.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://github.com/iamsikun/lark/docs/report-contract/v1.schema.json"
    return schema
