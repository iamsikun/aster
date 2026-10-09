import os, logging
from ..collectors.arxiv import fetch_arxiv
from ..collectors.openalex import fetch_openalex
from ..collectors.crossref_events import fetch_mentions_7d
from ..storage.db import init_db, upsert_paper, top_scored
from ..ranker import rank_and_store
from ..delivery.notion import post_to_notion
from ..delivery.slack import post_to_slack

log = logging.getLogger(__name__)

def run_daily(config: dict):
    init_db()
    items = []

    if config.get("sources", {}).get("arxiv", {}).get("enabled"):
        acfg = config["sources"]["arxiv"]
        items += fetch_arxiv(acfg.get("categories", []), acfg.get("query", ""), acfg.get("max_results", 100))

    if config.get("sources", {}).get("openalex", {}).get("enabled"):
        ocfg = config["sources"]["openalex"]
        items += fetch_openalex(ocfg.get("filter", "from_publication_date:{today}"), ocfg.get("per_page", 100))

    for it in items:
        upsert_paper(it)

    mentions_map = {}
    if config.get("sources", {}).get("crossref_events", {}).get("enabled"):
        cfcfg = config["sources"]["crossref_events"]
        mentions_map = fetch_mentions_7d(cfcfg.get("lookback_days", 7), cfcfg.get("rows", 500))

    rank_and_store(items, mentions_map, config.get("ranking", {}))

    n_new = config.get("limits", {}).get("new_today", 20)
    n_trend = config.get("limits", {}).get("trending", 20)
    top = top_scored(limit=n_new + n_trend)
    rows = [{
        "title": t[1], "venue": t[2], "year": t[3],
        "url": t[4], "pdf_url": t[5], "score": round(float(t[6]), 3)
    } for t in top]

    if config.get("delivery", {}).get("notion", {}).get("enabled") and os.getenv("NOTION_DATABASE_ID"):
        post_to_notion(os.getenv("NOTION_DATABASE_ID"), rows[: n_new + n_trend])
    if config.get("delivery", {}).get("slack", {}).get("enabled"):
        post_to_slack(rows[: n_new + n_trend])

    log.info("Daily run complete: %d items processed, %d delivered", len(items), len(rows))
