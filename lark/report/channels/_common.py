"""Helpers shared by the short-message channels (Slack, Telegram, Discord, ntfy)."""

from __future__ import annotations

import requests

from lark.report.contract import Report
from lark.report.renderers import DEPTH_LABELS


def short_text(report: Report, max_items: int = 8, *, bold: str = "*", link_fmt: str = "{label}: {url}") -> str:
    """A compact plain-text adaptation of the contract for chat-style channels."""
    lines = [f"{bold}{report.title}{bold}", report.summary]
    if not report.items and report.quiet_day_reason:
        lines.append(f"Quiet day: {report.quiet_day_reason}")
    for n in report.notes:
        lines.append(f"⚠ {n}")
    for i, it in enumerate(report.items[:max_items], 1):
        w = it.work
        link = link_fmt.format(label="link", url=w.links[0].url) if w.links else ""
        block = [
            f"{i}. {bold}{w.title}{bold} [{DEPTH_LABELS[it.reading_depth]}, {it.priority}]",
            f"   {it.why_now}",
        ]
        if it.connection:
            block.append(f"   Lark's inference ({it.connection.profile}): {it.connection.text}")
        if link:
            block.append(f"   {link}")
        lines.append("\n".join(block))
    if len(report.items) > max_items:
        lines.append(f"…and {len(report.items) - max_items} more in the full report.")
    if report.also_noted:
        lines.append("Also noted: " + "; ".join(w.title for w in report.also_noted[:5]))
    bad = [f"{s.source}: {s.status}" for s in report.sources if s.status in ("failed", "stale", "partial")]
    if bad:
        lines.append("Source problems: " + ", ".join(bad))
    return "\n\n".join(lines)


def chunk(text: str, limit: int) -> list[str]:
    """Split on paragraph (then line, then hard) boundaries so each piece fits `limit` characters."""
    out: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        pieces = [para] if len(para) <= limit else _split_long(para, limit)
        for p in pieces:
            candidate = f"{cur}\n\n{p}" if cur else p
            if len(candidate) <= limit:
                cur = candidate
            else:
                if cur:
                    out.append(cur)
                cur = p
    if cur:
        out.append(cur)
    return out


def _split_long(para: str, limit: int) -> list[str]:
    out, cur = [], ""
    for line in para.split("\n"):
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) <= limit:
            cur = candidate
        else:
            out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return out


def post(url: str, *, timeout: int = 20, **kw) -> requests.Response:
    r = requests.post(url, timeout=timeout, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r
