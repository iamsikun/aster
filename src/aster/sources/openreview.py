"""OpenReview (API v2): submissions and decisions for configured venues.

A public submission, an acceptance, and a proceedings publication are different events, so the
note's venueid decides `status`: `<venue>/Submission` -> submitted, `<venue>` -> accepted,
`.../Rejected_Submission` or `.../Withdrawn_Submission` -> withdrawn/skipped.

Anonymous API access currently hits a bot challenge (HTTP 403 ChallengeRequiredError), so a
login (OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD) is effectively required.
Only (forum, status) pairs not seen before are emitted, so a venue isn't re-ingested daily.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import requests

from aster.context import Context
from aster.fields import venue_index
from aster.records import SourceRecord
from aster.sources._common import fetch_json, iso_date, strip_markup
from aster.sources.base import FetchResult
from aster.sources.ratelimit import LIMITER
from aster.utils.http import get_json, user_agent
from aster.utils.text import short_hash

API = "https://api2.openreview.net"


def _val(content: dict, key: str):
    v = content.get(key)
    return v.get("value") if isinstance(v, dict) else v


def status_for(venueid: str, venue: str) -> str | None:
    if venueid == venue:
        return "accepted"
    if venueid == f"{venue}/Submission":
        return "submitted"
    if venueid.endswith("Withdrawn_Submission") or venueid.endswith("Desk_Rejected_Submission"):
        return "withdrawn"
    if venueid.endswith("Rejected_Submission"):
        return None  # public rejected submissions are not news
    return None


def parse_note(note: dict, venue: str) -> SourceRecord | None:
    c = note.get("content") or {}
    venueid = _val(c, "venueid") or ""
    status = status_for(venueid, venue)
    title = strip_markup(_val(c, "title"))
    forum = note.get("forum") or note.get("id")
    if not status or not title or not forum:
        return None
    pdf = _val(c, "pdf")
    cdate = note.get("pdate") or note.get("cdate") or note.get("tcdate")
    released = None
    if isinstance(cdate, int | float):
        released = datetime.fromtimestamp(cdate / 1000, UTC).date().isoformat()
    return SourceRecord(
        source="openreview",
        source_id=forum,
        title=title,
        authors=list(_val(c, "authors") or []),
        abstract=strip_markup(_val(c, "abstract")),
        ids={"openreview": forum},
        venue=_val(c, "venue") or venue,
        status=status,  # type: ignore[arg-type]
        url=f"https://openreview.net/forum?id={forum}",
        pdf_url=f"https://openreview.net{pdf}" if isinstance(pdf, str) and pdf.startswith("/") else pdf,
        released_date=iso_date(released),
        extra={"venueid": venueid, "keywords": _val(c, "keywords")},
    )


def login() -> str | None:
    user, pw = os.getenv("OPENREVIEW_USERNAME"), os.getenv("OPENREVIEW_PASSWORD")
    if not (user and pw):
        return None
    LIMITER.wait(API)
    r = requests.post(
        f"{API}/login", json={"id": user, "password": pw}, timeout=30, headers={"User-Agent": user_agent()}
    )
    r.raise_for_status()
    return r.json().get("token")


class OpenReviewCollector:
    name = "openreview"

    def fetch(self, ctx: Context, checkpoint: dict[str, Any]) -> FetchResult:
        cfg = ctx.config.get("sources.openreview", {}) or {}
        index = venue_index(ctx.config.active_fields())
        venues: list[str] = list(dict.fromkeys([*index, *(cfg.get("venues", []) or [])]))
        if not venues:
            return FetchResult(self.name, "empty", checkpoint=checkpoint, error="no venues configured")
        headers = {}
        if not ctx.offline:
            try:
                token = login()
            except Exception as e:
                return FetchResult(self.name, "failed", checkpoint=checkpoint, error=f"login failed: {e}")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        limit = int(cfg.get("page_size", 1000))
        max_pages = int(cfg.get("max_pages", 10))
        seen: dict[str, dict[str, str]] = {k: dict(v) for k, v in (checkpoint.get("seen") or {}).items()}
        records: list[SourceRecord] = []
        errors: list[str] = []
        truncated = False
        for venue in venues:
            vseen = seen.setdefault(venue, {})
            for venueid in (f"{venue}/Submission", venue):
                for page in range(max_pages):
                    params = {"content.venueid": venueid, "limit": limit, "offset": page * limit}
                    fixture = f"{short_hash(venueid, n=8)}.json"
                    try:
                        if ctx.offline:
                            data = fetch_json(ctx, self.name, "", fixture=fixture)
                        else:
                            data = get_json(f"{API}/notes", params=params, headers=headers, limiter=LIMITER)
                            if isinstance(data, str):
                                data = json.loads(data)
                    except Exception as e:
                        msg = str(e)
                        if "Challenge" in msg or "403" in msg:
                            msg = "OpenReview requires login (set OPENREVIEW_USERNAME/OPENREVIEW_PASSWORD)"
                        errors.append(f"{venueid}: {msg}")
                        break
                    notes = (data or {}).get("notes") or []
                    for n in notes:
                        rec = parse_note(n, venue)
                        if rec and vseen.get(rec.source_id) != rec.status:
                            vseen[rec.source_id] = rec.status
                            rec.fields = list(index.get(venue, []))
                            records.append(rec)
                    if ctx.offline or len(notes) < limit:
                        break
                    if page == max_pages - 1:
                        truncated = True
        if errors and not records:
            return FetchResult(self.name, "failed", checkpoint=checkpoint, error="; ".join(errors)[:500])
        status = "partial" if (errors or truncated) else ("ok" if records else "empty")
        return FetchResult(
            self.name,
            status,
            records=records,
            checkpoint={**checkpoint, "seen": seen, "since": ctx.run_date},
            truncated=truncated,
            error="; ".join(errors)[:500] or None,
        )
