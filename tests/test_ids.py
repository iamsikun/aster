from aster.ids import canonical_url, ids_from_url, parse_ref


def test_ids_from_urls():
    assert ids_from_url("https://arxiv.org/abs/2610.01234v2") == {"arxiv": "2610.01234"}
    assert ids_from_url("https://huggingface.co/papers/2610.01234") == {"arxiv": "2610.01234"}
    assert ids_from_url("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5123456") == {"ssrn": "5123456"}
    assert ids_from_url("https://www.nber.org/papers/w35800") == {"nber": "w35800"}
    assert ids_from_url("https://openreview.net/forum?id=AbC_12") == {"openreview": "AbC_12"}
    assert ids_from_url("https://doi.org/10.1287/mksc.2026.0123") == {"doi": "10.1287/mksc.2026.0123"}
    assert ids_from_url("https://huggingface.co/google/gemma-4") == {"hf": "google/gemma-4"}
    assert ids_from_url("https://example.com/post") == {}


def test_canonical_url():
    assert canonical_url("https://twitter.com/user/status/1?s=20&utm_source=x#top") == "https://x.com/user/status/1"
    assert canonical_url("HTTPS://WWW.Example.com/a/") == "https://example.com/a"


def test_parse_ref():
    assert parse_ref("arXiv:2610.01234v3") == {"arxiv": "2610.01234"}
    assert parse_ref("2610.01234") == {"arxiv": "2610.01234"}
    assert parse_ref("10.48550/arXiv.2610.01234") == {"arxiv": "2610.01234"}
    assert parse_ref("ssrn:5123456") == {"ssrn": "5123456"}
    assert parse_ref("https://x.com/a/status/1") == {"url": "https://x.com/a/status/1"}
    assert parse_ref("not a ref") == {}
