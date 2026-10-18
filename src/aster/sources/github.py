"""GitHub releases of followed repositories (`follows.repos`). Set GITHUB_TOKEN for higher limits."""

from __future__ import annotations

import os
from typing import Any

from aster.context import Context
from aster.records import SourceRecord
from aster.sources._common import fetch_json, iso_date, related_arxiv, strip_markup, window_start
from aster.sources.base import FetchResult

API = "https://api.github.com/repos/{repo}/releases"


def parse_releases(repo: str, releases: list[dict]) -> list[SourceRecord]:
    out = []
    name_part = repo.split("/")[-1]
    for r in releases or []:
        if r.get("draft"):
            continue
        tag = r.get("tag_name") or str(r.get("id"))
        name = r.get("name") or tag
        body = (r.get("body") or "")[:3000]
        sid = f"{repo}@{tag}"
        out.append(
            SourceRecord(
                source="github",
                source_id=sid,
                title=f"{name_part} {name}" if name_part.lower() not in name.lower() else name,
                authors=[repo.split("/")[0]],
                abstract=strip_markup(body),
                ids={"github": sid},
                venue=f"GitHub · {repo}",
                status="release",
                kind="release",
                url=r.get("html_url"),
                released_date=iso_date(r.get("published_at") or r.get("created_at")),
                extra={
                    "repo": repo,
                    "tag": tag,
                    "prerelease": bool(r.get("prerelease")),
                    "related_arxiv": related_arxiv(body),
                },
            )
        )
    return out


class GithubCollector:
    name = "github"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        repos = list(dict.fromkeys(ctx.config.follows.repos))
        if not repos:
            return FetchResult(
                self.name, "empty", checkpoint={**checkpoint, "since": ctx.run_date}, error="no followed repositories"
            )
        cfg = ctx.config.get("sources.github", {}) or {}
        start = window_start(checkpoint, ctx, overlap_days=2, first_run_days=int(cfg.get("first_run_days", 14)))
        headers = {"Accept": "application/vnd.github+json"}
        if os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        records: list[SourceRecord] = []
        errors: list[str] = []
        for repo in repos:
            try:
                data = fetch_json(
                    ctx,
                    self.name,
                    API.format(repo=repo),
                    fixture=f"gh_{repo.replace('/', '__')}.json",
                    params={"per_page": 10},
                    headers=headers,
                )
                records += parse_releases(repo, data if isinstance(data, list) else [])
            except Exception as e:
                errors.append(f"{repo}: {e}")
        records = [r for r in records if not r.released_date or r.released_date >= start]
        if errors and len(errors) == len(repos):
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if errors else ("ok" if records else "empty")
        return FetchResult(
            self.name,
            status,
            records=records,
            checkpoint={**checkpoint, "since": ctx.run_date},
            error="; ".join(errors)[:500] or None,
        )
