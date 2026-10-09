"""Read selected works at a stated depth and record evidence-anchored notes."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lark.context import RunContext
from lark.jobs import JobQueue
from lark.llm.base import LLMRequest
from lark.profiles import Profile
from lark.read import _llm
from lark.read.parse import Parsed, Section, get_parsed
from lark.read.verify import verify_note
from lark.records import Claim, Connection, Evidence, ReadingNote, Screening, Work

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).with_name("prompts")
FOCUSED_BUDGET_TOKENS = 12_000
FULL_BUDGET_TOKENS = 60_000  # per call; longer texts are chunked by lark.read.deep


class ReadFailed(Exception):
    pass


def est_tokens(text: str) -> int:
    return len(text) // 4


# ---------------------------------------------------------------- schema + prompts

_EVIDENCE = {
    "type": "object",
    "properties": {"quote": {"type": "string"}, "location": {"type": ["string", "null"]}},
    "required": ["quote", "location"],
    "additionalProperties": False,
}
_NULLABLE_STR = {"type": ["string", "null"]}
READING_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "question": _NULLABLE_STR,
        "obstacle": _NULLABLE_STR,
        "idea": _NULLABLE_STR,
        "mechanism": _NULLABLE_STR,
        "main_result": _NULLABLE_STR,
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["paper_result", "author_interpretation"]},
                    "evidence": {"type": "array", "items": _EVIDENCE},
                },
                "required": ["text", "kind", "evidence"],
                "additionalProperties": False,
            },
        },
        "conditions": {"type": "array", "items": {"type": "string"}},
        "connection": {
            "type": ["object", "null"],
            "properties": {"profile": {"type": "string"}, "text": {"type": "string"}},
            "required": ["profile", "text"],
            "additionalProperties": False,
        },
        "escalate": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["question", "obstacle", "idea", "mechanism", "main_result", "claims", "conditions", "connection", "escalate"],
    "additionalProperties": False,
}


@cache
def load_prompt(depth: str) -> tuple[str, str]:
    """Return (version, system prompt) for a depth; 'abstract' reuses the focused prompt."""
    name = "focused" if depth == "abstract" else depth
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    core = (PROMPT_DIR / "_core.md").read_text(encoding="utf-8")
    version = name
    m = re.match(r"^---\n(.*?)\n---\n", raw, flags=re.S)
    if m:
        raw = raw[m.end() :]
        if vm := re.search(r"^version:\s*(\S+)", m.group(1), flags=re.M):
            version = vm.group(1)
    return version, raw.replace("{core}", core).strip()


# ---------------------------------------------------------------- section selection

_SKIP_TITLES = re.compile(r"reference|bibliograph|acknowledg|funding|author contributions|competing interest", re.I)
_INTRO = re.compile(r"introduction|overview", re.I)
_ABSTRACT = re.compile(r"^\s*abstract", re.I)
_RESULTS = re.compile(
    r"main result|results?\b|theor|method|model|estimat|identif|inference|experiment|empirical|evaluation|"
    r"analysis|framework|approach|conclusion|discussion",
    re.I,
)
_STOP = set("the a an and or of for to in on with by from is are this that its as at be we our using via".split())


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z\-]{2,}", (text or "").lower()) if w not in _STOP}


def select_sections(
    parsed: Parsed, reason: str = "", budget_tokens: int = FOCUSED_BUDGET_TOKENS, abstract: str | None = None
) -> list[Section]:
    """Pick sections for a focused read within a token budget, returned in document order.

    Priority: abstract, introduction, main-result/method sections, then sections whose text
    overlaps the screening reason. A section that does not fit is truncated if a useful
    amount (>= 2k chars) of budget remains; truncated titles end with " (partial)".
    """
    secs = [s for s in parsed.sections if not _SKIP_TITLES.search(s.title)]
    if abstract and not any(_ABSTRACT.match(s.title) for s in secs):
        secs = [Section("Abstract", abstract, None, 1), *secs]
    reason_terms = _terms(reason)
    scored: list[tuple[float, int, Section]] = []
    for i, s in enumerate(secs):
        score = 0.0
        if _ABSTRACT.match(s.title):
            score += 100
        elif _INTRO.search(s.title):
            score += 50
        elif s.title == "Front matter":
            score -= 5
        if _RESULTS.search(s.title):
            score += 30
        if reason_terms:
            overlap = len(reason_terms & _terms(s.title + " " + s.text[:4000]))
            score += 4 * overlap
        score -= i * 0.01  # earlier first on ties
        scored.append((score, i, s))
    budget = budget_tokens * 4
    chosen: list[tuple[int, Section]] = []
    for _, i, s in sorted(scored, key=lambda t: -t[0]):
        if budget <= 0:
            break
        if len(s.text) <= budget:
            chosen.append((i, s))
            budget -= len(s.text)
        elif budget >= 2000:
            chosen.append((i, Section(s.title + " (partial)", s.text[:budget], s.page_start, s.level)))
            budget = 0
    return [s for _, s in sorted(chosen, key=lambda t: t[0])]


def all_sections(parsed: Parsed, max_pages: int | None = None) -> list[Section]:
    out = [s for s in parsed.sections if not _SKIP_TITLES.search(s.title)]
    if max_pages:
        out = [s for s in out if s.page_start is None or s.page_start <= max_pages]
    return out


# ---------------------------------------------------------------- prompt assembly


@dataclass
class Material:
    sections: list[Section]
    full_text_obtained: bool
    note: str = ""  # e.g. "Part 2 of 3"


def latest_screening(ctx: RunContext, work_id: str) -> Screening | None:
    rows = ctx.store.all(Screening, "json_extract(json, '$.work_id') = ?", (work_id,))
    return rows[-1] if rows else None


def relevant_profiles(ctx: RunContext, screening: Screening | None) -> list[Profile]:
    profiles = ctx.config.profiles
    if screening and screening.topics:
        chosen = [p for p in profiles if p.id in screening.topics]
        if chosen:
            return chosen
    return []


def build_prompt(
    work: Work, material: Material, profiles: list[Profile], reason: str = "", request: str = ""
) -> str:
    meta = [
        f"Title: {work.title}",
        f"Authors: {', '.join(work.authors[:12])}{' et al.' if len(work.authors) > 12 else ''}",
        f"Venue/status: {work.venue or 'unknown'} / {work.status}",
        f"Version: {work.current_version or 'unknown'}",
        f"Identifiers: {', '.join(f'{k}:{v}' for k, v in work.ids.items()) or 'none'}",
    ]
    parts = ["# Work", *meta, ""]
    if profiles:
        parts += ["# Reader's research profiles (for `connection` only)", *[p.brief() for p in profiles], ""]
    else:
        parts += ["# Reader's research profiles", "None supplied: set `connection` to null.", ""]
    if reason:
        parts += ["# Why this was selected (screening reason)", reason, ""]
    if request:
        parts += ["# Request", request, ""]
    listing = ", ".join(s.label for s in material.sections)
    parts += [
        "# Material supplied",
        f"Full text obtained: {'yes' if material.full_text_obtained else 'no (abstract only)'}.",
        f"Sections supplied ({len(material.sections)}): {listing}.",
    ]
    if material.note:
        parts.append(material.note)
    parts += ["Nothing else from the paper was supplied.", "", "# Source text", "<<<SOURCE"]
    for s in material.sections:
        parts += [f"### {s.label}", s.text, ""]
    parts.append("SOURCE>>>")
    return "\n".join(parts)


# ---------------------------------------------------------------- reply -> note


def note_from_reply(
    data: dict, work: Work, depth: str, material: Material, version_id: str | None, source_url: str | None
) -> ReadingNote:
    claims = []
    for c in data.get("claims") or []:
        if not isinstance(c, dict) or not c.get("text"):
            continue
        evidence = [
            Evidence(quote=e["quote"], location=e.get("location"), url=source_url)
            for e in (c.get("evidence") or [])
            if isinstance(e, dict) and e.get("quote")
        ]
        kind = c.get("kind") if c.get("kind") in ("paper_result", "author_interpretation") else "author_interpretation"
        claims.append(Claim(text=c["text"], kind=kind, evidence=evidence))
    conn = data.get("connection")
    connection = (
        Connection(profile=str(conn["profile"]), text=str(conn["text"]))
        if isinstance(conn, dict) and conn.get("profile") and conn.get("text")
        else None
    )
    return ReadingNote(
        work_id=work.work_id,
        version_id=version_id,
        depth=depth,  # type: ignore[arg-type]
        full_text_obtained=material.full_text_obtained,
        sections_read=[s.title for s in material.sections],  # what was supplied, never what the model claims
        question=data.get("question"),
        obstacle=data.get("obstacle"),
        idea=data.get("idea"),
        mechanism=data.get("mechanism"),
        main_result=data.get("main_result"),
        claims=claims,
        conditions=[str(x) for x in (data.get("conditions") or [])],
        connection=connection,
        escalate=[str(x) for x in (data.get("escalate") or [])],
    )


def version_for(work: Work, parsed: Parsed | None) -> str:
    if work.current_version:
        return work.current_version
    return f"text:{parsed.sha256[:12]}" if parsed else "abstract"


def call_reader(
    ctx: RunContext, work: Work, depth: str, material: Material, profiles: list[Profile], *,
    reason: str = "", request: str = "", role: str = "reader", route: str | None = None, job_id: str | None = None,
) -> tuple[dict, object]:
    version, system = load_prompt(depth)
    req = LLMRequest(
        role=role,
        system=system,
        prompt=build_prompt(work, material, profiles, reason, request),
        schema=READING_SCHEMA,
        max_output_tokens=6000 if depth in ("full", "technical") else 3000,
        timeout_s=600 if depth in ("full", "technical") else 300,
        job_id=job_id,
        prompt_version=version,
    )
    res = _llm.call(ctx, role, req, route=route)
    if not res.ok or not isinstance(res.data, dict):
        raise ReadFailed(f"{res.error_kind or 'error'}: {res.error or 'no structured output'}")
    return res.data, res


# ---------------------------------------------------------------- public API


def read_work(
    ctx: RunContext, work_id: str, depth: str = "focused", role: str = "reader", *,
    route: str | None = None, persist: bool = True, job_id: str | None = None,
) -> ReadingNote | None:
    """Read one work. Returns None if the work is unknown; raises ReadFailed on model failure
    and lets NoRouteAvailable propagate so callers can defer."""
    work = ctx.store.get(Work, work_id)
    if work is None:
        return None
    screening = latest_screening(ctx, work_id)
    profiles = relevant_profiles(ctx, screening)
    reason = screening.reason if screening else ""

    parsed = get_parsed(ctx, work) if depth != "abstract" else None
    if parsed is None:
        if not work.abstract:
            raise ReadFailed("no full text and no abstract")
        depth = "abstract"
        material = Material([Section("Abstract", work.abstract)], full_text_obtained=False)
    elif depth == "focused":
        material = Material(select_sections(parsed, reason, FOCUSED_BUDGET_TOKENS, work.abstract), True)
    else:
        secs = select_sections(parsed, reason, FULL_BUDGET_TOKENS, work.abstract)
        material = Material(secs, True)

    data, res = call_reader(ctx, work, depth, material, profiles, reason=reason, role=role, route=route, job_id=job_id)
    note = note_from_reply(data, work, depth, material, version_for(work, parsed), parsed.source_url if parsed else None)
    note.llm = getattr(res, "call", None)
    verify_note(note, parsed, extra_text=work.abstract or "")
    if persist:
        ctx.store.append(note)
    return note


def run_read_jobs(ctx: RunContext, limit: int | None = None, role: str = "reader", tag: str | None = None) -> dict:
    """Drain queued `read` jobs, highest priority first. Over-budget/unavailable → deferred."""
    q = JobQueue(ctx.store)
    limit = limit if limit is not None else int(ctx.config.get("limits.focused_reads_per_day", 20))
    no_route = _llm.no_route_error()
    stats = {"done": 0, "deferred": 0, "failed": 0, "retry": 0, "notes": []}
    for job in q.pending(kind="read", tag=tag, limit=limit):
        job = q.start(job)
        depth = job.payload.get("depth", "focused")
        try:
            note = read_work(ctx, job.work_id or "", depth, role, job_id=job.job_id)
        except no_route as e:
            q.defer(job, f"no route: {e}")
            stats["deferred"] += 1
            # Every later job would hit the same wall: defer them without trying.
            for rest in q.pending(kind="read", tag=tag):
                if rest.job_id != job.job_id and rest.state == "queued":
                    q.defer(rest, "no route available")
                    stats["deferred"] += 1
            break
        except Exception as e:  # noqa: BLE001 - one bad paper must not stop the run
            log.warning("read %s failed: %s", job.work_id, e)
            job = q.fail(job, str(e))
            stats["failed" if job.state == "failed" else "retry"] += 1
            continue
        if note is None:
            job.attempts = job.max_attempts  # permanent: retrying cannot help
            q.fail(job, "unknown work")
            stats["failed"] += 1
            continue
        q.done(job)
        stats["done"] += 1
        stats["notes"].append(note.work_id)
    return stats
