import json

from conftest import FIX

from aster import ids as idmod
from aster.records import Checkpoint, Signal, SourceHealth, SourceRecord
from aster.sources import COLLECTORS, collect
from aster.sources.arxiv import ArxivCollector, first_release, id_month, oai_set, parse_feed, parse_oai
from aster.sources.base import FetchResult
from aster.sources.crossref import load_journals, parse_item
from aster.sources.feeds import parse_feed as parse_rss
from aster.sources.huggingface import parse_daily
from aster.sources.nber import NberCollector
from aster.sources.openalex import build_queries, reconstruct_abstract
from aster.sources.openreview import status_for

# ---------- parsers ----------


def test_arxiv_feed_announce_types_and_versions():
    updated, recs = parse_feed((FIX / "arxiv" / "stat.ME.atom").read_text())
    assert updated
    types = {r.announce_type for r in recs}
    assert {"new", "cross", "replace", "replace-cross"} <= types
    rep = next(r for r in recs if r.announce_type == "replace")
    assert rep.version and rep.version != "v1" and rep.released_date == id_month(rep.source_id)
    new = next(r for r in recs if r.announce_type == "new")
    assert new.released_date == "2026-10-09" and new.ids == {"arxiv": new.source_id}
    assert new.abstract and not new.abstract.startswith("arXiv:")
    multi = [r for r in recs if len(r.authors) > 1]
    assert multi and all("," not in a for a in multi[0].authors)


def test_arxiv_oai_parse():
    recs, token = parse_oai((FIX / "arxiv" / "oai_stat.ME.xml").read_text())
    assert len(recs) == 3 and token is None
    with_doi = next(r for r in recs if "doi" in r.ids)
    assert with_doi.ids["arxiv"] == "2110.05720" and with_doi.authors[0] == "Bradley Rava"
    assert oai_set("stat.ME") == "stat:stat:ME" and oai_set("cs.LG") == "cs:cs:LG" and oai_set("q-fin") == "q-fin"


def test_arxiv_dates_revisions_by_id_month():
    assert id_month("2608.00123") == "2026-08" and id_month("math/0601001") is None
    assert first_release("2610.01234", "2026-10-13") == "2026-10-13"
    assert first_release("2608.00123", "2026-10-13") == "2026-08"  # a replacement, not new


def test_crossref_parse_strips_jats():
    items = json.loads((FIX / "crossref" / "0012-9682.json").read_text())["message"]["items"]
    rec = parse_item(items[0], {"id": "ecta", "name": "Econometrica", "issn": "0012-9682", "fields": ["economics"]})
    assert rec.ids["doi"] == items[0]["DOI"].lower()
    assert rec.abstract and "<jats" not in rec.abstract
    assert rec.status == "published" and rec.venue == "Econometrica"
    assert rec.fields == ["economics"]
    assert parse_item({"DOI": "10.1/x", "title": ["Front Matter"]}) is None


def test_crossref_journals_come_from_active_fields(ctx):
    journals = {j["id"]: j for j in load_journals(ctx)}
    assert journals["ecta"]["issns"] == ["0012-9682", "1468-0262"]
    assert journals["ecta"]["fields"] == ["economics"]
    assert journals["mnsc"]["fields"] == ["marketing"]
    assert "jasa" in journals and "nature" not in journals


def test_doi_normalization_to_native_ids():
    assert idmod.ids_from_doi("https://doi.org/10.48550/arXiv.2609.38361") == {"arxiv": "2609.38361"}
    assert idmod.ids_from_doi("10.2139/ssrn.5123456") == {"ssrn": "5123456"}
    assert idmod.ids_from_doi("10.3386/w35800")["nber"] == "w35800"
    assert idmod.ids_from_repec("RePEc:arx:papers:2608.04983")["arxiv"] == "2608.04983"


def test_openalex_abstract_and_queries(ctx):
    assert reconstruct_abstract({"b": [1], "a": [0], "c": [2]}) == "a b c"
    labels = [q[0] for q in build_queries(ctx)]
    assert "field:marketing" in labels and "field:statistics" in labels
    assert labels[-2:] == ["interests", "author:Isaiah Andrews"]


def test_openreview_status_mapping():
    v = "ICLR.cc/2027/Conference"
    assert status_for(v, v) == "accepted"
    assert status_for(f"{v}/Submission", v) == "submitted"
    assert status_for(f"{v}/Withdrawn_Submission", v) == "withdrawn"
    assert status_for(f"{v}/Rejected_Submission", v) is None


def test_feed_entry_links_to_identifiers():
    from aster.fields import Feed

    feed = Feed(name="Example", url="https://research.example.com/feed.xml", fields=["machine-learning"])
    recs = parse_rss(feed, (FIX / "feeds" / "feed_bf26426d.xml").read_text())
    assert recs[0].kind == "release" and recs[0].ids["url"].startswith("https://research.example.com/")
    assert recs[0].extra["related_arxiv"] == ["2610.01234"] and "arxiv" not in recs[0].ids
    assert recs[0].fields == ["machine-learning"]


def test_hf_daily_papers_become_arxiv_records_and_signals():
    recs, sigs = parse_daily(json.loads((FIX / "huggingface" / "daily_papers.json").read_text()))
    assert recs and all("arxiv" in r.ids for r in recs)
    assert sigs[0].about == {"arxiv": recs[0].ids["arxiv"]} and "upvotes" in sigs[0].metrics
    assert sigs[0].origin == "api"


# ---------- driver ----------


def test_collect_offline_all_sources(ctx):
    results = {r.source: r for r in collect(ctx)}
    assert set(results) == set(COLLECTORS)
    for name in ("arxiv", "crossref", "openalex", "nber", "nep", "openreview", "feeds", "huggingface", "github"):
        assert results[name].status in ("ok", "partial"), (name, results[name].error)
        assert results[name].records, name
    assert {r.status for r in results["openreview"].records} == {"submitted", "accepted"}
    assert all(r.fields == ["machine-learning"] for r in results["openreview"].records)
    assert all(r.kind == "release" for r in results["github"].records)
    arxiv_fields = {f for r in results["arxiv"].records for f in r.fields}
    assert "statistics" in arxiv_fields
    assert ctx.store.count(SourceHealth) == len(COLLECTORS)
    assert ctx.store.count(Signal) > 0
    assert ctx.store.get(Checkpoint, "arxiv").value["last_reconcile"] == "2026-10-09"
    assert ctx.store.count(SourceRecord) > 10


def test_muted_field_is_not_collected(ctx):
    for fid in list(ctx.config.fields):
        if fid != "economics":
            ctx.config.fields[fid] = ctx.config.fields[fid].model_copy(update={"tier": "mute"})
    res = collect(ctx, only=["arxiv"])[0]
    assert res.records and not any("statistics" in r.fields for r in res.records)
    assert {j["id"] for j in load_journals(ctx)} >= {"aer", "ecta"}
    assert not {j["id"] for j in load_journals(ctx)} & {"mnsc", "jasa", "jf"}
    assert [q for q, _ in build_queries(ctx) if q.startswith("field:")] == ["field:economics"]


def test_checkpoints_prevent_reingest(ctx):
    collect(ctx, only=["arxiv", "nber", "nep"])
    again = {r.source: r for r in collect(ctx, only=["arxiv", "nber", "nep"])}
    assert again["arxiv"].status == "empty"  # same feed <updated>, reconcile not yet due
    assert again["nber"].status == "empty"
    assert again["nep"].status == "empty"


def test_nber_goes_stale_not_failed(ctx):
    res = NberCollector().fetch(ctx, {"max_wp": 99999, "last_new": "2026-09-20"})
    assert res.status == "stale" and res.error


def test_failure_isolation_and_checkpoint_kept(ctx, monkeypatch):
    ctx.store.append(Checkpoint(source="arxiv", value={"feed_updated": "old", "last_reconcile": "2026-10-08"}))

    def boom(self, ctx, checkpoint):
        raise RuntimeError("network down")

    monkeypatch.setattr(ArxivCollector, "fetch", boom)
    results = {r.source: r for r in collect(ctx, only=["arxiv", "nber"])}
    assert results["arxiv"].status == "failed" and "network down" in results["arxiv"].error
    assert results["nber"].status == "ok"
    assert ctx.store.get(Checkpoint, "arxiv").value["feed_updated"] == "old"
    health = {h.source: h for h in ctx.store.all(SourceHealth)}
    assert health["arxiv"].status == "failed"


def test_limit_marks_partial(ctx):
    res = collect(ctx, only=["arxiv"], limit=2)[0]
    assert len(res.records) == 2 and res.truncated and res.status == "partial"


def test_unknown_source_is_rejected(ctx):
    import pytest

    with pytest.raises(ValueError):
        collect(ctx, only=["twitter"])


def test_fetchresult_shape():
    r = FetchResult(source="x", status="empty")
    assert r.records == [] and r.checkpoint == {} and r.signals == []
