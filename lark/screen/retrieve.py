"""Candidate retrieval: the UNION of several cheap routes, so different terminology still gets through.

Routes:
- bm25:        lexical match of title+abstract against each profile's query text
- embedding:   cosine similarity to profile text and seed papers (fastembed, CPU; optional)
- watchlist:   followed authors, labs, venues, repos (profiles + watchlist.yaml)
- exploration: a small seeded random sample of everything else

Nothing here decides relevance; it only decides what the screener looks at.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from lark.context import RunContext
from lark.profiles import Profile
from lark.records import Work

log = logging.getLogger(__name__)

EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_STOP = set(
    "a an and are as at be by for from has have in into is it its of on or that the their this to was were which "
    "with we our via using use based new approach approaches method methods paper study results show propose "
    "proposed under over can how when what between".split()
)


@dataclass
class Candidate:
    work_id: str
    score: float
    routes: set[str] = field(default_factory=set)
    profiles: set[str] = field(default_factory=set)
    no_abstract: bool = False


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower()) if t not in _STOP and len(t) > 1]


def work_text(w: Work) -> str:
    return f"{w.title}\n{w.abstract or ''}"


def _add(found: dict[str, Candidate], w: Work, score: float, route: str, profile: str | None) -> None:
    c = found.get(w.work_id)
    if c is None:
        c = found[w.work_id] = Candidate(work_id=w.work_id, score=0.0, no_abstract=not (w.abstract or "").strip())
    c.score = max(c.score, score)
    c.routes.add(route)
    if profile:
        c.profiles.add(profile)


# ---------------- BM25 ----------------
def bm25_matches(ctx: RunContext, works: list[Work], profiles: list[Profile]) -> list[tuple[str, str, float]]:
    """(work_id, profile_id, relative score in [0, 1])."""
    if not works or not profiles:
        return []
    top_k = int(ctx.config.get("screen.bm25_top_k", 200))
    min_rel = float(ctx.config.get("screen.bm25_min_rel", 0.2))
    corpus = [tokenize(work_text(w)) or ["_empty_"] for w in works]
    bm25 = BM25Okapi(corpus)
    out = []
    for p in profiles:
        query = tokenize(p.query_text())
        if not query:
            continue
        scores = bm25.get_scores(query)
        best = max(scores) if len(scores) else 0
        if best <= 0:
            continue
        ranked = sorted(range(len(works)), key=lambda i: (-scores[i], works[i].work_id))[:top_k]
        for i in ranked:
            rel = float(scores[i] / best)
            if scores[i] > 0 and rel >= min_rel:
                out.append((works[i].work_id, p.id, rel))
    return out


# ---------------- embeddings ----------------
_EMBEDDER = None


def _embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding  # optional extra

        _EMBEDDER = TextEmbedding(EMBED_MODEL)
    return _EMBEDDER


def embeddings_enabled(ctx: RunContext) -> bool:
    if os.getenv("LARK_NO_EMBED") or not ctx.config.get("screen.embeddings", True):
        return False
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


def _profile_vectors(ctx: RunContext, profiles: list[Profile]):
    """Profile + seed embeddings, cached under .cache keyed by content hash."""
    import numpy as np

    texts: list[tuple[str, str]] = []
    for p in profiles:
        texts.append((p.id, p.query_text()))
        texts += [(p.id, f"{s.title}\n{s.abstract or ''}") for s in p.seeds]
    key = hashlib.sha256(json.dumps([EMBED_MODEL, texts]).encode()).hexdigest()[:16]
    path = ctx.data_dir / ".cache" / f"profile_emb_{key}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        return list(z["ids"]), z["vecs"]
    vecs = np.array(list(_embedder().embed([t for _, t in texts])), dtype="float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, ids=np.array([i for i, _ in texts]), vecs=vecs)
    return [i for i, _ in texts], vecs


def embedding_matches(ctx: RunContext, works: list[Work], profiles: list[Profile]) -> list[tuple[str, str, float]]:
    if not works or not profiles or not embeddings_enabled(ctx):
        return []
    import numpy as np

    try:
        pids, pvecs = _profile_vectors(ctx, profiles)
        wvecs = np.array(list(_embedder().embed([work_text(w) for w in works])), dtype="float32")
    except Exception as e:  # model download or runtime failure: degrade to BM25 only
        log.warning("embedding retrieval disabled for this run: %s", e)
        return []
    wvecs /= np.linalg.norm(wvecs, axis=1, keepdims=True) + 1e-9
    sims = wvecs @ pvecs.T  # works x profile-texts
    threshold = float(ctx.config.get("screen.embed_threshold", 0.62))
    top_k = int(ctx.config.get("screen.embed_top_k", 200))
    out = []
    for pid in sorted(set(pids)):
        cols = [j for j, x in enumerate(pids) if x == pid]
        best = sims[:, cols].max(axis=1)
        ranked = sorted(range(len(works)), key=lambda i: (-best[i], works[i].work_id))[:top_k]
        out += [(works[i].work_id, pid, float(best[i])) for i in ranked if best[i] >= threshold]
    return out


# ---------------- watchlist ----------------
def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def watch_matches(ctx: RunContext, works: list[Work], profiles: list[Profile]) -> list[tuple[str, str | None]]:
    """(work_id, profile_id or None for the global watchlist)."""
    global_watch = ctx.config.data_file("watchlist.yaml") or {}
    watchers: list[tuple[str | None, dict]] = [(p.id, p.watch.model_dump()) for p in profiles]
    watchers.append((None, {k: global_watch.get(k, []) or [] for k in ("authors", "labs", "venues", "repos", "hf_orgs")}))
    out = []
    for w in works:
        authors = {_norm_name(a) for a in w.authors}
        text = f"{w.title} {w.abstract or ''}".lower()
        venue = (w.venue or "").lower()
        links = " ".join([*w.urls.values(), *w.ids.values()]).lower()
        for pid, watch in watchers:
            hit = (
                any(_norm_name(a) in authors for a in watch.get("authors", []))
                or any(lab.lower() in text for lab in watch.get("labs", []))
                or any(v.lower() == venue or (len(v) > 6 and v.lower() in venue) for v in watch.get("venues", []))
                or any(r.lower() in links for r in watch.get("repos", []))
                or any(f"huggingface.co/{o.lower()}/" in links for o in watch.get("hf_orgs", []))
            )
            if hit:
                out.append((w.work_id, pid))
    return out


# ---------------- union ----------------
def candidates(ctx: RunContext, works: list[Work]) -> list[Candidate]:
    profiles = ctx.config.profiles
    by_id = {w.work_id: w for w in works}
    found: dict[str, Candidate] = {}
    for wid, pid, rel in bm25_matches(ctx, works, profiles):
        _add(found, by_id[wid], rel, "bm25", pid)
    for wid, pid, sim in embedding_matches(ctx, works, profiles):
        _add(found, by_id[wid], sim, "embedding", pid)
    for wid, pid in watch_matches(ctx, works, profiles):
        _add(found, by_id[wid], 1.0, "watchlist", pid)

    # Works without abstracts only enter through a title match or watchlist (handled above);
    # they stay marked no_abstract so screening yields insufficient_info rather than skip.
    cap = int(ctx.config.get("limits.candidates_per_day", 1500))
    n_explore = int(ctx.config.get("limits.exploration_sample", 10))
    ranked = sorted(found.values(), key=lambda c: (-c.score, c.work_id))[: max(cap - n_explore, 0)]
    chosen = {c.work_id for c in ranked}

    rest = sorted(w.work_id for w in works if w.work_id not in found)
    rng = random.Random(f"lark-explore-{ctx.run_date}")
    explore = rng.sample(rest, min(n_explore, len(rest))) if rest else []
    for wid in sorted(explore):
        w = by_id[wid]
        c = Candidate(work_id=wid, score=0.0, routes={"exploration"}, no_abstract=not (w.abstract or "").strip())
        ranked.append(c)
        chosen.add(wid)
    return ranked[:cap]
