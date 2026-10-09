"""Feedback through GitHub issues on the private data repo.

Each report item carries links that open a prefilled "new issue" form. Submitting it
is the feedback; `sync()` turns open feedback issues into Feedback records and closes
them. This needs no server and works from any mail client.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlencode

import requests

from lark.context import RunContext
from lark.jobs import JobQueue
from lark.records import Feedback
from lark.report.contract import FeedbackAction

log = logging.getLogger(__name__)

API = "https://api.github.com"
VALUES = ("useful", "peripheral", "known", "wrong_topic", "missed_key", "deep_read")
LABELS = {
    "peripheral": "Interesting but peripheral",
    "known": "Already knew",
    "wrong_topic": "Wrong topic",
    "missed_key": "Summary missed the key idea",
    "deep_read": "Read in depth",
}
TITLE_RE = re.compile(r"^feedback:\s*(?P<value>[a-z_]+)\s+(?P<work_id>\S+)", re.I)
FIELD_RE = re.compile(r"^(?P<k>report_id|profile|work_id):\s*(?P<v>.*)$", re.M)


def _repo(ctx: RunContext) -> str:
    return ctx.config.get("feedback.repo", "iamsikun/lark-data")


def issue_url(repo: str, value: str, work_id: str, report_id: str, profile: str | None = None) -> str:
    body = (
        f"work_id: {work_id}\nreport_id: {report_id}\nprofile: {profile or ''}\n\n"
        "Optional note (anything below this line is kept as a note):\n"
    )
    q = urlencode({"title": f"feedback: {value} {work_id}", "labels": f"feedback,{value}", "body": body})
    return f"https://github.com/{repo}/issues/new?{q}"


def feedback_actions(ctx: RunContext, work_id: str, report_id: str, profiles: list[str]) -> list[FeedbackAction]:
    repo = _repo(ctx)
    actions = [
        FeedbackAction(label=f"Useful for {p}", url=issue_url(repo, "useful", work_id, report_id, p), value="useful")
        for p in (profiles or [""])
    ]
    if not profiles:
        actions[0].label = "Useful"
    for value, label in LABELS.items():
        actions.append(FeedbackAction(label=label, url=issue_url(repo, value, work_id, report_id), value=value))
    return actions


def parse_issue(issue: dict) -> Feedback | None:
    m = TITLE_RE.match(issue.get("title") or "")
    if not m or m.group("value").lower() not in VALUES:
        return None
    body = issue.get("body") or ""
    fields = {fm.group("k"): fm.group("v").strip() for fm in FIELD_RE.finditer(body)}
    note = body.split("kept as a note):", 1)[1].strip() if "kept as a note):" in body else None
    extra = {"at": issue["created_at"]} if issue.get("created_at") else {}
    return Feedback(
        feedback_id=f"gh-{issue['number']}",
        work_id=fields.get("work_id") or m.group("work_id"),
        report_id=fields.get("report_id") or None,
        label=m.group("value").lower(),
        profile=fields.get("profile") or None,
        note=note or None,
        origin="github_issue",
        **extra,
    )


def sync(ctx: RunContext) -> int:
    """Import open feedback issues, close them, and return the number imported."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        log.info("No GITHUB_TOKEN; skipping feedback sync")
        return 0
    repo = _repo(ctx)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    queue = JobQueue(ctx.store)
    n = 0
    page = 1
    while True:
        r = requests.get(
            f"{API}/repos/{repo}/issues",
            headers=headers,
            params={"state": "open", "labels": "feedback", "per_page": 100, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        issues = [i for i in r.json() if "pull_request" not in i]
        if not issues:
            break
        for issue in issues:
            fb = parse_issue(issue)
            comment = "Recorded by Lark." if fb else "Lark could not parse this feedback; closing."
            if fb:
                if ctx.store.get(Feedback, fb.feedback_id) is None:
                    ctx.store.append(fb)
                    n += 1
                if fb.label == "deep_read" and fb.work_id:
                    queue.enqueue("deep", fb.work_id, priority=90, payload={"origin": fb.feedback_id})
                    comment = "Recorded by Lark; a deep read has been queued."
            if not ctx.dry_run:
                url = f"{API}/repos/{repo}/issues/{issue['number']}"
                requests.post(f"{url}/comments", headers=headers, json={"body": comment}, timeout=30)
                requests.patch(url, headers=headers, json={"state": "closed", "state_reason": "completed"}, timeout=30)
        if len(issues) < 100:
            break
        page += 1
    return n
