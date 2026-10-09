import math
from datetime import datetime
from .utils.timez import now_local
from .storage.db import upsert_signals

def _age_days(published_date: str | None) -> float:
    if not published_date:
        return 999.0
    try:
        dt = datetime.fromisoformat(published_date)
    except ValueError:
        try:
            dt = datetime.strptime(published_date, "%Y-%m-%d")
        except:
            return 999.0
    delta = now_local() - dt
    return max(delta.days, 0)

def score_item(item: dict, mentions_7d: int, recency_weight: float, mentions_weight: float, half_life_days: int):
    age = _age_days(item.get("published_date"))
    rec = math.exp(-math.log(2) * age / max(half_life_days, 1))
    m = math.log1p(max(0, mentions_7d))
    score = recency_weight * rec + mentions_weight * (m / math.log(2 + m))
    return score

def rank_and_store(items: list[dict], mentions_map: dict, cfg_rank: dict):
    for it in items:
        key = it["key"]
        mentions = 0
        doi = (it.get("doi") or "").lower()
        if doi: mentions = mentions_map.get(doi, 0)
        s = score_item(
            it, mentions,
            cfg_rank.get("recency_weight", 0.6),
            cfg_rank.get("mentions_7d_weight", 0.4),
            cfg_rank.get("age_half_life_days", 14),
        )
        upsert_signals({"key": key, "mentions_7d": mentions, "score": float(s)})
