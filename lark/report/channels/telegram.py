"""Telegram bot (free). Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Plain text, chunked under 4096 chars."""

from __future__ import annotations

import os

from lark.context import RunContext
from lark.report.channels._common import chunk, post, short_text
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report

LIMIT = 4000


class TelegramChannel:
    name = "telegram"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        ok = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        return ok, "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID"

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        n = int(ctx.config.get("delivery.telegram.max_items", 8))
        parts = chunk(short_text(report, n, bold=""), LIMIT)
        if ctx.dry_run:
            return DeliveryResult(self.name, "skipped", f"dry run: {len(parts)} message(s)")
        url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
        for p in parts:
            post(url, json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": p, "disable_web_page_preview": True})
        return DeliveryResult(self.name, "sent", f"{len(parts)} message(s)")
