import os
from datetime import timedelta
from ..utils.timez import now_local
from ..utils.http import get_json

def fetch_mentions_7d(lookback_days: int = 7, rows: int = 500):
    base = os.getenv("CROSSREF_EVENT_BASE", "https://api.eventdata.crossref.org/v1")
    url = f"{base}/events"
    frm = (now_local() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    params = {"from-occurred-date": frm, "rows": rows}
    data = get_json(url, params=params)
    counts = {}
    message = data.get("message") or {}
    for ev in message.get("events", []):
        obj = ev.get("obj_id") or ""
        doi = None
        if "doi.org/" in obj: doi = obj.split("doi.org/")[-1].lower()
        if not doi: continue
        counts[doi] = counts.get(doi, 0) + 1
    return counts
