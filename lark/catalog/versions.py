"""Version tracking and revision triage.

A revision is classified before anything pays to reread it:
  new          first version Lark has seen
  trivial      whitespace or typo-level edit to title/abstract (< ~2% normalized difference)
  substantive  a real change to title/abstract, or a new venue version (e.g. journal vs preprint)
  withdrawn    the source marks the work withdrawn
  unknown      a new version exists but title/abstract are identical; the full text was not compared
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from lark.records import ChangeKind, SourceRecord, Version, Work
from lark.utils.text import text_hash

TRIVIAL_THRESHOLD = 0.02
# Aggregators re-describe works published elsewhere; their wording differences are not revisions.
AGGREGATOR_SOURCES = {"openalex", "nep"}
WITHDRAWN_RE = re.compile(r"\b(this (paper|article|submission) (has been|is) withdrawn|withdrawn by the author)", re.I)


def _norm(t: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t or "")).strip().lower()


def classify_change(old_text: str | None, new_text: str | None) -> ChangeKind:
    new_n = _norm(new_text)
    if WITHDRAWN_RE.search(new_n):
        return "withdrawn"
    old_n = _norm(old_text)
    if not old_n:
        return "new"
    if old_n == new_n:
        return "trivial"
    diff = 1.0 - SequenceMatcher(None, old_n, new_n, autojunk=False).ratio()
    return "trivial" if diff < TRIVIAL_THRESHOLD else "substantive"


def record_text(title: str | None, abstract: str | None) -> str:
    return f"{title or ''}\n{abstract or ''}"


def version_for(
    work: Work | None, rec: SourceRecord, known_version_ids: set[str], last_hash: str | None
) -> Version | None:
    """Return a new Version for `rec`, or None if this version is already known.

    `work` is the canonical work *before* merging `rec` (None for a brand-new work).
    """
    text = record_text(rec.title, rec.abstract)
    h = text_hash(text)
    if rec.version:
        vid = f"{rec.source}:{rec.version}"
    else:
        if h == last_hash:
            return None
        vid = f"{rec.source}:{h[:10]}"
    if vid in known_version_ids:
        return None
    if work is not None and rec.source in AGGREGATOR_SOURCES:
        return None
    if work is None:
        change: ChangeKind = "withdrawn" if rec.status == "withdrawn" else "new"
        return Version(work_id=rec.work_id or "", version_id=vid, text_hash=h, change=change)
    if rec.status == "withdrawn":
        return Version(work_id=work.work_id, version_id=vid, text_hash=h, change="withdrawn")
    old_text = record_text(work.title, work.abstract)
    change = classify_change(old_text, text)
    note = None
    if rec.version and change == "trivial" and text_hash(old_text) == h:
        change, note = "unknown", "title and abstract unchanged; full text not compared"
    elif not rec.abstract and not rec.version:
        change, note = "unknown", f"{rec.source} record has no abstract to compare"
    return Version(work_id=work.work_id, version_id=vid, text_hash=h, change=change, note=note)
