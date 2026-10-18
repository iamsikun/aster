"""Collector interface.

A collector turns one source into normalized SourceRecords. It must distinguish
"fetched fine, nothing new" (empty) from "fetch failed" (failed), report truncation
(partial), and return a new checkpoint. It never decides relevance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from aster.context import Context
from aster.records import FetchStatus, Signal, SourceRecord


@dataclass
class FetchResult:
    source: str
    status: FetchStatus
    records: list[SourceRecord] = field(default_factory=list)
    checkpoint: dict[str, Any] = field(default_factory=dict)  # new checkpoint; only saved if status != failed
    error: str | None = None
    truncated: bool = False
    signals: list[Signal] = field(default_factory=list)  # discussion evidence, e.g. upvotes


class Collector(Protocol):
    name: str

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult: ...
