"""Retrieve full text for a selected work.

Order: arXiv HTML (LaTeXML) -> arXiv PDF -> the work's html_url -> its pdf_url.
Raw bytes are cached under <data>/cache/raw/<work_id>/ (not committed); parsed text
is cached by lark.read.parse under <data>/cache/fulltext/ (committed).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lark.context import RunContext
from lark.records import Work
from lark.sources.ratelimit import LIMITER
from lark.utils import http
from lark.utils.text import normalize_arxiv_id

log = logging.getLogger(__name__)

MAX_BYTES = 40_000_000
DocKind = Literal["html", "pdf", "text"]
_EXT = {"html": "html", "pdf": "pdf", "text": "txt"}


@dataclass
class Document:
    work_id: str
    source_url: str
    kind: DocKind
    path: Path
    sha256: str

    @property
    def sha12(self) -> str:
        return self.sha256[:12]

    def read_bytes(self) -> bytes:
        return self.path.read_bytes()


def raw_dir(ctx: RunContext, work_id: str) -> Path:
    return ctx.data_dir / "cache" / "raw" / work_id


def candidate_urls(work: Work) -> list[tuple[str, DocKind]]:
    out: list[tuple[str, DocKind]] = []
    base, ver = normalize_arxiv_id(work.ids.get("arxiv"))
    if base:
        v = ver or ""
        out.append((f"https://arxiv.org/html/{base}{v}", "html"))
        out.append((f"https://arxiv.org/pdf/{base}{v}", "pdf"))
    if html := work.urls.get("html"):
        out.append((html, "html"))
    if pdf := work.urls.get("pdf"):
        out.append((pdf, "pdf"))
    seen: set[str] = set()
    return [(u, k) for u, k in out if not (u in seen or seen.add(u))]


def _sniff_kind(content: bytes, content_type: str, guess: DocKind) -> DocKind | None:
    head = content[:1024].lstrip()
    if head.startswith(b"%PDF"):
        return "pdf"
    if "html" in content_type or head[:15].lower().startswith((b"<!doctype html", b"<html")):
        return "html"
    if "text/plain" in content_type:
        return "text"
    return guess if guess != "pdf" else None


def _usable_html(content: bytes, url: str) -> bool:
    if "arxiv.org/html/" not in url:
        return len(content) > 2000
    # arXiv serves an error page (sometimes 200) when no HTML rendering exists.
    return b"ltx_document" in content or b"ltx_page_main" in content


def cached_document(ctx: RunContext, work_id: str) -> Document | None:
    folder = raw_dir(ctx, work_id)
    if not folder.exists():
        return None
    files = sorted(folder.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        if p.suffix == ".url":
            continue
        kind = {"html": "html", "pdf": "pdf", "txt": "text"}.get(p.suffix.lstrip("."))
        if not kind:
            continue
        url_file = p.with_suffix(".url")
        url = url_file.read_text().strip() if url_file.exists() else ""
        return Document(work_id, url, kind, p, hashlib.sha256(p.read_bytes()).hexdigest())  # type: ignore[arg-type]
    return None


def store_document(ctx: RunContext, work_id: str, url: str, kind: DocKind, content: bytes) -> Document:
    sha = hashlib.sha256(content).hexdigest()
    folder = raw_dir(ctx, work_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha[:12]}.{_EXT[kind]}"
    path.write_bytes(content)
    path.with_suffix(".url").write_text(url)
    return Document(work_id, url, kind, path, sha)


def fetch_fulltext(ctx: RunContext, work: Work, *, refresh: bool = False) -> Document | None:
    if not refresh and (doc := cached_document(ctx, work.work_id)):
        return doc
    if ctx.offline:
        return None
    for url, guess in candidate_urls(work):
        try:
            r = http.get(url, timeout=60, limiter=LIMITER)
        except Exception as e:  # noqa: BLE001 - try the next candidate
            log.info("fulltext %s: %s", url, e)
            continue
        content = r.content
        if not content or len(content) > MAX_BYTES:
            continue
        kind = _sniff_kind(content, r.headers.get("content-type", ""), guess)
        if kind is None or (kind == "html" and not _usable_html(content, url)):
            continue
        return store_document(ctx, work.work_id, url, kind, content)
    return None
