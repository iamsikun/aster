"""Weekly synthesis: connect the week's readings into a few themes for the user's research."""

from __future__ import annotations

import logging
from datetime import timedelta

from lark.context import RunContext
from lark.pipelines.brief import DEPTH_RANK, Selected, build_report, latest_by_work
from lark.records import Analysis, Feedback, ReadingNote, Screening, Work
from lark.report import contract as C
from lark.utils.timez import now_utc

log = logging.getLogger(__name__)

THEMES_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "work_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "text", "work_ids"],
            },
        },
    },
    "required": ["summary", "themes"],
}


def _week_items(ctx: RunContext, since: str) -> list[Selected]:
    store = ctx.store
    notes = latest_by_work(
        [n for n in store.since(ReadingNote, since) if n.depth != "abstract"], lambda n: (DEPTH_RANK[n.depth], n.at)
    )
    pv = ctx.config.profiles_version
    useful = {f.work_id for f in store.since(Feedback, since) if f.label in ("useful", "deep_read")}
    out = []
    for wid, note in notes.items():
        work = store.get(Work, wid)
        if not work:
            continue
        out.append(
            Selected(
                work=work,
                screening=store.get(Screening, f"{wid}|{pv}"),
                note=note,
                analyses=[a for a in store.since(Analysis, since) if a.work_id == wid],
            )
        )
    out.sort(key=lambda s: (s.work.work_id in useful, *s.sort_key()), reverse=True)
    return out


def _themes(ctx: RunContext, items: list[Selected]) -> tuple[str | None, list[C.Theme]]:
    if len(items) < 2:
        return None, []
    from lark.llm import roles
    from lark.llm.base import LLMRequest

    profiles = "\n\n".join(p.brief() for p in ctx.config.profiles)
    blocks = []
    for s in items[:25]:
        n = s.note
        claims = "; ".join(c.text for c in n.claims[:4])
        blocks.append(
            f"[{s.work.work_id}] {s.work.title}\nquestion: {n.question}\nidea: {n.idea}\nresult: {n.main_result}\n"
            f"claims: {claims}\nconditions: {'; '.join(n.conditions[:4])}"
        )
    req = LLMRequest(
        role="analyst",
        system=(
            "You write a weekly research synthesis for one researcher. Identify a few substantive developments that "
            "connect several of the supplied papers, useful comparisons, and possible implications for the researcher's "
            "projects. Use only the supplied notes; label implications as your inference. Do not restate each paper."
        ),
        prompt=f"Research profiles:\n{profiles}\n\nThis week's readings:\n\n" + "\n\n".join(blocks)
        + "\n\nReturn JSON with a 2-3 sentence `summary` and 1-4 `themes` (title, 80-200 word text, work_ids).",
        schema=THEMES_SCHEMA,
        max_output_tokens=4000,
        prompt_version="weekly-1",
    )
    try:
        res = roles.call(ctx, "analyst", req)
    except Exception as e:
        log.warning("weekly synthesis unavailable: %s", e)
        return None, []
    if not res.ok or not res.data:
        return None, []
    known = {s.work.work_id for s in items}
    themes = [
        C.Theme(title=t["title"], text=t["text"], work_ids=[w for w in t.get("work_ids", []) if w in known])
        for t in res.data.get("themes", [])
        if isinstance(t, dict) and t.get("title") and t.get("text")
    ]
    return res.data.get("summary"), themes


def run_weekly(ctx: RunContext, *, skip_deliver: bool = False) -> C.Report:
    since = (now_utc() - timedelta(days=7)).isoformat(timespec="seconds")
    items = _week_items(ctx, since)
    summary, themes = _themes(ctx, items)
    limit = int(ctx.config.get("limits.briefing_items", 8))
    report = build_report(ctx, "weekly", since=since, picked=items[:limit], themes=themes)
    if summary:
        report.summary = summary
    if not skip_deliver:
        from lark.pipelines.daily import deliver

        deliver(ctx, report)
    return report
