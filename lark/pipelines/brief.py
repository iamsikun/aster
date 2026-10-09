"""Assemble a Report (the delivery contract) from stored records.

Selection is deterministic and evidence-first: the composer model only writes the
summary line and per-item `why_now`; it never adds claims. Items come from today's
readings (focused or deeper) and high-priority abstract-only screenings; works
already briefed are skipped unless a substantive new version appeared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from lark.context import RunContext
from lark.records import Analysis, Briefed, ReadingNote, Screening, Version, Work
from lark.report import contract as C
from lark.utils.timez import iso_now, now_utc

log = logging.getLogger(__name__)

PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}
DEPTH_RANK = {"technical": 4, "full": 3, "focused": 2, "abstract": 1}


@dataclass
class Selected:
    work: Work
    screening: Screening | None
    note: ReadingNote | None
    analyses: list[Analysis] = field(default_factory=list)
    update_of: str | None = None
    update_note: str | None = None

    @property
    def priority(self) -> str:
        return self.screening.priority if self.screening else "medium"

    def sort_key(self) -> tuple:
        depth = DEPTH_RANK.get(self.note.depth, 0) if self.note else 0
        verified = (self.note.verification or {}).get("n_verified", 0) if self.note else 0
        return (PRIORITY_RANK.get(self.priority, 0), depth, verified, self.work.work_id)


def _links(work: Work) -> list[C.Link]:
    labels = {"landing": "Link", "html": "HTML", "pdf": "PDF", "code": "Code", "model": "Model"}
    out = []
    if aid := work.ids.get("arxiv"):
        out.append(C.Link(label="arXiv", url=f"https://arxiv.org/abs/{aid}"))
    if doi := work.ids.get("doi"):
        out.append(C.Link(label="DOI", url=f"https://doi.org/{doi}"))
    for k, url in work.urls.items():
        if url and all(url != link.url for link in out):
            out.append(C.Link(label=labels.get(k, k.title()), url=url))
    return out


def work_ref(work: Work) -> C.WorkRef:
    return C.WorkRef(
        work_id=work.work_id,
        title=work.title,
        authors=work.authors[:12],
        venue=work.venue,
        status=work.status,
        kind=work.kind,
        released_date=work.released_date,
        ids=work.ids,
        links=_links(work),
    )


def _claims(note: ReadingNote) -> list[C.Claim]:
    return [
        C.Claim(
            text=c.text,
            kind=c.kind,
            evidence=[C.EvidenceAnchor(quote=e.quote, location=e.location, url=e.url, verified=e.verified) for e in c.evidence],
        )
        for c in note.claims
    ]


def latest_by_work(items: list, ranker) -> dict[str, object]:
    out: dict[str, object] = {}
    for it in items:
        cur = out.get(it.work_id)
        if cur is None or ranker(it) >= ranker(cur):
            out[it.work_id] = it
    return out


def select_items(ctx: RunContext, since: str, limit: int) -> tuple[list[Selected], list[Work]]:
    store = ctx.store
    pv = ctx.config.profiles_version
    screenings = latest_by_work(
        [s for s in store.since(Screening, since) if s.profiles_version == pv], lambda s: s.at
    )
    notes = latest_by_work(store.since(ReadingNote, since), lambda n: (DEPTH_RANK.get(n.depth, 0), n.at))
    analyses: dict[str, list[Analysis]] = {}
    for a in store.since(Analysis, since):
        analyses.setdefault(a.work_id, []).append(a)

    briefed: dict[str, Briefed] = {}
    for b in store.all(Briefed):
        if b.work_id not in briefed or b.at > briefed[b.work_id].at:
            briefed[b.work_id] = b

    candidates: list[Selected] = []
    also: list[Work] = []
    for wid in set(screenings) | set(notes):
        work = store.get(Work, wid)
        if work is None:
            continue
        s = screenings.get(wid)
        n = notes.get(wid)
        sel = Selected(work=work, screening=s, note=n, analyses=analyses.get(wid, []))
        if prev := briefed.get(wid):
            v = store.get(Version, f"{wid}|{work.current_version}") if work.current_version else None
            if not v or v.change not in ("substantive", "withdrawn") or v.version_id == prev.version_id:
                continue
            sel.update_of = prev.report_id
            sel.update_note = f"New version ({v.version_id}): {v.change} change" + (f" — {v.note}" if v.note else "")
        has_reading = n is not None and n.depth != "abstract"
        if has_reading or (s is not None and s.priority == "high"):
            candidates.append(sel)
        elif s is not None and s.priority == "medium":
            also.append(work)
    candidates.sort(key=Selected.sort_key, reverse=True)
    return candidates[:limit], also[:10]


def _profile_names(ctx: RunContext) -> dict[str, str]:
    return {p.id: p.name for p in ctx.config.profiles}


def _compose(ctx: RunContext, kind: str, picked: list[Selected]) -> tuple[str, dict[str, str]]:
    """Ask the composer model for a summary and one `why_now` line per item. Falls back to templates."""
    fallback_why = {
        s.work.work_id: (s.screening.reason if s.screening and s.screening.reason else "Matched your research profiles.")
        for s in picked
    }
    if not picked:
        return "Nothing cleared the bar today.", {}
    fallback_summary = f"{len(picked)} item{'s' if len(picked) != 1 else ''} worth your attention."
    try:
        from lark.llm import roles
        from lark.llm.base import LLMRequest
    except ImportError:
        return fallback_summary, fallback_why

    names = _profile_names(ctx)
    lines = []
    for s in picked:
        n = s.note
        lines.append(
            f"- id={s.work.work_id} | {s.work.title} | profiles={[names.get(t, t) for t in (s.screening.topics if s.screening else [])]}"
            f" | screening_reason={s.screening.reason if s.screening else ''}"
            f" | main_result={(n.main_result if n else '') or ''} | connection={(n.connection.text if n and n.connection else '')}"
        )
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "why_now": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
            },
        },
        "required": ["summary", "why_now"],
    }
    req = LLMRequest(
        role="composer",
        system=(
            "You write the framing lines of a researcher's daily literature briefing. Use only the facts given. "
            "Do not add claims about papers. Plain, specific language; no hype."
        ),
        prompt=(
            f"Briefing kind: {kind}. Items:\n" + "\n".join(lines) + "\n\n"
            "Return JSON: `summary` (1-2 sentences naming what's notable today) and, for each id, `why_now` "
            "(one sentence, at most 30 words, on why it deserves attention now, grounded in the reason/result/connection)."
        ),
        schema=schema,
        max_output_tokens=1200,
        prompt_version="compose-1",
    )
    try:
        res = roles.call(ctx, "composer", req)
    except Exception as e:  # NoRouteAvailable or anything else: degrade, never block delivery
        log.warning("composer unavailable, using templates: %s", e)
        return fallback_summary, fallback_why
    if not res.ok or not res.data:
        return fallback_summary, fallback_why
    why = dict(fallback_why)
    for w in res.data.get("why_now", []):
        if isinstance(w, dict) and w.get("id") in why and w.get("text"):
            why[w["id"]] = w["text"]
    return res.data.get("summary") or fallback_summary, why


def _item(ctx: RunContext, report_id: str, i: int, s: Selected, why_now: str) -> C.ReportItem:
    n = s.note
    topics = s.screening.topics if s.screening else ([n.connection.profile] if n and n.connection else [])
    priority = s.priority if s.priority in ("high", "medium", "low") else "medium"
    analysis = None
    if s.analyses:
        analysis = "\n\n".join(f"Q: {a.question}\nA: {a.answer}" for a in s.analyses)
    try:
        from lark.feedback.issues import feedback_actions

        actions = feedback_actions(ctx, s.work.work_id, report_id, topics)
    except ImportError:
        actions = []
    return C.ReportItem(
        item_id=f"{report_id}#{i}",
        work=work_ref(s.work),
        profiles=topics,
        priority=priority,
        reading_depth=n.depth if n else "abstract",
        sections_read=(n.sections_read if n else ["Abstract"]),
        is_update_of=s.update_of,
        update_note=s.update_note,
        why_now=why_now,
        contribution=C.Contribution(
            question=n.question if n else None,
            obstacle=n.obstacle if n else None,
            idea=n.idea if n else None,
            main_result=n.main_result if n else None,
        ),
        claims=_claims(n) if n else [],
        conditions=n.conditions if n else [],
        connection=C.Connection(profile=n.connection.profile, text=n.connection.text) if n and n.connection else None,
        analysis=analysis,
        feedback_actions=actions,
    )


def _footer(ctx: RunContext, health: list, stats: C.Stats) -> tuple[list[C.SourceStatus], C.BudgetSummary, list[str]]:
    sources = [C.SourceStatus(source=h.source, status=h.status, n_records=h.n_records, note=h.error) for h in health]
    notes: list[str] = []
    budget = C.BudgetSummary(monthly_cap_usd=float(ctx.config.get("budget.monthly_cash_usd", 0.0)))
    try:
        from lark import budget as B
        from lark.llm import roles

        summ = B.summary(ctx)
        budget.cash_today_usd = round(summ.get("cash_today_usd", 0.0), 4)
        budget.cash_month_usd = round(summ.get("cash_month_usd", 0.0), 4)
        used = {rt for role in ("screener", "reader", "analyst", "composer") for rt in ctx.config.get(f"roles.{role}.routes", [])}
        for r in roles.route_status(ctx):
            if r["route"] not in used:  # e.g. the local GPU route, which is expected to be offline
                continue
            budget.routes.append(C.RouteStatus(route=r["route"], ok=r["ok"], calls_today=r.get("calls_today", 0), note=r.get("note")))
            if not r["ok"] and r.get("note"):
                notes.append(f"Route {r['route']}: {r['note']}")
    except (ImportError, AttributeError) as e:
        log.debug("budget summary unavailable: %s", e)
    for h in health:
        if h.status == "failed":
            notes.append(f"Source {h.source} failed: {h.error or 'unknown error'}")
    return sources, budget, notes


def build_report(
    ctx: RunContext,
    kind: str = "daily",
    *,
    since: str | None = None,
    health: list | None = None,
    stats: C.Stats | None = None,
    themes: list[C.Theme] | None = None,
    picked: list[Selected] | None = None,
    report_id: str | None = None,
    title: str | None = None,
) -> C.Report:
    now = now_utc()
    days = 7 if kind == "weekly" else 1
    since = since or (now - timedelta(days=days, hours=6)).isoformat(timespec="seconds")
    limit = int(ctx.config.get("limits.briefing_items", 8))
    also: list[Work] = []
    if picked is None:
        picked, also = select_items(ctx, since, limit)
    report_id = report_id or f"{kind}-{ctx.run_date}"
    summary, why = _compose(ctx, kind, picked)
    items = [_item(ctx, report_id, i + 1, s, why.get(s.work.work_id, "")) for i, s in enumerate(picked)]
    stats = stats or C.Stats()
    from lark.jobs import JobQueue

    stats.backlog = JobQueue(ctx.store).backlog_count()
    sources, budget, notes = _footer(ctx, health or [], stats)
    date_label = now.astimezone().strftime("%a %-d %b %Y")
    return C.Report(
        report_id=report_id,
        kind=kind,
        title=title or f"Lark {kind} briefing — {date_label}",
        generated_at=iso_now(),
        period_start=since[:10],
        period_end=ctx.run_date,
        summary=summary,
        quiet_day_reason=None if items else "No new work cleared the relevance and evidence bar in this period.",
        items=items,
        themes=themes or [],
        also_noted=[work_ref(w) for w in also],
        sources=sources,
        budget=budget,
        stats=stats,
        notes=notes,
    )


def record_briefed(ctx: RunContext, report: C.Report) -> None:
    recs = []
    for it in report.items:
        work = ctx.store.get(Work, it.work.work_id)
        recs.append(
            Briefed(work_id=it.work.work_id, report_id=report.report_id, version_id=work.current_version if work else None, depth=it.reading_depth)
        )
    ctx.store.append_many(recs)
