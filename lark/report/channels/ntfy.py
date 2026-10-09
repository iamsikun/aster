"""ntfy push notification (free, no account). Env: NTFY_TOPIC; optional NTFY_SERVER, NTFY_TOKEN.

Sends a short summary; `delivery.ntfy.click_url` (e.g. the archive folder on GitHub)
becomes the tap target. Pick an unguessable topic name: public ntfy.sh topics are readable
by anyone who knows them.
"""

from __future__ import annotations

import os

from lark.context import RunContext
from lark.report.channels._common import post
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report

LIMIT = 3800  # ntfy message body limit is 4096 bytes


def summary(report: Report) -> str:
    lines = [report.summary]
    if not report.items and report.quiet_day_reason:
        lines.append(f"Quiet day: {report.quiet_day_reason}")
    lines += [f"• {it.work.title} [{it.priority}]" for it in report.items]
    text = "\n".join(lines)
    return text if len(text.encode()) <= LIMIT else text.encode()[:LIMIT].decode(errors="ignore") + "…"


class NtfyChannel:
    name = "ntfy"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        return (bool(os.getenv("NTFY_TOPIC")), "NTFY_TOPIC")

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        if ctx.dry_run:
            return DeliveryResult(self.name, "skipped", "dry run")
        server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        # JSON publishing (POST to the server root) keeps UTF-8 titles intact, unlike HTTP headers.
        body = {"topic": os.environ["NTFY_TOPIC"], "title": report.title, "message": summary(report), "tags": ["bird"]}
        if click := ctx.config.get("delivery.ntfy.click_url"):
            body["click"] = click
        headers = {"Authorization": f"Bearer {os.environ['NTFY_TOKEN']}"} if os.getenv("NTFY_TOKEN") else {}
        post(server, json=body, headers=headers)
        return DeliveryResult(self.name, "sent", "1 notification")
