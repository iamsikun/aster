import os

import pytest
import yaml

from lark.context import make_context
from lark.jobs import JobQueue
from lark.llm import roles
from lark.llm.fake import FakeWorker
from lark.records import Job, Screening, Work
from lark.screen import audit, candidates, read_priority, run
from lark.screen.screener import SCHEMA, screen

PROFILES = {
    "selection": {
        "name": "Selection and statistical decision making",
        "bucket": "active", "priority": "high",
        "questions": ["How to do valid inference on the winner after selecting the best arm?"],
        "keywords": ["winner's curse", "selective inference", "bootstrap", "maximum", "ranking uncertainty"],
        "watch": {"authors": ["Isaiah Andrews"]},
    },
    "factors": {
        "name": "Factor models for finance",
        "bucket": "methods",
        "keywords": ["factor model", "asset pricing", "risk premia", "portfolio"],
    },
}

WORKS = [
    ("w_sel", "Bootstrap inference for the maximum of estimated means",
     "We study selective inference after picking the best arm and correct the winner's curse with a bootstrap."),
    ("w_fac", "Statistical factor models and risk premia", "A factor model for asset pricing and portfolio choice."),
    ("w_bio", "Protein folding with diffusion", "We fold proteins using diffusion models on amino acid chains."),
    ("w_watch", "Notes on something unrelated", "Gardening and soil chemistry."),
    ("w_noabs", "Winner's curse in ranking: bootstrap corrections", None),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LARK_NO_EMBED", "1")
    monkeypatch.setenv("LARK_WRITER", "test")
    roles.reset_workers()
    FakeWorker.handlers.clear()
    yield
    FakeWorker.handlers.clear()


def make(tmp_path, extra_cfg: str = ""):
    (tmp_path / "profiles").mkdir()
    for pid, body in PROFILES.items():
        (tmp_path / "profiles" / f"{pid}.yaml").write_text(yaml.safe_dump(body))
    (tmp_path / "lark.yaml").write_text("limits: {exploration_sample: 1, audit_fraction: 0.5}\n" + extra_cfg)
    ctx = make_context(tmp_path, fake_llm=True, dry_run=True, offline=True)
    authors = {"w_watch": ["Isaiah Andrews"]}
    for wid, title, abstract in WORKS:
        ctx.store.append(Work(work_id=wid, title=title, abstract=abstract, authors=authors.get(wid, ["A. Person"])))
    return ctx


def handler(req):
    if "Bootstrap inference for the maximum" in req.prompt:
        return {"topics": ["selection", "bogus"], "priority": "high", "next_action": "focused_read",
                "reason": "Inference after selecting a maximum.", "information_missing": []}
    if "Statistical factor models" in req.prompt:
        return {"topics": ["factors"], "priority": "medium", "next_action": "full_read", "reason": "r",
                "information_missing": []}
    return {"topics": [], "priority": "none", "next_action": "skip", "reason": "unrelated", "information_missing": []}


def test_retrieval_union_bm25_watchlist_exploration(tmp_path):
    ctx = make(tmp_path)
    cands = {c.work_id: c for c in candidates(ctx, ctx.store.all(Work))}
    assert "bm25" in cands["w_sel"].routes and "selection" in cands["w_sel"].profiles
    assert "factors" in cands["w_fac"].profiles
    assert "watchlist" in cands["w_watch"].routes
    assert cands["w_noabs"].no_abstract and "bm25" in cands["w_noabs"].routes
    explore = [c for c in cands.values() if c.routes == {"exploration"}]
    assert len(explore) == 1 and explore[0].work_id == "w_bio"
    again = [c.work_id for c in candidates(ctx, ctx.store.all(Work))]
    assert again == [c.work_id for c in candidates(ctx, ctx.store.all(Work))]  # deterministic


def test_run_screens_enqueues_reads_and_audits(tmp_path):
    FakeWorker.handlers["screener"] = handler
    ctx = make(tmp_path)
    stats = run(ctx, [w[0] for w in WORKS])
    pv = ctx.config.profiles_version
    sel = ctx.store.get(Screening, f"w_sel|{pv}")
    assert sel.topics == ["selection"]  # unknown topic ids dropped
    assert sel.llm.route == "fake" and "bm25" in sel.retrieval_routes
    noabs = ctx.store.get(Screening, f"w_noabs|{pv}")
    assert noabs.next_action == "insufficient_info" and "abstract" in noabs.information_missing
    assert stats.to_read == 2 and stats.insufficient == 1
    reads = {j.work_id: j for j in JobQueue(ctx.store).pending(kind="read")}
    assert set(reads) == {"w_sel", "w_fac"}
    assert reads["w_sel"].priority > reads["w_fac"].priority  # active bucket outranks methods
    assert reads["w_fac"].payload["depth"] == "full"
    assert stats.audits >= 1 and all(j.tag == "gpu" for j in JobQueue(ctx.store).pending(kind="audit"))

    # idempotent: nothing is re-screened for the same profiles version
    stats2 = run(ctx, [w[0] for w in WORKS])
    assert stats2.screened == 0


def test_overflow_is_deferred_not_dropped(tmp_path):
    FakeWorker.handlers["screener"] = handler
    ctx = make(tmp_path)
    ctx.config.data["limits"]["screen_per_day"] = 2
    stats = run(ctx, [w[0] for w in WORKS])
    assert stats.screened == 2 and stats.deferred >= 3
    pending = JobQueue(ctx.store).pending(kind="screen")
    assert len(pending) == stats.deferred and all(j.state == "deferred" for j in pending)
    # next day (fresh cap): the backlog gets screened and its jobs close
    ctx.config.data["limits"]["screen_per_day"] = 100
    # simulate a new day: today's screenings no longer count against the cap
    ctx.store.db.execute("UPDATE events SET ts = '2000-01-01' WHERE kind = 'screenings'")
    run(ctx, [])
    assert JobQueue(ctx.store).pending(kind="screen") == []
    assert all(ctx.store.get(Job, j.job_id).state == "done" for j in pending)


def test_no_route_defers_remaining(tmp_path):
    ctx = make(tmp_path)
    ctx.fake_llm = False
    ctx.config.data["roles"]["screener"] = {"routes": ["claude_haiku"], "strategy": "fallback"}

    class Off(FakeWorker):
        def available(self):
            return False, "logged out"

    roles.register_worker("claude_haiku", Off("claude_haiku"))
    cands = candidates(ctx, ctx.store.all(Work))
    done = screen(ctx, cands)
    assert done == [] and len(JobQueue(ctx.store).pending(kind="screen")) == len(cands)


def test_audit_jobs_store_separately_and_flag_disagreement(tmp_path):
    FakeWorker.handlers["screener"] = handler
    ctx = make(tmp_path)
    run(ctx, [w[0] for w in WORKS])
    q = JobQueue(ctx.store)
    q.enqueue("audit", "w_bio", tag="gpu")
    FakeWorker.handlers["local_screener"] = lambda r: {
        "topics": ["selection"], "priority": "medium", "next_action": "focused_read", "reason": "audit thinks so",
        "information_missing": []}
    out = audit.run_audit_jobs(ctx, role="local_screener")
    assert out["done"] >= 1 and out["disagreements"] >= 1
    pv = ctx.config.profiles_version
    assert ctx.store.get(Screening, f"w_bio|{pv}:audit").audit is True
    primary = ctx.store.get(Screening, f"w_bio|{pv}")
    assert primary is None or primary.audit is False
    assert any(d["work_id"] == "w_bio" for d in audit.audit_disagreements(ctx))
    assert any(j.work_id == "w_bio" for j in q.pending(kind="read"))


def test_read_priority_uses_attention(tmp_path):
    ctx = make(tmp_path)
    s_active = Screening(work_id="x", profiles_version="v", topics=["selection"], priority="high", next_action="focused_read")
    s_methods = Screening(work_id="y", profiles_version="v", topics=["factors"], priority="high", next_action="focused_read")
    assert read_priority(ctx, s_active) == 90 and read_priority(ctx, s_methods) == round(90 * 0.25 / 0.6)


def test_schema_is_strict_enough():
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])


@pytest.mark.skipif(not os.getenv("LARK_TEST_EMBED"), reason="downloads an embedding model; set LARK_TEST_EMBED=1")
def test_embedding_route(tmp_path, monkeypatch):
    monkeypatch.delenv("LARK_NO_EMBED")
    ctx = make(tmp_path)
    cands = {c.work_id: c for c in candidates(ctx, ctx.store.all(Work))}
    assert "embedding" in cands["w_sel"].routes
