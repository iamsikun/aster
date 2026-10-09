from pathlib import Path

import pytest
from test_read_reader import Calls

from lark.read import _llm, deep
from lark.read.fetch import store_document
from lark.records import ReadingNote, Work

HTML = (Path(__file__).parent / "fixtures" / "read" / "arxiv_latexml.html").read_bytes()


@pytest.mark.parametrize("inp,expected", [
    ("2610.01234", {"arxiv": "2610.01234", "version": None}),
    ("arXiv:2610.01234v2", {"arxiv": "2610.01234", "version": "v2"}),
    ("https://arxiv.org/abs/2610.01234v3", {"arxiv": "2610.01234", "version": "v3"}),
    ("https://arxiv.org/pdf/2610.01234", {"arxiv": "2610.01234", "version": None}),
    ("math/0101001", {"arxiv": "math/0101001", "version": None}),
    ("10.1093/restud/rdab001", {"doi": "10.1093/restud/rdab001"}),
    ("https://doi.org/10.1093/ReStud/rdab001", {"doi": "10.1093/restud/rdab001"}),
    ("https://example.org/paper.pdf", {"url": "https://example.org/paper.pdf", "pdf": True}),
])
def test_resolve_identifier(inp, expected):
    assert deep.resolve_identifier(inp) == expected


def test_resolve_identifier_rejects_garbage():
    with pytest.raises(ValueError):
        deep.resolve_identifier("not a paper")


def test_resolve_work_finds_existing_or_creates(ctx):
    ctx.store.append(Work(work_id="w_known", title="Known", ids={"arxiv": "2610.01234"}))
    assert deep.resolve_work(ctx, "https://arxiv.org/abs/2610.01234v1").work_id == "w_known"
    w = deep.resolve_work(ctx, "doi:10.1234/xyz")  # offline: minimal work, no metadata
    assert w.ids == {"doi": "10.1234/xyz"} and ctx.store.get(Work, w.work_id) is not None
    assert deep.resolve_work(ctx, "10.1234/XYZ").work_id == w.work_id


def test_deep_read_chunks_then_reduces(ctx, monkeypatch):
    ctx.store.append(Work(work_id="w_d", title="Bootstrap", ids={"arxiv": "2610.01234"}, abstract="We study."))
    store_document(ctx, "w_d", "https://arxiv.org/html/2610.01234", "html", HTML)
    calls = Calls()
    monkeypatch.setattr(_llm, "call", calls)
    monkeypatch.setattr(deep, "FULL_BUDGET_TOKENS", 60)  # force several parts
    note = deep.deep_read(ctx, "2610.01234", depth="technical")
    prompts = [r[1].prompt for r in calls.reqs]
    assert len(calls.reqs) >= 3 and "Part 1 of" in prompts[0]
    assert calls.reqs[-1][1].prompt_version.endswith("+reduce")
    assert note.depth == "technical" and note.full_text_obtained
    assert "References" not in note.sections_read
    assert ctx.store.get(ReadingNote, f"w_d|{note.version_id}|technical") is not None
    assert note.claims[0].verified


def test_deep_read_rejects_bad_depth(ctx):
    with pytest.raises(ValueError):
        deep.deep_read(ctx, "2610.01234", depth="focused")
