"""Discord webhook (free). Env: DISCORD_WEBHOOK_URL. Chunked under the 2000-char content limit."""

from __future__ import annotations

import os

from lark.context import RunContext
from lark.report.channels._common import chunk, post, short_text
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report

LIMIT = 1900


class DiscordChannel:
    name = "discord"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        return (bool(os.getenv("DISCORD_WEBHOOK_URL")), "DISCORD_WEBHOOK_URL")

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        n = int(ctx.config.get("delivery.discord.max_items", 8))
        parts = chunk(short_text(report, n, bold="**", link_fmt="<{url}>"), LIMIT)
        if ctx.dry_run:
            return DeliveryResult(self.name, "skipped", f"dry run: {len(parts)} message(s)")
        for p in parts:
            post(os.environ["DISCORD_WEBHOOK_URL"], json={"content": p, "allowed_mentions": {"parse": []}})
        return DeliveryResult(self.name, "sent", f"{len(parts)} message(s)")
