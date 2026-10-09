"""Technology releases: GitHub releases, Hugging Face models, and research-blog feeds.

Watched items come from <data-dir>/watchlist.yaml (`repos`, `hf_orgs`, `blogs`) and each
profile's `watch`. arXiv ids mentioned by a release are kept in `extra.related_arxiv` rather
than `ids`, so a model release links to its paper without being merged into it.
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import Any

import feedparser

from lark.context import RunContext
from lark.records import SourceRecord
from lark.sources._common import fetch_json, fetch_text, iso_date, run_date, strip_markup
from lark.sources.base import FetchResult
from lark.utils.text import normalize_arxiv_id, short_hash

GH_API = "https://api.github.com/repos/{repo}/releases"
HF_API = "https://huggingface.co/api/models"
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)


def _related_arxiv(*texts: str | None) -> list[str]:
    found = []
    for t in texts:
        for m in ARXIV_RE.finditer(t or ""):
            base, _ = normalize_arxiv_id(m.group(1))
            if base and base not in found:
                found.append(base)
    return found


def watch_lists(ctx: RunContext) -> tuple[list[str], list[str], list[dict]]:
    wl = ctx.config.data_file("watchlist.yaml") or {}
    repos = list(wl.get("repos", []) or [])
    orgs = list(wl.get("hf_orgs", []) or [])
    for p in ctx.config.profiles:
        repos += p.watch.repos
        orgs += p.watch.hf_orgs
    blogs = [b if isinstance(b, dict) else {"name": b, "url": b} for b in wl.get("blogs", []) or []]
    return list(dict.fromkeys(repos)), list(dict.fromkeys(orgs)), blogs


def parse_github(repo: str, releases: list[dict]) -> list[SourceRecord]:
    out = []
    for r in releases or []:
        if r.get("draft"):
            continue
        tag = r.get("tag_name") or str(r.get("id"))
        name = r.get("name") or tag
        body = (r.get("body") or "")[:3000]
        sid = f"{repo}@{tag}"
        out.append(
            SourceRecord(
                source="releases",
                source_id=f"github:{sid}",
                title=f"{repo.split('/')[-1]} {name}" if repo.split("/")[-1].lower() not in name.lower() else name,
                authors=[repo.split("/")[0]],
                abstract=strip_markup(body),
                ids={"github": sid},
                venue=f"GitHub · {repo}",
                status="release",
                kind="release",
                url=r.get("html_url"),
                released_date=iso_date(r.get("published_at") or r.get("created_at")),
                extra={"repo": repo, "tag": tag, "prerelease": bool(r.get("prerelease")),
                       "related_arxiv": _related_arxiv(body)},
            )
        )
    return out


def parse_hf(org: str, models: list[dict]) -> list[SourceRecord]:
    out = []
    for m in models or []:
        mid = m.get("id") or m.get("modelId")
        if not mid or m.get("private"):
            continue
        tags = m.get("tags") or []
        arx = [t.split(":", 1)[1] for t in tags if t.startswith("arxiv:")]
        desc_tags = [t for t in tags if ":" not in t][:12]
        out.append(
            SourceRecord(
                source="releases",
                source_id=f"hf:{mid}",
                title=mid,
                authors=[org],
                abstract=(f"Hugging Face model ({m.get('pipeline_tag') or 'model'}); tags: " + ", ".join(desc_tags))
                if desc_tags else None,
                ids={"hf": mid},
                venue="Hugging Face",
                status="release",
                kind="release",
                url=f"https://huggingface.co/{mid}",
                released_date=iso_date(m.get("createdAt")),
                extra={"pipeline_tag": m.get("pipeline_tag"), "library": m.get("library_name"),
                       "related_arxiv": _related_arxiv(*(f"arxiv:{a}" for a in arx))},
            )
        )
    return out


def parse_blog(blog: dict, text: str) -> list[SourceRecord]:
    out = []
    for e in feedparser.parse(text).entries:
        link = e.get("link") or e.get("id")
        title = strip_markup(e.get("title"))
        if not link or not title:
            continue
        summary = strip_markup(e.get("summary"))
        out.append(
            SourceRecord(
                source="releases",
                source_id=f"blog:{link}",
                title=title,
                authors=[a.get("name") for a in e.get("authors") or [] if a.get("name")] or [blog.get("name", "")],
                abstract=summary,
                ids={"blog": link},
                venue=blog.get("name"),
                status="release",
                kind="release",
                url=link,
                released_date=iso_date(e.get("published") or e.get("updated")),
                extra={"blog": blog.get("name"), "related_arxiv": _related_arxiv(summary, link)},
            )
        )
    return out


class ReleasesCollector:
    name = "releases"

    def fetch(self, ctx: RunContext, checkpoint: dict[str, Any]) -> FetchResult:
        repos, orgs, blogs = watch_lists(ctx)
        if not (repos or orgs or blogs):
            return FetchResult(self.name, "empty", checkpoint={**checkpoint, "since": ctx.run_date},
                               error="watchlist is empty")
        cfg = ctx.config.get("sources.releases", {}) or {}
        cutoff = (
            date.fromisoformat(checkpoint["since"]) - timedelta(days=int(cfg.get("overlap_days", 2)))
            if checkpoint.get("since")
            else run_date(ctx) - timedelta(days=int(cfg.get("first_run_days", 14)))
        ).isoformat()
        gh_headers = {"Accept": "application/vnd.github+json"}
        if os.getenv("GITHUB_TOKEN"):
            gh_headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        records: list[SourceRecord] = []
        errors: list[str] = []

        for repo in repos:
            try:
                data = fetch_json(ctx, self.name, GH_API.format(repo=repo), fixture=f"gh_{repo.replace('/', '__')}.json",
                                  params={"per_page": 10}, headers=gh_headers)
                records += parse_github(repo, data if isinstance(data, list) else [])
            except Exception as e:
                errors.append(f"github {repo}: {e}")
        for org in orgs:
            try:
                data = fetch_json(ctx, self.name, HF_API, fixture=f"hf_{org}.json",
                                  params={"author": org, "sort": "createdAt", "direction": -1, "limit": 30})
                records += parse_hf(org, data if isinstance(data, list) else [])
            except Exception as e:
                errors.append(f"hf {org}: {e}")
        for blog in blogs:
            url = blog.get("url")
            if not url:
                continue
            try:
                text = fetch_text(ctx, self.name, url, fixture=f"blog_{short_hash(url, n=8)}.xml")
                records += parse_blog(blog, text or "")
            except Exception as e:
                errors.append(f"blog {blog.get('name')}: {e}")

        records = [r for r in records if not r.released_date or r.released_date >= cutoff]
        n_sources = len(repos) + len(orgs) + len(blogs)
        if errors and len(errors) == n_sources:
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if errors else ("ok" if records else "empty")
        return FetchResult(self.name, status, records=records, checkpoint={**checkpoint, "since": ctx.run_date},
                           error="; ".join(errors)[:500] or None)
