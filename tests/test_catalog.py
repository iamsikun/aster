import pytest

from lark.catalog import resolve, work_for_source
from lark.catalog.versions import classify_change
from lark.context import make_context
from lark.records import MergeProposal, SourceRecord, Version, Work

ABSTRACT = (
    "We propose a bootstrap procedure for inference on the value of the empirically best arm. "
    "The procedure is uniformly valid, including at exact ties, and is simple to implement."
)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("LARK_WRITER", "test")
    return make_context(tmp_path, offline=True, dry_run=True, fake_llm=True)


def rec(source, sid, *, ids, title="Bootstrap Inference for the Selected Arm", abstract=ABSTRACT,
        authors=("Jane Doe", "John Roe"), status="preprint", version=None, released="2026-10-01", **kw):
    return SourceRecord(source=source, source_id=sid, title=title, abstract=abstract, authors=list(authors),
                        ids=ids, status=status, version=version, released_date=released, **kw)


def test_exact_identifier_merge_across_sources(ctx):
    s1 = resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234"}, version="v1")])
    assert len(s1.new_work_ids) == 1
    wid = s1.new_work_ids[0]
    # OpenAlex reports the same arXiv paper (via its DataCite DOI, normalized to an arXiv id)
    s2 = resolve(ctx, [rec("openalex", "W1", ids={"openalex": "W1", "arxiv": "2610.01234"}, released="2026-09-30")])
    assert s2.new_work_ids == [] and s2.updated_work_ids == [wid]
    w = ctx.store.get(Work, wid)
    assert set(w.ids) == {"arxiv", "openalex"}
    assert w.source_keys == ["arxiv|2610.01234", "openalex|W1"]
    assert w.released_date == "2026-09-30"  # earliest wins
    assert work_for_source(ctx, "openalex", "W1").work_id == wid


def test_status_upgrade_is_substantive(ctx):
    wid = resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234"}, version="v1")]).new_work_ids[0]
    # arXiv OAI record carries the journal DOI -> links preprint and journal version
    resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234", "doi": "10.1080/x.2026.1"})])
    s = resolve(ctx, [rec("crossref", "10.1080/x.2026.1", ids={"doi": "10.1080/x.2026.1"}, status="published",
                          venue="JASA", url="https://doi.org/10.1080/x.2026.1", published_date="2026-10-05")])
    w = ctx.store.get(Work, wid)
    assert w.status == "published" and w.venue == "JASA"
    assert w.urls["published"] == "https://doi.org/10.1080/x.2026.1"
    assert wid in s.substantive_update_ids


def test_fuzzy_match_only_proposes(ctx):
    a = resolve(ctx, [rec("ssrn_alerts", "5123456", ids={"ssrn": "5123456"})]).new_work_ids[0]
    s = resolve(ctx, [rec("nep", "RePEc:xxx:wpaper:1", ids={"repec": "RePEc:xxx:wpaper:1"},
                          title="Bootstrap inference for the selected arm.")])
    assert len(s.new_work_ids) == 1 and s.new_work_ids[0] != a  # not merged
    assert len(s.proposals) == 1
    assert ctx.store.count(MergeProposal) == 1
    # different first author: no proposal
    s2 = resolve(ctx, [rec("crossref", "10.9/zz", ids={"doi": "10.9/zz"}, authors=("Someone Else",))])
    assert s2.proposals == []


def test_identifier_conflict_creates_proposal(ctx):
    a = resolve(ctx, [rec("arxiv", "2610.1", ids={"arxiv": "2610.1"}, title="Paper A about factor models today")])
    b = resolve(ctx, [rec("crossref", "10.1/b", ids={"doi": "10.1/b"}, title="Paper B on asset allocation rules",
                          authors=("Z Z",))])
    s = resolve(ctx, [rec("openalex", "W9", ids={"arxiv": "2610.1", "doi": "10.1/b"})])
    assert s.new_work_ids == []
    assert {s.proposals[0].work_id, s.proposals[0].other_work_id} == {a.new_work_ids[0], b.new_work_ids[0]}


def test_arxiv_versions_and_revision_triage(ctx):
    wid = resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234"}, version="v1")]).new_work_ids[0]
    # v2 with identical abstract -> unknown (full text not compared), not substantive
    s = resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234"}, version="v2")])
    assert wid not in s.substantive_update_ids
    # v3 with a rewritten abstract -> substantive, and title/abstract updated
    new_abs = "We show the standard bootstrap fails after selection and propose a smoothed alternative with rates."
    s = resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234"}, version="v3", abstract=new_abs)])
    assert wid in s.substantive_update_ids
    vs = {v.version_id: v for v in ctx.store.all(Version)}
    assert vs["arxiv:v1"].change == "new"
    assert vs["arxiv:v2"].change == "unknown"
    assert vs["arxiv:v3"].change == "substantive"
    assert ctx.store.get(Work, wid).abstract == new_abs
    # same version again -> nothing new
    s = resolve(ctx, [rec("arxiv", "2610.01234", ids={"arxiv": "2610.01234"}, version="v3", abstract=new_abs)])
    assert s.substantive_update_ids == [] and ctx.store.count(Version) == 3


def test_withdrawal(ctx):
    wid = resolve(ctx, [rec("arxiv", "2610.5", ids={"arxiv": "2610.5"}, version="v1")]).new_work_ids[0]
    s = resolve(ctx, [rec("arxiv", "2610.5", ids={"arxiv": "2610.5"}, version="v2", status="withdrawn",
                          abstract="This paper has been withdrawn by the author due to an error in Theorem 2.")])
    assert wid in s.substantive_update_ids
    assert ctx.store.get(Work, wid).status == "withdrawn"


def test_aggregator_rewording_is_not_a_revision(ctx):
    wid = resolve(ctx, [rec("arxiv", "2610.7", ids={"arxiv": "2610.7"}, version="v1")]).new_work_ids[0]
    s = resolve(ctx, [rec("openalex", "W7", ids={"openalex": "W7", "arxiv": "2610.7"}, abstract="Different words.")])
    assert s.substantive_update_ids == [] and wid in s.updated_work_ids


def test_classify_change():
    assert classify_change(None, "x") == "new"
    assert classify_change("Same text", "same   text") == "trivial"
    assert classify_change(ABSTRACT, ABSTRACT.replace("simple", "simpel")) == "trivial"
    assert classify_change(ABSTRACT, "A completely different abstract about something else.") == "substantive"
    assert classify_change(ABSTRACT, "This paper has been withdrawn.") == "withdrawn"


def test_resolve_on_collected_fixtures(ctx, tmp_path):
    """End to end: offline collection then resolve links every record to a work."""
    from lark.sources import collect

    results = collect(ctx, only=["arxiv", "nep", "nber"])
    records = [r for res in results for r in res.records]
    s = resolve(ctx, records)
    assert s.new_work_ids
    assert all(r.work_id for r in records)
    assert ctx.store.count(Work) == len(s.new_work_ids)
