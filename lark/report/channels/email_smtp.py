"""Email via SMTP over SSL (Gmail with an app password by default).

Sends multipart/alternative (text + HTML) with report.json attached. In dry-run mode
the message is written to <data-dir>/out/<report_id>.eml instead of being sent.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from lark.context import RunContext
from lark.report.channels.base import DeliveryResult
from lark.report.contract import Report
from lark.report.renderers import render_html, render_text


def _recipients() -> list[str]:
    return [a.strip() for a in os.getenv("LARK_EMAIL_TO", "").split(",") if a.strip()]


def message_id(report: Report) -> str:
    return f"<{report.report_id}@lark.briefing>"


def build_message(ctx: RunContext, report: Report) -> EmailMessage:
    cfg = ctx.config.get("delivery.email", {}) or {}
    sender = os.getenv("LARK_SMTP_USER") or "lark@localhost"
    to = _recipients() or [sender]
    msg = EmailMessage()
    msg["Subject"] = f"{cfg.get('subject_prefix', '[Lark]')} {report.title}".strip()
    msg["From"] = f"Lark <{sender}>"
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id(report) if report.report_id else make_msgid(domain="lark.briefing")
    msg["X-Lark-Report-Id"] = report.report_id
    msg.set_content(render_text(report))
    msg.add_alternative(render_html(report), subtype="html")
    msg.add_attachment(
        json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False).encode("utf-8"),
        maintype="application",
        subtype="json",
        filename=f"{report.report_id}.json",
    )
    return msg


class EmailChannel:
    name = "email"

    def configured(self, ctx: RunContext) -> tuple[bool, str]:
        if ctx.dry_run:
            return True, "dry run writes .eml"
        missing = [k for k in ("LARK_SMTP_USER", "LARK_SMTP_PASSWORD", "LARK_EMAIL_TO") if not os.getenv(k)]
        return (not missing, "missing " + ", ".join(missing) if missing else "ok")

    def deliver(self, ctx: RunContext, report: Report) -> DeliveryResult:
        msg = build_message(ctx, report)
        if ctx.dry_run:
            out = ctx.data_dir / "out"
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{report.report_id}.eml"
            path.write_bytes(bytes(msg))
            return DeliveryResult(self.name, "skipped", f"dry run: wrote {path}")
        cfg = ctx.config.get("delivery.email", {}) or {}
        host, port = cfg.get("smtp_host", "smtp.gmail.com"), int(cfg.get("smtp_port", 465))
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=60) as s:
            s.login(os.environ["LARK_SMTP_USER"], os.environ["LARK_SMTP_PASSWORD"])
            s.send_message(msg)
        return DeliveryResult(self.name, "sent", f"to {len(_recipients())} recipient(s)")
