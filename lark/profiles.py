"""Research profiles: what the user cares about, used for retrieval, screening, and connections."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SeedPaper(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    id: str | None = None  # doi:..., arxiv:..., ssrn:...
    abstract: str | None = None
    note: str | None = None


class Watch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authors: list[str] = Field(default_factory=list)
    labs: list[str] = Field(default_factory=list)
    venues: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)  # owner/name on GitHub
    hf_orgs: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    bucket: Literal["active", "methods", "exploration"] = "active"
    priority: Literal["high", "medium", "low"] = "medium"
    description: str = ""
    questions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    recognize: list[str] = Field(default_factory=list)  # concepts to recognize even without matching words
    seeds: list[SeedPaper] = Field(default_factory=list)
    negatives: list[SeedPaper] = Field(default_factory=list)  # superficially similar but irrelevant
    known: list[str] = Field(default_factory=list)  # papers/ideas already known
    watch: Watch = Field(default_factory=Watch)

    def query_text(self) -> str:
        """Text used for lexical/embedding retrieval."""
        parts = [self.name, self.description, *self.questions, *self.keywords, *self.recognize]
        parts += [s.title + " " + (s.abstract or "") for s in self.seeds]
        return "\n".join(p for p in parts if p)

    def brief(self) -> str:
        """Compact description for screening prompts."""
        lines = [f"[{self.id}] {self.name} (bucket={self.bucket}, priority={self.priority})"]
        if self.description:
            lines.append(self.description.strip())
        if self.questions:
            lines.append("Questions: " + " | ".join(self.questions))
        if self.recognize:
            lines.append("Recognize: " + "; ".join(self.recognize))
        if self.negatives:
            lines.append("Not relevant (superficially similar): " + "; ".join(n.title for n in self.negatives))
        return "\n".join(lines)


def load_profiles(folder: Path) -> list[Profile]:
    if not folder.exists():
        return []
    out = []
    for path in sorted(folder.glob("*.y*ml")):
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw.setdefault("id", path.stem)
        out.append(Profile.model_validate(raw))
    return out


def profiles_version(profiles: list[Profile]) -> str:
    blob = json.dumps([p.model_dump(mode="json") for p in profiles], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:10]
