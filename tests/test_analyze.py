from pathlib import Path

from lark import analyze
from lark.llm.base import LLMResult
from lark.read import _llm
from lark.read.fetch import store_document
from lark.records import Analysis, Claim, Evidence, LLMCall, ReadingNote, Screening, Work

HTML = (Path(__file__).parent / "fixtures" / "read" / "arxiv_latexml.html").read_bytes()


def _note(wid, escalate, verified=True, unsupported=False):
    return ReadingNote(
        work_id=wid, version_id="v1", depth="focused", main_result=f"result of {wid}", escalate=escalate,
        claims=[Claim(text="Uniform coverage.", verified=verified,
                      evidence=[Evidence(quote="uniform asymptotic coverage", location="Thm 2", verified=verified)])],
        verification={"unsupported_claims": [0] if unsupported else [], "unsupported_numbers": []},
    )


def test_needs_escalation_rules():
    high = Screening(work_id="w", profiles_version="p", priority="high")
    low = Screening(work_id="w", profiles_version="p", priority="low")
    assert analyze.needs_escalation(_note("w", []), high)[0] is False
    assert analyze.needs_escalation(_note("w", ["Is it fast?"]), high)[0] is True
    assert analyze.needs_escalation(_note("w", ["Is it fast?"]), low)[0] is False
    assert analyze.needs_escalation(_note("w", ["Is it fast?"], unsupported=True), low)[0] is True
    assert analyze.needs_escalation(_note("w", ["Does the identification assumption hold?"]), low)[0] is True
    assert analyze.needs_escalation(_note("w", ["Is it fast?"]), low, require_trigger=False)[0] is True


def _setup(ctx, monkeypatch, reply=None):
    for wid, title, prio in [("w1", "Bootstrap for the selected arm", "high"), ("w2", "Winner's curse in ranking", "low"),
                             ("w3", "Selected arm bootstrap with dependence", "low")]:
        ctx.store.append(Work(work_id=wid, title=title, abstract=f"{title} abstract"))
        ctx.store.append(Screening(work_id=wid, profiles_version="p", priority=prio))
    store_document(ctx, "w1", "https://arxiv.org/html/2610.01234", "html", HTML)
    ctx.store.append(_note("w1", ["Does Theorem 2 allow dependent observations?", "Is the rate n^{-1/4} needed?"]))
    ctx.store.append(_note("w2", ["Is it fast?"]))  # low priority, no trigger -> not escalated
    ctx.store.append(_note("w3", []))
    reqs = []

    def fake(c, role, req, route=None):
        reqs.append(req)
        return LLMResult(ok=True, data=reply or {
            "answer": "No: Section 2 assumes i.i.d. observations.",
            "evidence": [{"quote": "Observations are i.i.d. within each arm", "location": "2 Setup"},
                         {"quote": "made-up quote that does not exist", "location": None}],
            "confidence_note": "Checked Setup only.",
        }, call=LLMCall(role=role, route="fake", provider="fake", model="fake-1", billing="none"))

    monkeypatch.setattr(_llm, "call", fake)
    return reqs


def test_run_respects_cap_and_uses_original_passages(ctx, monkeypatch):
    reqs = _setup(ctx, monkeypatch)
    out = analyze.run(ctx, limit=1)
    assert len(out) == 1 and out[0].work_id == "w1"
    prompt = reqs[0].prompt
    assert "<<<SOURCE" in prompt and "i.i.d. within each arm" in prompt  # original passage, not the summary
    assert "Related saved works" in prompt and "Selected arm bootstrap with dependence" in prompt
    assert [e.verified for e in out[0].evidence] == [True, False]
    # cap: analyses_per_day=3 by default, one already done today
    rest = analyze.run(ctx)
    assert len(rest) == 1  # only the second w1 question qualifies; w2 has no trigger
    assert analyze.run(ctx) == []  # nothing left (already-answered questions are skipped)
    assert ctx.store.count(Analysis) == 2


def test_run_with_work_ids_ignores_cap_and_triggers(ctx, monkeypatch):
    _setup(ctx, monkeypatch)
    ctx.config.data["limits"]["analyses_per_day"] = 0
    assert analyze.run(ctx) == []
    out = analyze.run(ctx, work_ids=["w2"])
    assert [a.work_id for a in out] == ["w2"]
