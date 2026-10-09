from dataclasses import dataclass

@dataclass
class Paper:
    key: str
    title: str
    authors: list
    venue: str | None
    year: int | None
    doi: str | None
    arxiv_id: str | None
    url: str | None
    pdf_url: str | None
    abstract: str | None
    source: str
    published_date: str | None
    added_at: str
    is_oa: bool | None = None

@dataclass
class Signals:
    key: str
    mentions_7d: int = 0
    score: float = 0.0
