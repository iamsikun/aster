from aster.catalog import resolve
from aster.findings import add_findings
from aster.rank import candidate_json, candidates
from aster.records import Shown, Signal, SourceRecord, Work


def put(ctx, sid, title, *, fields=(), abstract="", authors=("A Person",), date="2026-10-05", **kw):
    rec = SourceRecord(
        source="test",
        source_id=sid,
        title=title,
        abstract=abstract,
        authors=list(authors),
        ids={"arxiv": sid},
        released_date=date,
        fields=list(fields),
        **kw,
    )
    ctx.store.append(rec)
    resolve(ctx.store, [rec])
    return rec.work_id


def test_ranking_reasons_and_filters(ctx):
    plain = put(ctx, "2610.1", "A study of tariffs", fields=["economics"])
    interest = put(
        ctx,
        "2610.2",
        "Selective inference after model search",
        fields=["statistics"],
        abstract="We correct the winner's curse.",
    )
    followed = put(ctx, "2610.3", "Something unrelated", authors=("Isaiah Andrews",))
    keyword = put(ctx, "2610.4", "Advertising auctions with budgets")
    put(ctx, "2610.5", "Old paper", fields=["economics"], date="2026-01-01")
    put(ctx, "2610.6", "Off-field paper about chemistry")

    found = candidates(ctx, "2026-10-01")
    ids = [c.work.work_id for c in found]
    # interest: core field + keyword + interest match; followed: follow only; keyword: field + keyword
    assert ids == [interest, followed, keyword, plain]
    by = {c.work.work_id: c for c in found}
    assert any(r.startswith("interest:") for r in by[interest].reasons)
    assert "follow:author:Isaiah Andrews" in by[followed].reasons
    assert by[keyword].fields == ["marketing"] and "keyword:advertising" in by[keyword].reasons
    assert [c.work.work_id for c in candidates(ctx, "2026-10-01", field_id="economics")] == [plain]

    ctx.store.append(Shown(work_id=plain))
    assert plain not in [c.work.work_id for c in candidates(ctx, "2026-10-01")]
    shown = [c for c in candidates(ctx, "2026-10-01", include_shown=True) if c.work.work_id == plain]
    assert shown and candidate_json(shown[0])["shown"] is True


def test_muted_fields_are_left_out(ctx):
    wid = put(ctx, "2610.7", "A marketing paper", fields=["marketing"])
    ctx.config.fields["marketing"] = ctx.config.fields["marketing"].model_copy(update={"tier": "mute"})
    assert wid not in [c.work.work_id for c in candidates(ctx, "2026-10-01")]


def test_add_findings_merges_items_and_links_signals(ctx):
    wid = put(ctx, "2610.01234", "Bootstrap inference for the selected arm", fields=["statistics"])
    result = add_findings(
        ctx,
        {
            "items": [
                {
                    "title": "Bootstrap inference for the selected arm",
                    "url": "https://arxiv.org/abs/2610.01234v2",
                    "signals": [
                        {"url": "https://twitter.com/econ/status/1?s=20", "platform": "X", "metrics": {"likes": 40}}
                    ],
                },
                {
                    "title": "A new SSRN paper on pricing",
                    "url": "https://papers.ssrn.com/abstract=5123456",
                    "date": "2026-10-08",
                    "fields": ["marketing", "nonsense"],
                },
            ],
            "signals": [
                {
                    "url": "https://news.ycombinator.com/item?id=9",
                    "platform": "hackernews",
                    "about": "arXiv:2610.01234",
                },
                {"url": "https://bsky.app/profile/a/post/1", "platform": "bluesky", "about": "ssrn:5123456"},
                {"url": "https://x.com/b/status/2", "platform": "x", "about": "10.9999/unknown"},
            ],
        },
    )
    first, second = result["items"]
    assert first == {"work_id": wid, "title": "Bootstrap inference for the selected arm", "new": False}
    assert second["new"] is True
    assert any("nonsense" in w for w in result["warnings"])
    sigs = {s.url: s for s in ctx.store.all(Signal)}
    assert sigs["https://x.com/econ/status/1"].work_id == wid
    assert sigs["https://x.com/econ/status/1"].platform == "x"
    assert sigs["https://news.ycombinator.com/item?id=9"].work_id == wid
    assert sigs["https://bsky.app/profile/a/post/1"].work_id == second["work_id"]
    assert sigs["https://x.com/b/status/2"].work_id is None
    new = ctx.store.get(Work, second["work_id"])
    assert new.ids["ssrn"] == "5123456" and new.fields == ["marketing"]

    found = {c.work.work_id: c for c in candidates(ctx, "2026-10-01")}
    assert "signals:2" in found[wid].reasons

    # The unlinked signal attaches once its paper arrives.
    from aster.catalog import link_signals

    rec = SourceRecord(
        source="crossref",
        source_id="10.9999/unknown",
        title="Late paper",
        ids={"doi": "10.9999/unknown"},
        released_date="2026-10-08",
    )
    resolve(ctx.store, [rec])
    assert link_signals(ctx.store) == 1
