from lark.jobs import JobQueue
from lark.records import Job, LedgerEntry, Work
from lark.store import Store


def test_keyed_latest_wins_and_rebuild(tmp_path):
    s = Store(tmp_path)
    s.append(Work(work_id="w1", title="First"))
    s.append(Work(work_id="w1", title="Second"))
    s.append(LedgerEntry(entry_id="e1", route="r", provider="p", billing="none", event="reserve"))
    s.append(LedgerEntry(entry_id="e2", route="r", provider="p", billing="none", event="settle"))
    assert s.get(Work, "w1").title == "Second"
    assert s.count(LedgerEntry) == 2
    s.close()

    s2 = Store(tmp_path, rebuild=True)
    assert s2.get(Work, "w1").title == "Second"
    assert s2.count(Work) == 1
    assert s2.count(LedgerEntry) == 2
    assert list(tmp_path.glob("data/*/*/*/works.*.jsonl"))


def test_queue_is_idempotent_and_defers(tmp_path):
    q = JobQueue(Store(tmp_path))
    a = q.enqueue("read", "w1", priority=10)
    b = q.enqueue("read", "w1", priority=90)
    assert a.job_id == b.job_id and b.priority == 90
    q.enqueue("read", "w2", priority=50)
    assert [j.work_id for j in q.pending()] == ["w1", "w2"]
    job = q.start(q.pending()[0])
    q.defer(job, "budget")
    assert q.backlog_count() == 2
    q.done(q.store.get(Job, job.job_id))
    assert q.backlog_count() == 1
    assert q.enqueue("read", "w1").state == "done"
