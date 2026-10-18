"""Helpers shared by collectors: network/fixture fetching, dates, identifier normalization."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from aster.context import Context
from aster.ids import ids_from_doi, ids_from_repec, repec_handle_from_url  # noqa: F401  (re-exported)
from aster.sources.ratelimit import LIMITER
from aster.utils import http


def fixtures_dir(ctx: Context, source: str) -> Path:
    if ctx.fixtures is None:
        raise RuntimeError("offline mode needs a fixtures folder (ASTER_FIXTURES_DIR)")
    return ctx.fixtures / source


def read_fixture(ctx: Context, source: str, name: str) -> str | None:
    p = fixtures_dir(ctx, source) / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def fetch_text(
    ctx: Context, source: str, url: str, *, fixture: str, params: dict | None = None, headers: dict | None = None
) -> str | None:
    """GET a URL politely, or read `fixture` from the source's fixture dir when offline.

    Offline, a missing fixture returns None (treated as "nothing there").
    """
    if ctx.offline:
        return read_fixture(ctx, source, fixture)
    return http.get_text(url, params=params, headers=headers, limiter=LIMITER)


def fetch_json(
    ctx: Context, source: str, url: str, *, fixture: str, params: dict | None = None, headers: dict | None = None
):
    import json

    if ctx.offline:
        txt = read_fixture(ctx, source, fixture)
        return json.loads(txt) if txt else None
    return http.get_json(url, params=params, headers=headers, limiter=LIMITER)


def run_date(ctx: Context) -> date:
    return date.fromisoformat(ctx.run_date)


def since_date(checkpoint: dict, ctx: Context, *, overlap_days: int, first_run_days: int = 7) -> date:
    """Start of the retrieval window: last checkpoint minus an overlap, or a first-run lookback."""
    if checkpoint.get("since"):
        return date.fromisoformat(checkpoint["since"]) - timedelta(days=overlap_days)
    return run_date(ctx) - timedelta(days=first_run_days)


def iso_date(value: str | None) -> str | None:
    """Best-effort YYYY-MM-DD (or YYYY-MM / YYYY) from assorted date strings."""
    if not value:
        return None
    value = value.strip()
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", value)
    if m:
        return "-".join(p for p in m.groups() if p)
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def date_parts(obj: dict | None) -> str | None:
    """Crossref {'date-parts': [[2026, 9, 14]]} -> '2026-09-14'."""
    if not obj:
        return None
    parts = (obj.get("date-parts") or [[None]])[0]
    parts = [p for p in parts if p]
    if not parts:
        return None
    return "-".join([f"{parts[0]:04d}", *[f"{p:02d}" for p in parts[1:]]])


TAG_RE = re.compile(r"<[^>]+>")


def strip_markup(text: str | None) -> str | None:
    """Remove JATS/HTML tags, unescape entities, collapse whitespace."""
    if not text:
        return None
    t = TAG_RE.sub(" ", text)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(abstract|summary)[:.]?\s+", "", t, flags=re.I)
    return t or None


def split_authors(s: str | None) -> list[str]:
    if not s:
        return []
    s = re.sub(r"\s+", " ", s).strip().strip(",")
    parts = re.split(r",\s*|\s+and\s+|;\s*", s)
    return [p.strip() for p in parts if p.strip()]


ARXIV_MENTION = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv:)\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)


def related_arxiv(*texts: str | None) -> list[str]:
    """arXiv ids mentioned in release notes or posts, kept apart from the item's own ids."""
    found: list[str] = []
    for t in texts:
        for m in ARXIV_MENTION.finditer(t or ""):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found


def window_start(checkpoint: dict, ctx: Context, *, overlap_days: int, first_run_days: int) -> str:
    """Oldest release date to keep for date-filtered sources (feeds, releases, models)."""
    if checkpoint.get("since"):
        start = date.fromisoformat(checkpoint["since"]) - timedelta(days=overlap_days)
    else:
        start = run_date(ctx) - timedelta(days=first_run_days)
    return start.isoformat()
