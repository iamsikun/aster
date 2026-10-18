"""Research fields: what to collect, how to label items, and where the agent should search.

A field names its sources (arXiv categories, journals, NEP reports, venues, feeds, Hugging Face
organizations) and a few keywords. Collectors label each record with the fields whose sources
produced it; keywords label items from any source.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField

Tier = Literal["core", "skim", "mute"]
TIER_WEIGHT: dict[str, float] = {"core": 1.0, "skim": 0.5, "mute": 0.0}


class Feed(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    url: str
    kind: Literal["paper", "release"] = "release"
    fields: list[str] = PydField(default_factory=list)


class SearchHints(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sites: list[str] = PydField(default_factory=list)
    accounts: list[str] = PydField(default_factory=list)
    queries: list[str] = PydField(default_factory=list)


class Field(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    tier: Tier = "core"
    description: str = ""
    arxiv: list[str] = PydField(default_factory=list)
    journals: list[str] = PydField(default_factory=list)
    nep: list[str] = PydField(default_factory=list)
    nber: bool = False
    openreview: list[str] = PydField(default_factory=list)
    feeds: list[Feed] = PydField(default_factory=list)
    hf_orgs: list[str] = PydField(default_factory=list)
    keywords: list[str] = PydField(default_factory=list)
    search: SearchHints = PydField(default_factory=SearchHints)

    @property
    def active(self) -> bool:
        return self.tier != "mute"

    @property
    def label(self) -> str:
        return self.name or self.id


class Journal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    area: str = ""
    issn: list[str] = PydField(default_factory=list)


def _add(index: dict[str, list[str]], key: str, field_id: str) -> None:
    ids = index.setdefault(key, [])
    if field_id not in ids:
        ids.append(field_id)


def arxiv_index(fields: list[Field]) -> dict[str, list[str]]:
    """arXiv category or archive -> field ids."""
    out: dict[str, list[str]] = {}
    for f in fields:
        for cat in f.arxiv:
            _add(out, cat, f.id)
    return out


def fields_for_categories(index: dict[str, list[str]], categories: list[str]) -> list[str]:
    """Fields for an item's arXiv categories; an archive entry (q-fin) covers its subcategories."""
    out: list[str] = []
    for cat in categories:
        for key in (cat, cat.split(".")[0]):
            for fid in index.get(key, []):
                if fid not in out:
                    out.append(fid)
    return out


def journal_index(fields: list[Field]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in fields:
        for jid in f.journals:
            _add(out, jid, f.id)
    return out


def nep_index(fields: list[Field]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in fields:
        for report in f.nep:
            _add(out, report, f.id)
    return out


def venue_index(fields: list[Field]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in fields:
        for venue in f.openreview:
            _add(out, venue, f.id)
    return out


def contains(text: str, phrase: str) -> bool:
    return f" {phrase.lower()}" in f" {' '.join(text.lower().split())} "


def keyword_fields(fields: list[Field], title: str, abstract: str = "") -> list[tuple[str, str]]:
    """(field id, keyword) pairs that place an item in a field.

    One keyword in the title is enough; in the abstract alone it takes two distinct keywords of
    the same field, so a passing mention does not move an item into a field.
    """
    out = []
    for f in fields:
        in_title = [kw for kw in f.keywords if contains(title, kw)]
        in_abstract = [kw for kw in f.keywords if kw not in in_title and contains(abstract, kw)]
        if in_title or len(in_abstract) >= 2:
            out += [(f.id, kw) for kw in [*in_title, *in_abstract]]
    return out
