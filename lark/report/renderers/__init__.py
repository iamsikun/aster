"""Render a Report into markdown, HTML (email-safe), or plain text.

All three formats come from the same view model (`view()`), so they cannot drift apart
in what they say; only presentation differs.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from lark.report.contract import Report

TEMPLATES = Path(__file__).with_name("templates")

DEPTH_LABELS = {
    "abstract": "Abstract only",
    "focused": "Focused read",
    "full": "Full paper read",
    "technical": "Technical investigation",
}
DEPTH_COLORS = {  # background, text — readable on light and dark clients
    "abstract": ("#e8e8e8", "#333333"),
    "focused": ("#dbeafe", "#1e3a8a"),
    "full": ("#dcfce7", "#14532d"),
    "technical": ("#ede9fe", "#4c1d95"),
}
CLAIM_KIND_LABELS = {"paper_result": "Paper result", "author_interpretation": "Authors' interpretation"}
STATUS_LABELS = {
    "preprint": "Preprint",
    "submitted": "Submitted",
    "accepted": "Accepted",
    "published": "Published",
    "withdrawn": "Withdrawn",
    "release": "Release",
    "unknown": "",
}
FLAG_STATUSES = {"failed", "stale", "partial"}


def sections_summary(sections: list[str], limit: int = 6) -> str:
    """Short list for display; the full list stays in report.json."""
    if len(sections) <= limit:
        return ", ".join(sections)
    has_appendix = any(re.match(r"(appendix|[A-H](\.\d+)?\s)", x.strip(), re.I) for x in sections)
    return f"{len(sections)} sections{' incl. appendices' if has_appendix else ''}"


def _env(autoescape: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["sections_summary"] = sections_summary
    env.globals.update(
        DEPTH_LABELS=DEPTH_LABELS,
        DEPTH_COLORS=DEPTH_COLORS,
        CLAIM_KIND_LABELS=CLAIM_KIND_LABELS,
        STATUS_LABELS=STATUS_LABELS,
        FLAG_STATUSES=FLAG_STATUSES,
    )
    return env


def verified_mark(verified: bool | None, *, unicode: bool = True) -> str:
    if verified:
        return "✓ verified" if unicode else "verified"
    return "unverified"


def view(report: Report) -> dict:
    flagged = [s for s in report.sources if s.status in FLAG_STATUSES]
    return {
        "r": report,
        "flagged_sources": flagged,
        "ok_sources": [s for s in report.sources if s.status not in FLAG_STATUSES],
        "verified_mark": verified_mark,
    }


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def render_markdown(report: Report) -> str:
    return _tidy(_env(False).get_template("report.md.j2").render(**view(report)))


def render_text(report: Report) -> str:
    return _env(False).get_template("report.txt.j2").render(**view(report)).strip() + "\n"


def render_html(report: Report) -> str:
    return _env(True).get_template("report.html.j2").render(**view(report))
