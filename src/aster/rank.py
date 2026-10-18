"""Candidates: recent works in the active fields, ranked by explainable, deterministic features.

Nothing here judges quality or relevance the way a reader would; it orders what the agent looks
at first and says why (`reasons`). Features:

- field tier: core fields count fully, skim fields half; muted fields are left out
- keywords: field keyword phrases found in the title or abstract
- interests: BM25 similarity of title and abstract to the user's `interests`
- follows: a followed author, lab, Hugging Face organization or repository
- signals: recorded discussion (posts, threads, upvotes) linked to the work
- search-only: a work seen only through OpenAlex keyword searches ranks lower, because those
  searches reach any outlet, not just the fields' own sources

`match` phrases (from a caller such as a knowledge-base gardener) filter rather than rank: a
work is kept only when one appears in its title or abstract, and each hit is listed in
`reasons`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from aster.config import Follows
from aster.context import Context
from aster.fields import TIER_WEIGHT, contains, keyword_fields
from aster.records import Shown, Signal, Work

_STOP = set(
    "a an and are as at be by for from has have in into is it its of on or that the their this to was were which "
    "with we our via using use based new approach approaches method methods paper study results show propose "
    "proposed under over can how when what between".split()
)

W_KEYWORD = 0.2  # per distinct keyword, up to MAX_KEYWORDS
MAX_KEYWORDS = 3
W_INTEREST = 1.0
W_FOLLOW = 1.5
W_SIGNAL = 0.3  # per signal, up to MAX_SIGNALS
MAX_SIGNALS = 5
W_SEARCH_ONLY = -0.5
SEARCH_ONLY_SOURCES = ("openalex|",)


@dataclass
class Candidate:
    work: Work
    fields: list[str]
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    shown: bool = False


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower()) if t not in _STOP and len(t) > 1]


def work_text(w: Work) -> str:
    return f"{w.title}\n{w.abstract or ''}"


def work_date(w: Work) -> str:
    return w.released_date or w.published_date or w.first_seen[:10]


def interest_scores(works: list[Work], interests: list[str]) -> dict[str, float]:
    """work_id -> BM25 score relative to the best match in this set, in [0, 1]."""
    query = tokenize(" ".join(interests))
    if not works or not query:
        return {}
    bm25 = BM25Okapi([tokenize(work_text(w)) or ["_empty_"] for w in works])
    scores = bm25.get_scores(query)
    best = max(scores)
    if best <= 0:
        return {}
    return {w.work_id: float(s / best) for w, s in zip(works, scores, strict=True) if s > 0}


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def follow_hits(w: Work, follows: Follows) -> list[str]:
    authors = {_norm_name(a) for a in w.authors}
    text = work_text(w).lower()
    links = " ".join([*w.urls.values(), *w.ids.values()]).lower()
    hf_id = w.ids.get("hf", "").lower()
    hits = [f"author:{a}" for a in follows.authors if _norm_name(a) and _norm_name(a) in authors]
    hits += [f"lab:{lab}" for lab in follows.labs if lab.lower() in text]
    hits += [f"hf:{o}" for o in follows.hf_orgs if hf_id.startswith(f"{o.lower()}/")]
    hits += [f"repo:{r}" for r in follows.repos if r.lower() in links]
    return hits


def candidates(
    ctx: Context,
    since: str,
    *,
    until: str | None = None,
    field_id: str | None = None,
    limit: int | None = None,
    include_shown: bool = False,
    match: list[str] | None = None,
    first_seen_since: str | None = None,
) -> list[Candidate]:
    cfg = ctx.config
    active = {f.id: f for f in cfg.active_fields()}
    if field_id and field_id not in cfg.fields:
        raise ValueError(f"unknown field {field_id!r}; choose from {', '.join(cfg.fields)}")
    works = [w for w in ctx.store.all(Work) if work_date(w) >= since and (not until or work_date(w) <= until)]
    if first_seen_since:
        works = [w for w in works if w.first_seen >= first_seen_since]
    phrases = list(dict.fromkeys(p.strip() for p in match or [] if p.strip()))
    shown = {s.work_id for s in ctx.store.all(Shown)}
    signals: dict[str, list[Signal]] = {}
    for s in ctx.store.all(Signal, "json_extract(json, '$.work_id') IS NOT NULL"):
        signals.setdefault(s.work_id or "", []).append(s)
    follows = cfg.follows
    interests = interest_scores(works, cfg.interests)

    out: list[Candidate] = []
    for w in works:
        if w.work_id in shown and not include_shown:
            continue
        kw_hits = keyword_fields(list(active.values()), w.title, w.abstract or "")
        fields = [f for f in w.fields if f in active]
        for fid, _ in kw_hits:
            if fid not in fields:
                fields.append(fid)
        hits = follow_hits(w, follows)
        if field_id and field_id not in fields:
            continue
        if not fields and not hits:
            continue
        matched = [p for p in phrases if contains(w.title, p) or contains(w.abstract or "", p)]
        if phrases and not matched:
            continue
        c = Candidate(work=w, fields=fields, signals=signals.get(w.work_id, []), shown=w.work_id in shown)
        if fields:
            c.score += max(TIER_WEIGHT[active[f].tier] for f in fields)
            c.reasons.append(f"fields:{','.join(fields)}")
        kws = list(dict.fromkeys(kw for _, kw in kw_hits))[:MAX_KEYWORDS]
        if kws:
            c.score += W_KEYWORD * len(kws)
            c.reasons += [f"keyword:{k}" for k in kws]
        if rel := interests.get(w.work_id):
            c.score += W_INTEREST * rel
            c.reasons.append(f"interest:{rel:.2f}")
        if hits:
            c.score += W_FOLLOW
            c.reasons += [f"follow:{h}" for h in hits]
        if c.signals:
            c.score += W_SIGNAL * min(len(c.signals), MAX_SIGNALS)
            c.reasons.append(f"signals:{len(c.signals)}")
        if w.source_keys and all(k.startswith(SEARCH_ONLY_SOURCES) for k in w.source_keys) and not hits:
            c.score += W_SEARCH_ONLY
            c.reasons.append("search-only:openalex")
        c.reasons += [f"match:{p}" for p in matched]
        out.append(c)
    # Highest score first; among equal scores, newest first; then by id for a stable order.
    out.sort(key=lambda c: c.work.work_id)
    out.sort(key=lambda c: work_date(c.work), reverse=True)
    out.sort(key=lambda c: round(c.score, 6), reverse=True)
    return out[:limit] if limit else out


def signal_json(s: Signal) -> dict:
    return {
        k: v
        for k, v in {
            "platform": s.platform,
            "url": s.url,
            "title": s.title,
            "author": s.author,
            "posted_at": s.posted_at,
            "metrics": s.metrics or None,
            "origin": s.origin,
        }.items()
        if v
    }


def candidate_json(c: Candidate) -> dict:
    w = c.work
    return {
        "work_id": w.work_id,
        "title": w.title,
        "authors": w.authors[:10],
        "abstract": w.abstract,
        "kind": w.kind,
        "status": w.status,
        "venue": w.venue,
        "date": work_date(w),
        "first_seen": w.first_seen,
        "ids": w.ids,
        "urls": w.urls,
        "fields": c.fields,
        "score": round(c.score, 3),
        "reasons": c.reasons,
        "signals": [signal_json(s) for s in c.signals],
        "shown": c.shown,
    }
