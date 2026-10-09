"""Helpers shared by collectors: network/fixture fetching, dates, identifier normalization."""

from __future__ import annotations

import html
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lark.context import RunContext
from lark.sources.ratelimit import LIMITER
from lark.utils import http
from lark.utils.text import normalize_arxiv_id, normalize_doi

REPO_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sources"


def fixtures_dir(source: str) -> Path:
    base = Path(os.environ["LARK_FIXTURES_DIR"]) if os.getenv("LARK_FIXTURES_DIR") else REPO_FIXTURES
    return base / source


def read_fixture(source: str, name: str) -> str | None:
    p = fixtures_dir(source) / name
    return p.read_text(encoding="utf-8") if p.exists() else None


def fetch_text(
    ctx: RunContext, source: str, url: str, *, fixture: str, params: dict | None = None, headers: dict | None = None
) -> str | None:
    """GET a URL politely, or read `fixture` from the source's fixture dir when offline.

    Offline, a missing fixture returns None (treated as "nothing there").
    """
    if ctx.offline:
        return read_fixture(source, fixture)
    return http.get_text(url, params=params, headers=headers, limiter=LIMITER)


def fetch_json(
    ctx: RunContext, source: str, url: str, *, fixture: str, params: dict | None = None, headers: dict | None = None
):
    import json

    if ctx.offline:
        txt = read_fixture(source, fixture)
        return json.loads(txt) if txt else None
    return http.get_json(url, params=params, headers=headers, limiter=LIMITER)


def run_date(ctx: RunContext) -> date:
    return date.fromisoformat(ctx.run_date)


def since_date(checkpoint: dict, ctx: RunContext, *, overlap_days: int, first_run_days: int = 7) -> date:
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


def ids_from_doi(doi: str | None) -> dict[str, str]:
    """Normalize a DOI into identifiers; arXiv and SSRN DOIs become their native ids."""
    d = normalize_doi(doi)
    if not d:
        return {}
    m = re.match(r"^10\.48550/arxiv\.(.+)$", d)
    if m:
        base, _ = normalize_arxiv_id(m.group(1))
        return {"arxiv": base} if base else {}
    m = re.match(r"^10\.2139/ssrn\.(\d+)$", d)
    if m:
        return {"ssrn": m.group(1)}
    m = re.match(r"^10\.3386/w(\d+)$", d)
    if m:
        return {"nber": f"w{m.group(1)}", "doi": d}
    return {"doi": d}


def ids_from_repec(handle: str | None) -> dict[str, str]:
    """RePEc handle -> identifiers, mapping arXiv/NBER/SSRN-backed series to native ids."""
    if not handle:
        return {}
    h = handle.strip()
    out = {"repec": h}
    m = re.match(r"^RePEc:arx:papers:(.+)$", h, flags=re.I)
    if m:
        base, _ = normalize_arxiv_id(m.group(1))
        if base:
            out["arxiv"] = base
    m = re.match(r"^RePEc:nbr:nberwo:(\d+)$", h, flags=re.I)
    if m:
        out["nber"] = f"w{m.group(1)}"
    return out


def repec_handle_from_url(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    vals = q.get("u")
    return vals[0] if vals else None
