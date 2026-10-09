from pathlib import Path

import pytest
import yaml

from lark.context import make_context
from lark.records import Checkpoint, SourceHealth, SourceRecord
from lark.sources import COLLECTORS, collect
from lark.sources import _common as common
from lark.sources.arxiv import ArxivCollector, oai_set, parse_feed, parse_oai
from lark.sources.base import FetchResult
from lark.sources.crossref import parse_item
from lark.sources.nber import NberCollector
from lark.sources.openalex import build_queries, reconstruct_abstract
from lark.sources.openreview import status_for
from lark.sources.ssrn_alerts import parse_alert

FIX = Path(__file__).parent / "fixtures" / "sources"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LARK_WRITER", "test")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "selection.yaml").write_text(yaml.safe_dump({
        "name": "Selection and statistical decision making",
        "keywords": ["winner's curse", "selective inference"],
        "watch": {"authors": ["Isaiah Andrews"], "hf_orgs": ["google"]},
    }))
    (tmp_path / "journals.yaml").write_text(yaml.safe_dump(
        {"journals": [{"name": "Econometrica", "issn": "0012-9682", "field": "economics", "tier": 1}]}))
    (tmp_path / "watchlist.yaml").write_text(yaml.safe_dump({
        "repos": ["vllm-project/vllm"],
        "blogs": [{"name": "Example Research", "url": "https://research.example.com/feed.xml"}],
    }))
    (tmp_path / "lark.yaml").write_text(yaml.safe_dump(
        {"sources": {"openreview": {"venues": ["ICLR.cc/2027/Conference"]}, "nep": {"reports": ["nep-ecm"]}}}))
    return tmp_path


def _ctx(data_dir, **kw):
    ctx = make_context(data_dir, offline=True, dry_run=True, fake_llm=True)
    ctx.run_date = kw.get("run_date", "2026-10-09")
    return ctx


# ---------- parsers ----------

def test_arxiv_feed_announce_types_and_versions():
    updated, recs = parse_feed((FIX / "arxiv" / "stat.ME.atom").read_text())
    assert updated
    types = {r.announce_type for r in recs}
    assert {"new", "cross", "replace", "replace-cross"} <= types
    rep = next(r for r in recs if r.announce_type == "replace")
    assert rep.version and rep.version != "v1" and rep.released_date is None
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
    assert oai_set("stat.ME") == "stat:stat:ME" and oai_set("cs.LG") == "cs:cs:LG"


def test_crossref_parse_strips_jats():
    import json

    items = json.loads((FIX / "crossref" / "0012-9682.json").read_text())["message"]["items"]
    rec = parse_item(items[0], {"name": "Econometrica", "issn": "0012-9682"})
    assert rec.ids["doi"] == items[0]["DOI"].lower()
    assert rec.abstract and "<jats" not in rec.abstract
    assert rec.status == "published" and rec.venue == "Econometrica"
    assert parse_item({"DOI": "10.1/x", "title": ["Front Matter"]}) is None


def test_doi_normalization_to_native_ids():
    assert common.ids_from_doi("https://doi.org/10.48550/arXiv.2609.38361") == {"arxiv": "2609.38361"}
    assert common.ids_from_doi("10.2139/ssrn.5123456") == {"ssrn": "5123456"}
    assert common.ids_from_doi("10.3386/w35800")["nber"] == "w35800"
    assert common.ids_from_repec("RePEc:arx:papers:2608.04983")["arxiv"] == "2608.04983"


def test_openalex_abstract_and_queries(data_dir):
    assert reconstruct_abstract({"b": [1], "a": [0], "c": [2]}) == "a b c"
    labels = [q[0] for q in build_queries(_ctx(data_dir))]
    assert labels == ["profile:selection", "author:Isaiah Andrews"]


def test_ssrn_alert_parse():
    recs = parse_alert((FIX / "ssrn_alerts" / "01_ejournal.eml").read_bytes())
    assert [r.source_id for r in recs] == ["5123456", "5198765"]
    first = recs[0]
    assert first.title.startswith("Statistical Factors and the Winner's Curse")
    assert first.authors == ["Jane Q. Researcher", "John Smith", "Wei Zhang"]
    assert first.abstract and first.abstract.startswith("Researchers select")
    assert recs[1].authors == ["Ana Lopez"]


def test_openreview_status_mapping():
    v = "ICLR.cc/2027/Conference"
    assert status_for(v, v) == "accepted"
    assert status_for(f"{v}/Submission", v) == "submitted"
    assert status_for(f"{v}/Withdrawn_Submission", v) == "withdrawn"
    assert status_for(f"{v}/Rejected_Submission", v) is None


# ---------- driver ----------

def test_collect_offline_all_sources(data_dir):
    ctx = _ctx(data_dir)
    results = {r.source: r for r in collect(ctx)}
    assert set(results) == set(COLLECTORS)
    for name in ("arxiv", "crossref", "openalex", "nber", "nep", "openreview", "ssrn_alerts", "releases"):
        assert results[name].status in ("ok", "partial"), (name, results[name].error)
        assert results[name].records, name
    assert results["openreview"].records[0].status in ("submitted", "accepted")
    assert {r.status for r in results["openreview"].records} == {"submitted", "accepted"}
    rel = results["releases"].records
    assert {r.source_id.split(":")[0] for r in rel} >= {"github", "blog"}
    assert all(r.kind == "release" for r in rel)
    assert ctx.store.count(SourceHealth) == len(COLLECTORS)
    assert ctx.store.get(Checkpoint, "arxiv").value["last_reconcile"] == "2026-10-09"
    assert ctx.store.count(SourceRecord) > 10


def test_checkpoints_prevent_reingest(data_dir):
    ctx = _ctx(data_dir)
    collect(ctx, only=["arxiv", "nber", "ssrn_alerts", "nep"])
    again = {r.source: r for r in collect(ctx, only=["arxiv", "nber", "ssrn_alerts", "nep"])}
    assert again["arxiv"].status == "empty"  # same feed <updated>, reconcile not yet due
    assert again["nber"].status == "empty"
    assert again["ssrn_alerts"].status == "empty"
    assert again["nep"].status == "empty"


def test_nber_goes_stale_not_failed(data_dir):
    ctx = _ctx(data_dir)
    res = NberCollector().fetch(ctx, {"max_wp": 99999, "last_new": "2026-09-20"})
    assert res.status == "stale" and res.error


def test_failure_isolation_and_checkpoint_kept(data_dir, monkeypatch):
    ctx = _ctx(data_dir)
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


def test_ssrn_without_credentials_reports_not_configured(data_dir, monkeypatch):
    from lark.sources.ssrn_alerts import SsrnAlertsCollector

    monkeypatch.delenv("LARK_IMAP_USER", raising=False)
    ctx = _ctx(data_dir)
    ctx.offline = False
    res = SsrnAlertsCollector().fetch(ctx, {})
    assert res.status == "failed" and res.error == "not configured"


def test_limit_marks_partial(data_dir):
    ctx = _ctx(data_dir)
    res = collect(ctx, only=["arxiv"], limit=2)[0]
    assert len(res.records) == 2 and res.truncated and res.status == "partial"


def test_fetchresult_shape():
    r = FetchResult(source="x", status="empty")
    assert r.records == [] and r.checkpoint == {}
