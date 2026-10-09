import email
import json
from email import policy
from pathlib import Path

import pytest

from lark.records import Delivery
from lark.report.channels import REGISTRY, deliver_all, get_channels
from lark.report.channels._common import chunk, short_text
from lark.report.channels.base import DeliveryResult
from lark.report.channels.email_smtp import EmailChannel
from lark.report.contract import Report

EXAMPLE = Path(__file__).parents[1] / "docs" / "report-contract" / "example.json"


@pytest.fixture
def report() -> Report:
    return Report.model_validate(json.loads(EXAMPLE.read_text()))


def test_email_dry_run_writes_valid_eml(ctx, report, monkeypatch):
    monkeypatch.setenv("LARK_SMTP_USER", "bot@gmail.com")
    monkeypatch.setenv("LARK_EMAIL_TO", "a@x.edu, b@y.edu")
    res = EmailChannel().deliver(ctx, report)
    assert res.status == "skipped" and "dry run" in res.detail
    path = ctx.data_dir / "out" / f"{report.report_id}.eml"
    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    assert msg["Subject"] == f"[Lark] {report.title}"
    assert msg["To"] == "a@x.edu, b@y.edu"
    assert msg["Message-ID"] == f"<{report.report_id}@lark.briefing>"
    assert msg.get_content_type() == "multipart/mixed"
    body = msg.get_body(preferencelist=("html",))
    assert body is not None and "Lark" in body.get_content()
    assert "Why now" in msg.get_body(preferencelist=("plain",)).get_content()
    atts = list(msg.iter_attachments())
    assert len(atts) == 1 and atts[0].get_filename() == f"{report.report_id}.json"
    assert Report.model_validate(json.loads(atts[0].get_content())) == report


def test_archive_writes_md_and_json(ctx, report):
    res = REGISTRY["archive"]().deliver(ctx, report)
    assert res.status == "sent"
    folder = ctx.data_dir / "briefings" / "2026"
    assert (folder / f"{report.report_id}.md").read_text().startswith("# Lark daily briefing")
    assert json.loads((folder / f"{report.report_id}.json").read_text())["report_id"] == report.report_id


class _Ok:
    name = "ok"
    calls = 0

    def configured(self, ctx):
        return True, "ok"

    def deliver(self, ctx, report):
        type(self).calls += 1
        return DeliveryResult(self.name, "sent", "fine")


class _Boom:
    name = "boom"

    def configured(self, ctx):
        return True, "ok"

    def deliver(self, ctx, report):
        raise RuntimeError("smtp down")


class _Unconfigured:
    name = "nope"

    def configured(self, ctx):
        return False, "MISSING_TOKEN"

    def deliver(self, ctx, report):  # pragma: no cover
        raise AssertionError("should not be called")


def test_deliver_all_isolates_failures_and_is_idempotent(ctx, report):
    _Ok.calls = 0
    chans = [_Boom(), _Ok(), _Unconfigured()]
    first = {r.channel: r.status for r in deliver_all(ctx, report, chans)}
    assert first == {"boom": "failed", "ok": "sent", "nope": "skipped"}
    assert ctx.store.get(Delivery, f"{report.report_id}|boom").status == "failed"

    second = {r.channel: (r.status, r.detail) for r in deliver_all(ctx, report, chans)}
    assert second["ok"] == ("skipped", "already sent") and _Ok.calls == 1
    assert second["boom"][0] == "failed"  # failed channels are retried


def test_get_channels_from_config(ctx):
    ctx.config.data["delivery"]["channels"] = ["email", "archive", "bogus"]
    assert [c.name for c in get_channels(ctx)] == ["email", "archive"]


def test_dry_run_chat_channels_do_not_post(ctx, report, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("posted")))
    for name, env in [("slack", {"SLACK_WEBHOOK_URL": "https://h"}), ("discord", {"DISCORD_WEBHOOK_URL": "https://h"}),
                      ("telegram", {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}), ("ntfy", {"NTFY_TOPIC": "x"}),
                      ("notion", {"NOTION_API_KEY": "k", "NOTION_DATABASE_ID": "d"})]:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        ch = REGISTRY[name]()
        assert ch.configured(ctx)[0]
        assert ch.deliver(ctx, report).status == "skipped"


def test_live_discord_chunks_under_limit(ctx, report, monkeypatch):
    sent = []

    class R:
        status_code = 204
        text = ""

    monkeypatch.setattr("requests.post", lambda url, **k: sent.append(k["json"]["content"]) or R())
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    ctx.dry_run = False
    report.items = report.items * 12
    for i, it in enumerate(report.items):
        report.items[i] = it.model_copy(update={"item_id": f"x{i}"})
    res = REGISTRY["discord"]().deliver(ctx, report)
    assert res.status == "sent" and len(sent) > 1 and all(len(s) <= 2000 for s in sent)


def test_chunk_respects_limit():
    text = "\n\n".join(["a" * 50] * 30 + ["b" * 300])
    parts = chunk(text, 120)
    assert all(len(p) <= 120 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_short_text_keeps_labels(report):
    s = short_text(report)
    assert "Lark's inference" in s and "Focused read" in s and "nber: stale" in s
