import json

import pytest
from test_read_reader import Calls

from lark import eval as ev
from lark.read import _llm
from lark.records import Briefed, Feedback, LedgerEntry, ReadingNote, Screening, Work


@pytest.fixture
def catalog(ctx):
    ctx.store.append_many([
        Work(work_id="w1", title="Bootstrap for the Selected Arm", ids={"arxiv": "2610.01234"}, first_seen="2026-10-03T12:00:00+00:00"),
        Work(work_id="w2", title="Ranking Uncertainty", ids={"doi": "10.1234/abc"}, first_seen="2026-10-05T00:00:00+00:00"),
        Work(work_id="w3", title="Unrelated Chemistry", ids={"doi": "10.1234/chem"}),
        Screening(work_id="w1", profiles_version="p", priority="high", next_action="focused_read"),
        Screening(work_id="w2", profiles_version="p", priority="none", next_action="skip"),
        Screening(work_id="w3", profiles_version="p", priority="medium", next_action="abstract_only"),
    ])
    return ctx


def test_screening_recall(catalog, tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text("\n".join(json.dumps(r) for r in [
        {"arxiv": "2610.01234v2", "relevant": True, "profile": "sel"},
        {"title": "ranking uncertainty", "relevant": True, "profile": "sel"},
        {"doi": "10.1234/chem", "relevant": False},
        {"title": "A paper we never collected", "relevant": True},
    ]))
    r = ev.screening_recall(catalog, labels)
    assert r["recall"] == 0.5 and r["precision"] == 0.5
    assert r["screened_out"] == ["Ranking Uncertainty"]
    assert r["not_in_catalog"] == ["A paper we never collected"]
    assert r["per_profile"]["sel"]["recall"] == 0.5


def test_coverage_audit(catalog, tmp_path):
    ref = tmp_path / "ref.jsonl"
    ref.write_text("\n".join(json.dumps(r) for r in [
        {"id": "arxiv:2610.01234", "date": "2026-10-01", "source": "arxiv"},
        {"doi": "10.1234/ABC", "date": "2026-10-04", "source": "journal"},
        {"doi": "10.1234/missing", "source": "journal"},
    ]))
    r = ev.coverage_audit(catalog, ref)
    assert r["n_found"] == 2 and r["missing"] == ["10.1234/missing"]
    assert r["median_lag_days"] == 1.75 and r["per_source"]["journal"]["coverage"] == 0.5
    plain = tmp_path / "ids.txt"
    plain.write_text("https://doi.org/10.1234/abc\n2610.01234\n")
    assert ev.coverage_audit(catalog, plain)["coverage"] == 1.0


def test_cost_per_useful(catalog):
    catalog.store.append_many([
        LedgerEntry(entry_id="e1", route="gemini_flash", provider="google", billing="api_credit", event="reserve", cash_usd=0.5),
        LedgerEntry(entry_id="e2", route="gemini_flash", provider="google", billing="api_credit", event="settle", cash_usd=0.2, calls=10),
        LedgerEntry(entry_id="e3", route="claude_haiku", provider="anthropic", billing="subscription", event="settle", calls=4),
        Briefed(work_id="w1", report_id="daily-1"), Briefed(work_id="w2", report_id="daily-1"),
        Feedback(feedback_id="f1", work_id="w1", label="useful"), Feedback(feedback_id="f2", work_id="w2", label="known"),
    ])
    r = ev.cost_per_useful(catalog.store and catalog)
    assert r["cash_usd"] == 0.2 and r["calls"] == 14 and r["useful"] == 1 and r["briefed"] == 2
    assert r["cost_per_useful_usd"] == 0.2 and r["useful_rate"] == 0.5


def test_compare_readers(catalog, monkeypatch):
    catalog.store.append(Work(work_id="w_abs", title="Abstract only", abstract="We show uniform coverage holds."))
    reply = {"question": "q", "obstacle": None, "idea": "i", "mechanism": None, "main_result": "m",
             "claims": [{"text": "Coverage holds.", "kind": "paper_result",
                         "evidence": [{"quote": "We show uniform coverage holds", "location": "abstract"}]}],
             "conditions": [], "connection": None, "escalate": []}
    good = Calls(reply)

    def routed(ctx, role, req, route=None):
        if route == "broken":
            raise RuntimeError("auth expired")
        return good(ctx, role, req, route)

    monkeypatch.setattr(_llm, "call", routed)
    path = ev.compare_readers(catalog, ["w_abs"], ["claude_haiku", "broken"])
    assert path.exists() and path.suffix == ".md"
    data = json.loads(path.with_suffix(".json").read_text())
    assert data["summary"]["claude_haiku"]["mean_verified_quote_rate"] == 1.0
    assert data["summary"]["broken"]["schema_valid_rate"] == 0.0
    assert {r[2] for r in good.reqs} == {"claude_haiku"}
    assert catalog.store.count(ReadingNote) == 0  # comparisons don't overwrite real readings
    assert "| mean_verified_quote_rate |" in path.read_text()
