"""Notion (free plan): one page per report in a database, items as blocks.

Env: NOTION_API_KEY, NOTION_DATABASE_ID. The database needs a title property named
"Name" (Notion's default). One page per report is simpler and more robust than a row
per item: a failed send never leaves a half-written set of rows.
"""

from __future__ import annotations

import os

from lark.context import RunContext
from lark.report.channels._common import post
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report
from lark.report.renderers import CLAIM_KIND_LABELS, DEPTH_LABELS

API = "https://api.notion.com/v1/pages"
VERSION = "2022-06-28"
MAX_BLOCKS = 100  # per create request
MAX_TEXT = 1900  # rich_text content limit is 2000


def _rt(text: str, url: str | None = None) -> list[dict]:
    t: dict = {"content": text[:MAX_TEXT]}
    if url:
        t["link"] = {"url": url}
    return [{"type": "text", "text": t}]


def _block(kind: str, text: str, url: str | None = None) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": _rt(text, url)}}


def blocks(report: Report) -> list[dict]:
    out = [_block("paragraph", report.summary)]
    if not report.items and report.quiet_day_reason:
        out.append(_block("callout", f"Quiet day: {report.quiet_day_reason}"))
    for n in report.notes:
        out.append(_block("callout", n))
    for i, it in enumerate(report.items, 1):
        w = it.work
        out.append(_block("heading_2", f"{i}. {w.title}", w.links[0].url if w.links else None))
        meta = f"{DEPTH_LABELS[it.reading_depth]} · priority {it.priority} · {', '.join(it.profiles)}"
        out.append(_block("paragraph", meta))
        out.append(_block("paragraph", f"Why now: {it.why_now}"))
        c = it.contribution
        for label, val in (("Question", c.question), ("Idea", c.idea), ("Main result", c.main_result)):
            if val:
                out.append(_block("bulleted_list_item", f"{label}: {val}"))
        for cl in it.claims:
            out.append(_block("bulleted_list_item", f"[{CLAIM_KIND_LABELS[cl.kind]}] {cl.text}"))
            for ev in cl.evidence:
                mark = "verified" if ev.verified else "unverified"
                out.append(_block("quote", f"“{ev.quote}” — {ev.location or 'n/a'} ({mark})"))
        if it.connection:
            out.append(_block("callout", f"Lark's inference ({it.connection.profile}): {it.connection.text}"))
    if report.also_noted:
        out.append(_block("heading_3", "Also noted"))
        out += [_block("bulleted_list_item", w.title, w.links[0].url if w.links else None) for w in report.also_noted]
    return out[:MAX_BLOCKS]


class NotionChannel:
    name = "notion"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        ok = bool(os.getenv("NOTION_API_KEY") and os.getenv("NOTION_DATABASE_ID"))
        return ok, "NOTION_API_KEY, NOTION_DATABASE_ID"

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        payload = {
            "parent": {"database_id": os.getenv("NOTION_DATABASE_ID", "")},
            "properties": {"Name": {"title": _rt(report.title)}},
            "children": blocks(report),
        }
        if ctx.dry_run:
            return DeliveryResult(self.name, "skipped", f"dry run: {len(payload['children'])} blocks")
        headers = {"Authorization": f"Bearer {os.environ['NOTION_API_KEY']}", "Notion-Version": VERSION}
        post(API, json=payload, headers=headers, timeout=30)
        return DeliveryResult(self.name, "sent", f"{len(payload['children'])} blocks")
