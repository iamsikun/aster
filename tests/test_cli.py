import json

import pytest
from conftest import FIX

from aster.cli import main


@pytest.fixture
def run(tmp_path, config_file, monkeypatch, capsys):
    monkeypatch.setenv("ASTER_CONFIG", str(config_file))
    monkeypatch.setenv("ASTER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ASTER_FIXTURES_DIR", str(FIX))

    def _run(*argv, stdin=None):
        if stdin is not None:
            monkeypatch.setattr("sys.stdin", __import__("io").StringIO(stdin))
        code = main(list(argv))
        out = capsys.readouterr().out
        return code, out

    return _run


def test_offline_round_trip(run):
    code, out = run("refresh", "--offline", "--json")
    assert code == 0
    summary = json.loads(out)
    assert summary["new_works"] > 10 and summary["signals_linked"] >= 0

    code, out = run("candidates", "--since", "2026-01-01", "--json", "--limit", "500")
    data = json.loads(out)
    assert data["schema"] == "aster.candidates/1" and data["items"]
    assert {"economics", "statistics"} <= {f for it in data["items"] for f in it["fields"]}
    first = data["items"][0]
    assert {"work_id", "title", "urls", "reasons", "signals", "score"} <= set(first)

    code, out = run(
        "add",
        stdin=json.dumps(
            {"signals": [{"url": "https://x.com/a/status/1", "platform": "x", "about": first["work_id"]}]}
        ),
    )
    assert code == 0 and json.loads(out)["signals"][0]["work_id"] == first["work_id"]

    code, out = run("lookup", first["work_id"])
    looked = json.loads(out)
    assert looked["work"]["work_id"] == first["work_id"] and looked["signals"][0]["platform"] == "x"

    code, out = run("seen", first["work_id"], "--context", "brief")
    assert code == 0
    code, out = run("candidates", "--since", "2026-01-01", "--json", "--limit", "500")
    assert first["work_id"] not in [it["work_id"] for it in json.loads(out)["items"]]


def test_init_paths_fields_and_doctor(run, tmp_path, monkeypatch):
    new = tmp_path / "fresh" / "config.yaml"
    monkeypatch.setenv("ASTER_CONFIG", str(new))
    assert run("init")[0] == 0 and new.exists()
    assert run("init")[0] == 1  # never overwrites without --force
    code, out = run("paths", "--json")
    assert json.loads(out)["config"] == str(new)
    code, out = run("fields", "--json")
    assert [f["id"] for f in json.loads(out)["fields"]] == [
        "economics",
        "machine-learning",
        "quant-finance",
        "marketing",
    ]
    code, out = run("doctor")
    assert "fields  economics (core)" in out


def test_lookup_missing_and_bad_input(run):
    assert run("lookup", "arxiv:2999.00001")[0] == 1
    assert run("add", stdin="{not json")[0] == 2
    assert run("candidates", "--field", "nope")[0] == 2


def test_candidates_match_terms_and_first_seen(run, tmp_path):
    assert run("refresh", "--offline", "--json")[0] == 0
    code, out = run("candidates", "--since", "2026-01-01", "--json", "--limit", "500")
    items = json.loads(out)["items"]
    term = items[0]["title"].split()[0].lower()
    expected = {
        it["work_id"]
        for it in items
        if f" {term}" in f" {it['title'].lower()}" or f" {term}" in f" {(it['abstract'] or '').lower()}"
    }

    terms = tmp_path / "terms.txt"
    terms.write_text(f"{term}\n\nno such phrase anywhere\n")
    code, out = run("candidates", "--since", "2026-01-01", "--json", "--limit", "500", "--match-file", str(terms))
    data = json.loads(out)
    assert code == 0 and {it["work_id"] for it in data["items"]} == expected
    assert all(f"match:{term}" in it["reasons"] for it in data["items"])
    assert data["match"] == [term, "no such phrase anywhere"]

    code, out = run("candidates", "--since", "2026-01-01", "--json", "--match", "no such phrase anywhere")
    assert json.loads(out)["items"] == []

    first_seen = max(it["first_seen"] for it in items)
    code, out = run("candidates", "--since", "2026-01-01", "--json", "--limit", "500", "--first-seen-since", first_seen)
    assert {it["first_seen"] for it in json.loads(out)["items"]} == {first_seen}
    code, out = run("candidates", "--since", "2026-01-01", "--json", "--first-seen-since", "2999-01-01T00:00:00-05:00")
    assert json.loads(out)["items"] == []
    assert run("candidates", "--json", "--first-seen-since", "not a date")[0] == 2


def test_doctor_json_reports_sources_and_last_success(run):
    code, out = run("doctor", "--json")
    before = json.loads(out)
    assert before["counts"] is None and before["last_success"] is None and before["sources"] == []
    assert run("refresh", "--offline", "--json")[0] == 0
    code, out = run("doctor", "--json")
    report = json.loads(out)
    assert report["counts"]["works"] > 10 and report["fields"]["physics"] == "mute"
    ok = [s for s in report["sources"] if s["enabled"] and s["status"] == "ok"]
    assert ok and report["last_success"] == max(s["last_success"] for s in report["sources"] if s["last_success"])
    assert code == (1 if report["problems"] else 0) and report["ok"] == (not report["problems"])
