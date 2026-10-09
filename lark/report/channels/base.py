"""Channel interface: every delivery channel adapts the Report contract to its medium."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lark.context import RunContext
from lark.report.contract import Report


@dataclass
class DeliveryResult:
    channel: str
    status: str  # sent | failed | skipped
    detail: str | None = None


class Channel(Protocol):
    name: str

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        """Whether credentials/settings exist. No network."""
        ...

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult: ...
