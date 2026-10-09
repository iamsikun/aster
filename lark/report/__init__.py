"""Report contract, renderers, and delivery channels."""

from __future__ import annotations

from typing import Literal

from lark.report.contract import Report


def render(report: Report, fmt: Literal["markdown", "html", "text"]) -> str:
    from lark.report.renderers import render_html, render_markdown, render_text

    return {"markdown": render_markdown, "html": render_html, "text": render_text}[fmt](report)
