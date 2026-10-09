"""Archive channel: markdown + JSON in the data repo (briefings/YYYY/<report_id>.{md,json}).

The archive doubles as an Obsidian/Markdown vault and the source for an RSS feed.
"""

from __future__ import annotations

import json

from lark.context import RunContext
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report
from lark.report.renderers import render_markdown


class ArchiveChannel:
    name = "archive"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        return True, "ok"

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        folder = ctx.data_dir / ctx.config.get("delivery.archive.dir", "briefings") / report.period_end[:4]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{report.report_id}.md").write_text(render_markdown(report), encoding="utf-8")
        (folder / f"{report.report_id}.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return DeliveryResult(self.name, "sent", str(folder / report.report_id))
