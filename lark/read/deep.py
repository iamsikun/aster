"""On-request deep reading of one paper given a URL, arXiv id, or DOI."""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET

from lark.context import RunContext
from lark.read.parse import get_parsed
from lark.read.reader import (
    FULL_BUDGET_TOKENS,
    READING_SCHEMA,
    Material,
    ReadFailed,
    _llm,
    all_sections,
    call_reader,
    est_tokens,
    latest_screening,
    load_prompt,
    note_from_reply,
    relevant_profiles,
    version_for,
)
from lark.read.verify import verify_note
from lark.records import ReadingNote, SourceRecord, Work
from lark.sources.ratelimit import LIMITER
from lark.utils import http
from lark.utils.text import normalize_arxiv_id, normalize_doi, short_hash

log = logging.getLogger(__name__)

_ARXIV_NEW = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
_ARXIV_OLD = re.compile(r"^([a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)


def resolve_identifier(url_or_id: str) -> dict:
    """Classify input into {'arxiv': id, 'version': vN} | {'doi': ...} | {'url': ..., 'pdf': bool}."""
    s = url_or_id.strip()
    if re.search(r"arxiv\.org/(abs|pdf|html)/", s, re.I) or s.lower().startswith("arxiv:"):
        base, ver = normalize_arxiv_id(s)
        return {"arxiv": base, "version": ver}
    if _ARXIV_NEW.match(s) or _ARXIV_OLD.match(s):
        base, ver = normalize_arxiv_id(s)
        return {"arxiv": base, "version": ver}
    if re.search(r"(doi\.org/|^doi:|^10\.\d{4,9}/)", s, re.I):
        return {"doi": normalize_doi(s)}
    if s.startswith(("http://", "https://")):
        return {"url": s, "pdf": s.lower().split("?")[0].endswith(".pdf")}
    raise ValueError(f"Unrecognized paper identifier: {url_or_id!r}")


def _find_work(ctx: RunContext, key: str, value: str) -> Work | None:
    rows = ctx.store.all(Work, f"json_extract(json, '$.ids.{key}') = ?", (value,))
    return rows[-1] if rows else None


def _arxiv_metadata(aid: str) -> dict:
    text = http.get_text("https://export.arxiv.org/api/query", params={"id_list": aid}, limiter=LIMITER)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = ET.fromstring(text).find("a:entry", ns)
    if entry is None:
        return {}
    return {
        "title": " ".join((entry.findtext("a:title", "", ns) or "").split()),
        "abstract": " ".join((entry.findtext("a:summary", "", ns) or "").split()),
        "authors": [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)],
        "released_date": (entry.findtext("a:published", "", ns) or "")[:10] or None,
    }


def _crossref_metadata(doi: str) -> dict:
    data = http.get_json(f"https://api.crossref.org/works/{doi}", limiter=LIMITER)
    m = (data or {}).get("message", {}) if isinstance(data, dict) else {}
    pdf = next((ln["URL"] for ln in m.get("link", []) if ln.get("content-type") == "application/pdf"), None)
    abstract = re.sub(r"<[^>]+>", " ", m.get("abstract") or "")
    return {
        "title": " ".join((m.get("title") or [""])[0].split()),
        "abstract": " ".join(abstract.split()) or None,
        "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in m.get("author", [])],
        "venue": (m.get("container-title") or [None])[0],
        "pdf": pdf,
        "landing": m.get("URL"),
    }


def resolve_work(ctx: RunContext, url_or_id: str) -> Work:
    """Find the work in the catalog, or create a minimal one (registered via lark.catalog if available)."""
    ident = resolve_identifier(url_or_id)
    if aid := ident.get("arxiv"):
        if w := _find_work(ctx, "arxiv", aid):
            return w
        meta = {} if ctx.offline else _safe(_arxiv_metadata, aid)
        rec = SourceRecord(
            source="deep_read", source_id=f"arxiv:{aid}", title=meta.get("title") or f"arXiv:{aid}",
            authors=meta.get("authors", []), abstract=meta.get("abstract"), ids={"arxiv": aid}, venue="arXiv",
            status="preprint", url=f"https://arxiv.org/abs/{aid}", released_date=meta.get("released_date"),
            version=ident.get("version"),
        )
    elif doi := ident.get("doi"):
        if w := _find_work(ctx, "doi", doi):
            return w
        meta = {} if ctx.offline else _safe(_crossref_metadata, doi)
        rec = SourceRecord(
            source="deep_read", source_id=f"doi:{doi}", title=meta.get("title") or f"doi:{doi}",
            authors=meta.get("authors", []), abstract=meta.get("abstract"), ids={"doi": doi},
            venue=meta.get("venue"), status="published", url=meta.get("landing") or f"https://doi.org/{doi}",
            pdf_url=meta.get("pdf"),
        )
    else:
        url = ident["url"]
        rows = ctx.store.all(Work, "json_extract(json, '$.urls.landing') = ? OR json_extract(json, '$.urls.pdf') = ?", (url, url))
        if rows:
            return rows[-1]
        rec = SourceRecord(
            source="deep_read", source_id=f"url:{short_hash(url)}", title=url, url=url,
            pdf_url=url if ident["pdf"] else None, html_url=None if ident["pdf"] else url,
        )
    return _register(ctx, rec)


def _safe(fn, arg) -> dict:
    try:
        return fn(arg) or {}
    except Exception as e:  # noqa: BLE001 - metadata is best effort
        log.warning("metadata lookup failed for %s: %s", arg, e)
        return {}


def _register(ctx: RunContext, rec: SourceRecord) -> Work:
    try:  # use the catalog's identity resolution so the deep read joins any existing record
        from lark import catalog

        ctx.store.append(rec)
        catalog.resolve(ctx, [rec])
        if rec.work_id and (w := ctx.store.get(Work, rec.work_id)):
            return w
    except Exception as e:  # noqa: BLE001
        log.warning("catalog resolution failed for deep read: %s", e)
    ids = rec.ids
    urls = {k: v for k, v in {"landing": rec.url, "pdf": rec.pdf_url, "html": rec.html_url}.items() if v}
    wid = "w_" + short_hash(*(f"{k}:{v}" for k, v in sorted(ids.items())) or (rec.source_id,))
    work = Work(
        work_id=wid, title=rec.title, authors=rec.authors, abstract=rec.abstract, ids=ids, venue=rec.venue,
        status=rec.status, urls=urls, released_date=rec.released_date,
        source_keys=[f"{rec.source}|{rec.source_id}"], current_version=f"arxiv:{rec.version}" if rec.version and "arxiv" in ids else None,
    )
    rec.work_id = wid
    ctx.store.append_many([rec, work])
    return work


def _chunks(sections, budget_tokens: int):
    chunk, used = [], 0
    for s in sections:
        t = est_tokens(s.text)
        if chunk and used + t > budget_tokens:
            yield chunk
            chunk, used = [], 0
        chunk.append(s)
        used += t
    if chunk:
        yield chunk


REDUCE_INSTRUCTIONS = """You are merging partial readings of ONE paper, each produced from a different part of its text,
into a single reading with the same JSON schema. Rules:
- Keep claims' evidence quotes and locations exactly as given; never invent or edit a quote.
- Remove duplicate claims; keep the most precise version and its evidence.
- Reconcile question/obstacle/idea/mechanism/main_result across parts; the formal results sections take
  precedence over the introduction when they disagree.
- Keep every condition the main result depends on; drop generic caveats.
- Keep `escalate` questions only if they could change a research decision.
Return one JSON object matching the schema and nothing else."""


def deep_read(ctx: RunContext, url_or_id: str, depth: str = "full", question: str = "", role: str = "reader") -> ReadingNote:
    if depth not in ("full", "technical"):
        raise ValueError("depth must be 'full' or 'technical'")
    work = resolve_work(ctx, url_or_id)
    parsed = get_parsed(ctx, work)
    if parsed is None:
        raise ReadFailed(f"Could not obtain full text for {url_or_id}; supply a PDF URL or a local copy.")
    max_pages = int(ctx.config.get("limits.deep_read_max_pages", 80))
    sections = all_sections(parsed, max_pages)
    if work.abstract and not any(s.title.lower().startswith("abstract") for s in sections):
        from lark.read.parse import Section

        sections = [Section("Abstract", work.abstract), *sections]
    profiles = relevant_profiles(ctx, latest_screening(ctx, work.work_id)) or ctx.config.profiles
    request = f"Deep read requested ({depth})." + (f" Focus question: {question}" if question else "")

    chunks = list(_chunks(sections, FULL_BUDGET_TOKENS))
    partials: list[dict] = []
    last_res = None
    for i, chunk in enumerate(chunks, 1):
        note = f"Part {i} of {len(chunks)} of the paper's text." if len(chunks) > 1 else ""
        data, last_res = call_reader(
            ctx, work, depth, Material(chunk, True, note), profiles, request=request, role=role
        )
        partials.append(data)

    if len(partials) == 1:
        merged = partials[0]
    else:
        from lark.llm.base import LLMRequest

        version, system = load_prompt(depth)
        req = LLMRequest(
            role=role, system=system + "\n\n" + REDUCE_INSTRUCTIONS,
            prompt=f"Paper: {work.title}\n\nPartial readings (JSON, in text order):\n"
            + json.dumps(partials, ensure_ascii=False, indent=1),
            schema=READING_SCHEMA, max_output_tokens=8000, timeout_s=900, prompt_version=version + "+reduce",
        )
        res = _llm.call(ctx, role, req)
        if not res.ok or not isinstance(res.data, dict):
            raise ReadFailed(f"reduce step failed: {res.error}")
        merged, last_res = res.data, res

    material = Material(sections, True)
    note = note_from_reply(merged, work, depth, material, version_for(work, parsed), parsed.source_url)
    note.llm = getattr(last_res, "call", None)
    verify_note(note, parsed, extra_text=work.abstract or "")
    ctx.store.append(note)
    return note
