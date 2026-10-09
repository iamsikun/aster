"""Persistent job queue on top of the Store. Jobs over a budget are deferred, never dropped."""

from __future__ import annotations

from typing import Any

from lark.records import Job, JobKind
from lark.store import Store
from lark.utils.text import short_hash
from lark.utils.timez import iso_now


def job_id_for(kind: str, work_id: str | None, discriminator: str = "") -> str:
    return f"j_{kind}_{short_hash(kind, work_id or '', discriminator)}"


class JobQueue:
    def __init__(self, store: Store):
        self.store = store

    def enqueue(
        self,
        kind: JobKind,
        work_id: str | None = None,
        *,
        priority: int = 50,
        tag: str = "any",
        payload: dict[str, Any] | None = None,
        discriminator: str = "",
    ) -> Job:
        """Idempotent: re-enqueueing an existing unfinished job only raises its priority."""
        jid = job_id_for(kind, work_id, discriminator)
        existing = self.store.get(Job, jid)
        if existing and existing.state in ("queued", "deferred", "running"):
            if priority > existing.priority:
                existing.priority = priority
                existing.updated_at = iso_now()
                self.store.append(existing)
            return existing
        if existing and existing.state == "done":
            return existing
        job = Job(job_id=jid, kind=kind, work_id=work_id, priority=priority, tag=tag, payload=payload or {})
        self.store.append(job)
        return job

    def pending(self, kind: JobKind | None = None, tag: str | None = None, limit: int | None = None) -> list[Job]:
        where = "json_extract(json, '$.state') IN ('queued', 'deferred')"
        params: tuple = ()
        if kind:
            where += " AND json_extract(json, '$.kind') = ?"
            params += (kind,)
        if tag:
            where += " AND json_extract(json, '$.tag') = ?"
            params += (tag,)
        jobs = self.store.all(Job, where, params, order="json_extract(json, '$.priority') DESC, ts ASC")
        now = iso_now()
        jobs = [j for j in jobs if not j.not_before or j.not_before <= now]
        return jobs[:limit] if limit else jobs

    def _set(self, job: Job, state: str, error: str | None = None) -> Job:
        job.state = state  # type: ignore[assignment]
        job.updated_at = iso_now()
        if error is not None:
            job.last_error = error[:500]
        self.store.append(job)
        return job

    def start(self, job: Job) -> Job:
        job.attempts += 1
        return self._set(job, "running")

    def done(self, job: Job) -> Job:
        return self._set(job, "done")

    def defer(self, job: Job, reason: str) -> Job:
        return self._set(job, "deferred", reason)

    def fail(self, job: Job, error: str) -> Job:
        """Retry later unless attempts are exhausted."""
        state = "failed" if job.attempts >= job.max_attempts else "queued"
        return self._set(job, state, error)

    def backlog_count(self) -> int:
        return self.store.count(Job, "json_extract(json, '$.state') IN ('queued', 'deferred')")
