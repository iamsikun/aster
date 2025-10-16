import os, logging
from ..utils.http import post_json

log = logging.getLogger(__name__)

def post_to_notion(database_id: str, rows: list[dict]):
    key = os.getenv("NOTION_API_KEY")
    if not key:
        log.warning("NOTION_API_KEY missing; skipping Notion delivery")
        return
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    for r in rows:
        props = {
            "Name": {"title": [{"text": {"content": r.get("title", "")}}]},
            "Venue": {"rich_text": [{"text": {"content": r.get("venue") or ""}}]},
            "Year": {"number": r.get("year")},
            "Score": {"number": r.get("score")},
            "URL": {"url": r.get("url")},
            "PDF": {"url": r.get("pdf_url")},
        }
        payload = {"parent": {"database_id": database_id}, "properties": props}
        try:
            post_json(url, payload, headers=headers)
        except Exception as e:
            log.warning("Failed Notion post: %s", e)
