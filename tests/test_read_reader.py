from pathlib import Path

import pytest

from lark.jobs import JobQueue
from lark.llm.base import LLMResult
from lark.profiles import Profile
from lark.read import _llm, reader
from lark.read.fetch import store_document
from lark.records import Job, LLMCall, ReadingNote, Screening, Work

HTML = (Path(__file__).parent / "fixtures" / "read" / "arxiv_latexml.html").read_bytes()

REPLY = {
    "question": "Inference on the mean of the selected arm.",
    "obstacle": "The bootstrap is inconsistent at ties.",
    "idea": "A smoothed maximum with vanishing temperature.",
    "mechanism": "Smoothing restores differentiability.",
    "main_result": "Uniform asymptotic coverage including exact ties.",
    "claims": [
        {"text": "Uniform coverage including exact ties.", "kind": "paper_result",
         "evidence": [{"quote": "achieves uniform asymptotic coverage over the parameter space, including configurations with exact ties", "location": "Theorem 2"}]},
        {"text": "Coverage is 94.8% vs 87.1%.", "kind": "paper_result",
         "evidence": [{"quote": "coverage is 94.8% against 87.1% for the naive bootstrap", "location": "3.1 Coverage"}]},
        {"text": "It also beats everything by 40%.", "kind": "bogus-kind",
         "evidence": [{"quote": "invented sentence not in the paper at all whatsoever", "location": None}]},
    ],
    "conditions": ["i.i.d. within arm", "finite fourth moments"],
    "connection": {"profile": "selection-inference", "text": "Could replace projection intervals."},
    "escalate": ["Does the result hold for adaptively collected data?"],
}


class Calls:
    def __init__(self, reply=REPLY):
        self.reqs = []
        self.reply = reply

    def __call__(self, ctx, role, req, route=None):
        self.reqs.append((role, req, route))
        return LLMResult(ok=True, data=self.reply, text="{}", call=LLMCall(
            role=role, route=route or "fake", provider="fake", model="fake-1", billing="none",
            usage={"input_tokens": 100, "output_tokens": 50}, latency_s=0.1))


@pytest.fixture
def setup(ctx, monkeypatch):
    ctx.config.profiles.append(Profile(id="selection-inference", name="Selection and inference",
                                       questions=["Inference after selecting a maximum"]))
    ctx.config.profiles.append(Profile(id="other", name="Unrelated profile"))
    w = Work(work_id="w_sel", title="Bootstrap Inference for the Selected Arm", abstract="We study the best arm.",
             ids={"arxiv": "2610.01234"}, current_version="arxiv:v1")
    ctx.store.append(w)
    ctx.store.append(Screening(work_id="w_sel", profiles_version="pv", topics=["selection-inference"],
                               priority="high", next_action="focused_read", reason="bootstrap coverage at ties"))
    store_document(ctx, "w_sel", "https://arxiv.org/html/2610.01234", "html", HTML)
    calls = Calls()
    monkeypatch.setattr(_llm, "call", calls)
    return ctx, calls


def test_read_work_focused(setup):
    ctx, calls = setup
    note = reader.read_work(ctx, "w_sel")
    role, req, _ = calls.reqs[0]
    assert role == "reader" and req.schema is reader.READING_SCHEMA
    assert req.prompt_version.startswith("focused-")
    assert "[selection-inference]" in req.prompt and "Unrelated profile" not in req.prompt
    assert "References" not in note.sections_read and "Abstract" in note.sections_read
    assert note.full_text_obtained and note.depth == "focused" and note.version_id == "arxiv:v1"
    assert [c.verified for c in note.claims] == [True, True, False]
    assert note.claims[2].kind == "author_interpretation"  # unknown kind downgraded, never upgraded
    assert note.connection.profile == "selection-inference"
    assert note.llm.model == "fake-1"
    assert ctx.store.get(ReadingNote, "w_sel|arxiv:v1|focused") is not None


def test_read_work_custom_role_and_abstract_fallback(setup):
    ctx, calls = setup
    ctx.store.append(Work(work_id="w_abs", title="No full text", abstract="Only an abstract here."))
    note = reader.read_work(ctx, "w_abs", role="local_reader")
    assert calls.reqs[-1][0] == "local_reader"
    assert note.depth == "abstract" and not note.full_text_obtained and note.sections_read == ["Abstract"]
    assert "Full text obtained: no" in calls.reqs[-1][1].prompt
    assert reader.read_work(ctx, "w_missing") is None


def test_run_read_jobs_done_failed_deferred(setup, monkeypatch):
    ctx, calls = setup
    q = JobQueue(ctx.store)
    q.enqueue("read", "w_sel", priority=90)
    q.enqueue("read", "w_missing", priority=80)
    stats = reader.run_read_jobs(ctx)
    assert stats["done"] == 1 and stats["failed"] == 1

    class NoRoute(Exception):
        pass

    def boom(*a, **k):
        raise NoRoute("all routes exhausted")

    monkeypatch.setattr(_llm, "no_route_error", lambda: NoRoute)
    monkeypatch.setattr(_llm, "call", boom)
    ctx.store.append(Work(work_id="w_a", title="A", abstract="a"))
    ctx.store.append(Work(work_id="w_b", title="B", abstract="b"))
    q.enqueue("read", "w_a", priority=70)
    q.enqueue("read", "w_b", priority=60)
    stats = reader.run_read_jobs(ctx)
    assert stats["deferred"] == 2 and stats["done"] == 0
    states = {j.work_id: j.state for j in ctx.store.all(Job)}
    assert states["w_a"] == "deferred" and states["w_b"] == "deferred"  # kept, not dropped


def test_model_failure_raises(setup, monkeypatch):
    ctx, _ = setup
    monkeypatch.setattr(_llm, "call", lambda *a, **k: LLMResult(ok=False, error="bad json", error_kind="invalid_output"))
    with pytest.raises(reader.ReadFailed):
        reader.read_work(ctx, "w_sel")


def test_prompts_load_with_versions():
    for depth in ("focused", "full", "technical", "abstract"):
        version, text = reader.load_prompt(depth)
        assert "-2026-" in version and "{core}" not in text
        assert "verbatim" in text and "author_interpretation" in text and "escalate" in text
