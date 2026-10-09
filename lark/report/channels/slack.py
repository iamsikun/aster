"""Slack incoming webhook (free workspace). Env: SLACK_WEBHOOK_URL."""

from __future__ import annotations

import os

from lark.context import RunContext
from lark.report.channels._common import chunk, post, short_text
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report

LIMIT = 3500  # stay well under Slack's per-message text limit


class SlackChannel:
    name = "slack"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        return (bool(os.getenv("SLACK_WEBHOOK_URL")), "SLACK_WEBHOOK_URL")

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        n = int(ctx.config.get("delivery.slack.max_items", 8))
        text = short_text(report, n, bold="*", link_fmt="<{url}|{label}>")
        parts = chunk(text, LIMIT)
        if ctx.dry_run:
            return DeliveryResult(self.name, "skipped", f"dry run: {len(parts)} message(s)")
        for p in parts:
            post(os.environ["SLACK_WEBHOOK_URL"], json={"text": p})
        return DeliveryResult(self.name, "sent", f"{len(parts)} message(s)")
