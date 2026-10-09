"""Delivery channels. Each adapts the Report contract to one medium."""

from __future__ import annotations

import logging

from lark.context import RunContext
from lark.records import Delivery
from lark.report.channels.archive import ArchiveChannel
from lark.report.channels.base import Channel, DeliveryResult
from lark.report.channels.discord import DiscordChannel
from lark.report.channels.email_smtp import EmailChannel
from lark.report.channels.notion import NotionChannel
from lark.report.channels.ntfy import NtfyChannel
from lark.report.channels.slack import SlackChannel
from lark.report.channels.telegram import TelegramChannel
from lark.report.contract import Report

log = logging.getLogger(__name__)

REGISTRY: dict[str, type] = {
    "email": EmailChannel,
    "archive": ArchiveChannel,
    "slack": SlackChannel,
    "notion": NotionChannel,
    "telegram": TelegramChannel,
    "discord": DiscordChannel,
    "ntfy": NtfyChannel,
}


def get_channels(ctx: RunContext) -> list[Channel]:
    out: list[Channel] = []
    for name in ctx.config.get("delivery.channels", ["email", "archive"]) or []:
        cls = REGISTRY.get(name)
        if cls is None:
            log.warning("Unknown delivery channel %r; known: %s", name, ", ".join(REGISTRY))
            continue
        out.append(cls())
    return out


def deliver_all(ctx: RunContext, report: Report, channels: list[Channel] | None = None) -> list[DeliveryResult]:
    """Deliver to every configured channel. Idempotent per (report, channel); failures are isolated."""
    results: list[DeliveryResult] = []
    for ch in channels if channels is not None else get_channels(ctx):
        prior = ctx.store.get(Delivery, f"{report.report_id}|{ch.name}")
        if prior and prior.status == "sent":
            results.append(DeliveryResult(ch.name, "skipped", "already sent"))
            continue
        ok, why = ch.configured(ctx)
        if not ok:
            res = DeliveryResult(ch.name, "skipped", f"not configured: {why}")
        else:
            try:
                res = ch.deliver(ctx, report)
            except Exception as e:  # one broken channel must not block the others
                log.warning("Channel %s failed: %s", ch.name, e)
                res = DeliveryResult(ch.name, "failed", f"{type(e).__name__}: {str(e)[:300]}")
        ctx.store.append(Delivery(report_id=report.report_id, channel=ch.name, status=res.status, detail=res.detail))
        results.append(res)
    return results


__all__ = ["REGISTRY", "Channel", "DeliveryResult", "deliver_all", "get_channels"]
