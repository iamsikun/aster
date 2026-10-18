"""Configuration: packaged defaults and fields, deep-merged with the user's config file."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField

from aster import paths
from aster.fields import Feed, Field, Journal

ASSETS = Path(__file__).with_name("assets")


class ConfigError(Exception):
    pass


class Follows(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authors: list[str] = PydField(default_factory=list)
    labs: list[str] = PydField(default_factory=list)
    hf_orgs: list[str] = PydField(default_factory=list)
    repos: list[str] = PydField(default_factory=list)


def deep_merge(base: dict, override: dict) -> dict:
    """Merge `override` into `base`: dicts merge, other values replace, None removes the key."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


@dataclass
class Config:
    path: Path
    data_dir: Path
    data: dict[str, Any]
    fields: dict[str, Field] = field(default_factory=dict)
    journals: dict[str, Journal] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return default if cur is None else cur

    def active_fields(self) -> list[Field]:
        return [f for f in self.fields.values() if f.active]

    @property
    def follows(self) -> Follows:
        return Follows.model_validate(self.get("follows", {}))

    @property
    def interests(self) -> list[str]:
        return [str(i) for i in self.get("interests", []) if str(i).strip()]

    def feeds(self) -> list[Feed]:
        """Feeds of active fields (labelled with the field) plus the user's own feeds."""
        out: dict[str, Feed] = {}
        for f in self.active_fields():
            for feed in f.feeds:
                cur = out.setdefault(feed.url, feed.model_copy(update={"fields": []}))
                if f.id not in cur.fields:
                    cur.fields.append(f.id)
        for raw in self.get("feeds", []):
            feed = Feed.model_validate(raw)
            cur = out.setdefault(feed.url, feed)
            cur.fields = list(dict.fromkeys([*cur.fields, *feed.fields]))
        return list(out.values())


def _journals(user: list) -> dict[str, Journal]:
    out = {j["id"]: Journal.model_validate(j) for j in load_yaml(ASSETS / "journals.yaml").get("journals", [])}
    for raw in user or []:
        j = Journal.model_validate(raw)
        out[j.id] = j
    return out


def load_config(config_path: str | Path | None = None, data_dir: str | Path | None = None) -> Config:
    path = Path(config_path).expanduser() if config_path else paths.config_file()
    base = load_yaml(ASSETS / "defaults.yaml")
    base["fields"] = load_yaml(ASSETS / "fields.yaml").get("fields", {})
    user = {k: v for k, v in load_yaml(path).items() if v is not None}  # an empty section means unset
    merged = deep_merge(base, user)
    try:
        fields = {
            fid: Field.model_validate({**(spec or {}), "id": fid}) for fid, spec in (merged.get("fields") or {}).items()
        }
        journals = _journals(merged.get("journals") or [])
    except Exception as e:
        raise ConfigError(f"{path}: {e}") from e
    unknown = sorted({j for f in fields.values() for j in f.journals if j not in journals})
    if unknown:
        raise ConfigError(f"{path}: unknown journal ids {', '.join(unknown)}; add them under `journals:`")
    if tz := merged.get("timezone"):
        os.environ.setdefault("ASTER_TZ", tz)
    d = Path(data_dir).expanduser() if data_dir else paths.data_dir()
    return Config(path=path, data_dir=d, data=merged, fields=fields, journals=journals)
