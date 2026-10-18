from aster.records import SourceHealth, Work
from aster.store import Store


def test_keyed_latest_wins_and_rebuild(tmp_path):
    s = Store(tmp_path)
    s.append(Work(work_id="w1", title="First"))
    s.append(Work(work_id="w1", title="Second"))
    s.append(SourceHealth(source="arxiv", status="ok"))
    s.append(SourceHealth(source="arxiv", status="empty"))
    assert s.get(Work, "w1").title == "Second"
    assert s.count(SourceHealth) == 2
    s.close()

    s2 = Store(tmp_path, rebuild=True)
    assert s2.get(Work, "w1").title == "Second"
    assert s2.count(Work) == 1
    assert s2.count(SourceHealth) == 2
    assert list(tmp_path.glob("log/*/*/*/works.jsonl"))


def test_missing_index_is_rebuilt_from_the_log(tmp_path):
    s = Store(tmp_path)
    s.append(Work(work_id="w1", title="Kept"))
    s.close()
    (tmp_path / ".cache" / "index.sqlite").unlink()
    assert Store(tmp_path).get(Work, "w1").title == "Kept"
