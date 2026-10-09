import json
import re
from pathlib import Path

import pytest

from lark.report import render
from lark.report.contract import Report

EXAMPLE = Path(__file__).parents[1] / "docs" / "report-contract" / "example.json"


@pytest.fixture
def example() -> Report:
    r = Report.model_validate(json.loads(EXAMPLE.read_text()))
    # Make one quote unverified so the unverified mark is exercised.
    r.items[0].claims[1].evidence[0].verified = None
    return r


@pytest.fixture
def quiet() -> Report:
    return Report(
        report_id="daily-2026-10-10", kind="daily", title="Lark daily briefing — Sat 10 Oct 2026",
        generated_at="2026-10-10T11:40:00+00:00", period_start="2026-10-09", period_end="2026-10-10",
        summary="Nothing cleared the bar today.", quiet_day_reason="No candidate matched an active profile.",
    )


@pytest.mark.parametrize("fmt", ["markdown", "html", "text"])
def test_item_elements_present(example, fmt):
    out = render(example, fmt)
    assert "Bootstrap Inference for the Value of the Selected Arm" in out
    assert re.search("focused read", out, re.I), "reading-depth badge"
    assert "Lark's inference" in out
    assert "verified" in out and "unverified" in out
    assert "Paper result" in out and "Authors" in out
    assert "issues/new?title=feedback" in out
    assert "nber" in out and "stale" in out.lower()
    assert "A Survey of Off-Policy Evaluation" in out


@pytest.mark.parametrize("fmt", ["markdown", "html", "text"])
def test_quiet_day(quiet, fmt):
    out = render(quiet, fmt)
    assert "Quiet day" in out and "No candidate matched an active profile." in out


def test_html_is_email_safe(example):
    html = render(example, "html")
    assert "<script" not in html.lower()
    assert "<link" not in html.lower() and "<img" not in html.lower()
    assert "max-width:680px" in html
    # The only external URLs are hyperlinks, never loaded resources.
    urls = re.findall(r"""(?:src|href|url)\s*[=(]\s*["']?(https?://[^"') ]+)""", html)
    hrefs = re.findall(r'href="(https?://[^"]+)"', html)
    assert urls and set(urls) == set(hrefs)


def test_html_escapes_content(example):
    example.items[0].why_now = "<b>bold</b> & <script>alert(1)</script>"
    html = render(example, "html")
    assert "<script>alert" not in html and "&lt;script&gt;" in html
