"""Hugging Face: new models from followed organizations, and the Daily Papers list.

Models come from the active fields' `hf_orgs` and `follows.hf_orgs`; a model's card is at its
URL. Daily Papers entries are arXiv papers: they merge with the arXiv record, and their upvote
and comment counts are recorded as signals of discussion.
"""

from __future__ import annotations

from typing import Any

from aster.context import Context
from aster.records import Signal, SourceRecord
from aster.sources._common import fetch_json, iso_date, related_arxiv, window_start
from aster.sources.base import FetchResult
from aster.utils.text import normalize_arxiv_id

MODELS_API = "https://huggingface.co/api/models"
DAILY_API = "https://huggingface.co/api/daily_papers"


def parse_models(org: str, models: list[dict], fields: list[str]) -> list[SourceRecord]:
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
                source="huggingface",
                source_id=mid,
                title=mid,
                authors=[org],
                abstract=(f"Hugging Face model ({m.get('pipeline_tag') or 'model'}); tags: " + ", ".join(desc_tags))
                if desc_tags
                else None,
                ids={"hf": mid},
                venue="Hugging Face",
                status="release",
                kind="release",
                url=f"https://huggingface.co/{mid}",
                released_date=iso_date(m.get("createdAt")),
                fields=list(fields),
                extra={
                    "pipeline_tag": m.get("pipeline_tag"),
                    "library": m.get("library_name"),
                    "related_arxiv": related_arxiv(*(f"arxiv:{a}" for a in arx)),
                },
            )
        )
    return out


def parse_daily(items: list[dict]) -> tuple[list[SourceRecord], list[Signal]]:
    records: list[SourceRecord] = []
    signals: list[Signal] = []
    for it in items or []:
        p = it.get("paper") or {}
        base, _ = normalize_arxiv_id(p.get("id"))
        title = (p.get("title") or it.get("title") or "").strip()
        if not base or not title:
            continue
        records.append(
            SourceRecord(
                source="hf_daily",
                source_id=base,
                title=title,
                authors=[a.get("name") for a in p.get("authors") or [] if a.get("name")],
                abstract=p.get("summary"),
                ids={"arxiv": base},
                venue="arXiv",
                status="preprint",
                url=f"https://arxiv.org/abs/{base}",
                released_date=iso_date(p.get("publishedAt")),
            )
        )
        metrics = {
            k: int(v)
            for k, v in (("upvotes", p.get("upvotes")), ("comments", it.get("numComments")))
            if isinstance(v, int)
        }
        signals.append(
            Signal(
                url=f"https://huggingface.co/papers/{base}",
                platform="huggingface",
                about={"arxiv": base},
                title=title,
                posted_at=iso_date(it.get("publishedAt")),
                metrics=metrics,
                origin="api",
            )
        )
    return records, signals


class HuggingFaceCollector:
    name = "huggingface"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        cfg = ctx.config.get("sources.huggingface", {}) or {}
        orgs: dict[str, list[str]] = {}
        for f in ctx.config.active_fields():
            for org in f.hf_orgs:
                orgs.setdefault(org, []).append(f.id)
        for org in ctx.config.follows.hf_orgs:
            orgs.setdefault(org, [])
        daily = bool(cfg.get("daily_papers", True))
        if not orgs and not daily:
            return FetchResult(
                self.name,
                "empty",
                checkpoint={**checkpoint, "since": ctx.run_date},
                error="no Hugging Face organizations and Daily Papers is off",
            )
        start = window_start(checkpoint, ctx, overlap_days=2, first_run_days=int(cfg.get("first_run_days", 14)))
        records: list[SourceRecord] = []
        signals: list[Signal] = []
        errors: list[str] = []
        for org, fields in orgs.items():
            try:
                data = fetch_json(
                    ctx,
                    self.name,
                    MODELS_API,
                    fixture=f"hf_{org}.json",
                    params={"author": org, "sort": "createdAt", "direction": -1, "limit": 30},
                )
                records += parse_models(org, data if isinstance(data, list) else [], fields)
            except Exception as e:
                errors.append(f"{org}: {e}")
        records = [r for r in records if not r.released_date or r.released_date >= start]
        if daily:
            try:
                data = fetch_json(ctx, self.name, DAILY_API, fixture="daily_papers.json", params={"limit": 100})
                recs, sigs = parse_daily(data if isinstance(data, list) else [])
                records += recs
                signals += sigs
            except Exception as e:
                errors.append(f"daily papers: {e}")
        n_parts = len(orgs) + int(daily)
        if errors and len(errors) == n_parts:
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if errors else ("ok" if records else "empty")
        return FetchResult(
            self.name,
            status,
            records=records,
            signals=signals,
            checkpoint={**checkpoint, "since": ctx.run_date},
            error="; ".join(errors)[:500] or None,
        )
