from urllib.parse import parse_qs, urlparse

from lark.feedback.issues import feedback_actions, issue_url, parse_issue, sync
from lark.jobs import JobQueue
from lark.records import Feedback


def test_feedback_urls(ctx):
    actions = feedback_actions(ctx, "w_abc", "daily-2026-10-09", ["selection-inference", "recsys-dynamics"])
    values = [a.value for a in actions]
    assert values.count("useful") == 2
    assert set(values) == {"useful", "peripheral", "known", "wrong_topic", "missed_key", "deep_read"}
    assert actions[0].label == "Useful for selection-inference"
    u = urlparse(actions[0].url)
    assert u.netloc == "github.com" and u.path == "/iamsikun/lark-data/issues/new"
    q = parse_qs(u.query)
    assert q["title"] == ["feedback: useful w_abc"]
    assert q["labels"] == ["feedback,useful"]
    assert "report_id: daily-2026-10-09" in q["body"][0] and "profile: selection-inference" in q["body"][0]


def _issue(number, value, work_id="w_abc", note="", profile="selection-inference"):
    url = issue_url("iamsikun/lark-data", value, work_id, "daily-2026-10-09", profile)
    body = parse_qs(urlparse(url).query)["body"][0] + note
    return {"number": number, "title": f"feedback: {value} {work_id}", "body": body,
            "created_at": "2026-10-09T15:00:00Z", "labels": [{"name": "feedback"}]}


def test_parse_issue():
    fb = parse_issue(_issue(7, "missed_key", note="It ignores the dependence assumption."))
    assert fb.feedback_id == "gh-7" and fb.label == "missed_key" and fb.work_id == "w_abc"
    assert fb.report_id == "daily-2026-10-09" and fb.profile == "selection-inference"
    assert fb.note == "It ignores the dependence assumption."
    assert parse_issue({"number": 1, "title": "unrelated bug", "body": ""}) is None
    assert parse_issue({"number": 2, "title": "feedback: love w_x", "body": ""}) is None


class _Resp:
    def __init__(self, data=None, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def test_sync_records_closes_and_queues_deep(ctx, monkeypatch):
    import requests

    issues = [_issue(1, "useful"), _issue(2, "deep_read", work_id="w_deep"), {"number": 3, "title": "junk", "body": ""},
              {"number": 4, "title": "feedback: known w_pr", "body": "", "pull_request": {}}]
    calls = {"post": [], "patch": []}
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(requests, "get", lambda url, **k: _Resp(issues if k["params"]["page"] == 1 else []))
    monkeypatch.setattr(requests, "post", lambda url, **k: calls["post"].append(url) or _Resp())
    monkeypatch.setattr(requests, "patch", lambda url, **k: calls["patch"].append((url, k["json"])) or _Resp())
    ctx.dry_run = False

    assert sync(ctx) == 2
    assert ctx.store.get(Feedback, "gh-1").label == "useful"
    assert [j.work_id for j in JobQueue(ctx.store).pending(kind="deep")] == ["w_deep"]
    assert len(calls["patch"]) == 3 and all(p[1]["state"] == "closed" for p in calls["patch"])

    assert sync(ctx) == 0  # already recorded


def test_sync_without_token_is_noop(ctx, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert sync(ctx) == 0
