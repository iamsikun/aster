"""End-to-end: template data dir + offline fixtures + fake LLM → validated report and a dry-run email."""

import email
import json
import shutil
from pathlib import Path

import jsonschema
import pytest

from lark.context import make_context
from lark.report.contract import json_schema

ROOT = Path(__file__).parents[1]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "lark-data"
    shutil.copytree(ROOT / "templates" / "data-repo", d)
    monkeypatch.setenv("LARK_WRITER", "test")
    monkeypatch.setenv("LARK_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("LARK_SMTP_PASSWORD", "x")
    monkeypatch.setenv("LARK_EMAIL_TO", "me@example.com")
    for var in ("GITHUB_TOKEN", "GEMINI_API_KEY", "LARK_GROBID_URL", "LARK_IMAP_USER"):
        monkeypatch.delenv(var, raising=False)
    return d


def test_daily_end_to_end(data_dir, monkeypatch):
    from lark.pipelines.daily import run_daily

    ctx = make_context(data_dir, dry_run=True, fake_llm=True, offline=True)
    report = run_daily(ctx)
    jsonschema.validate(report.model_dump(mode="json"), json_schema())
    assert report.stats.collected > 0
    assert {s.source for s in report.sources} >= {"arxiv", "crossref"}

    emls = list((data_dir / "out").rglob("*.eml"))
    assert emls, "dry-run email not written"
    msg = email.message_from_bytes(emls[0].read_bytes())
    assert msg["Subject"].startswith("[Lark]")
    archived = list((data_dir / "briefings").rglob("*.json"))
    assert archived and json.loads(archived[0].read_text())["report_id"] == report.report_id

    # A second run on the same day is idempotent: no duplicate deliveries.
    report2 = run_daily(make_context(data_dir, dry_run=True, fake_llm=True, offline=True))
    assert len(list((data_dir / "out").rglob("*.eml"))) == len(emls)
    assert report2.report_id == report.report_id


def test_state_rebuild_matches(data_dir):
    from lark.pipelines.daily import run_daily
    from lark.records import Work

    ctx = make_context(data_dir, dry_run=True, fake_llm=True, offline=True)
    run_daily(ctx, skip_deliver=True)
    n = ctx.store.count(Work)
    ctx.store.close()
    assert make_context(data_dir, rebuild=True).store.count(Work) == n
