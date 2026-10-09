"""Configuration: package defaults deep-merged with <data-dir>/lark.yaml, plus research profiles."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lark.profiles import Profile, load_profiles, profiles_version

DEFAULTS_PATH = Path(__file__).with_name("defaults.yaml")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    data_dir: Path
    data: dict[str, Any]
    profiles: list[Profile] = field(default_factory=list)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @property
    def profiles_version(self) -> str:
        return profiles_version(self.profiles)

    def data_file(self, name: str) -> dict:
        """Load an auxiliary YAML file from the data dir (journals.yaml, watchlist.yaml)."""
        return _load_yaml(self.data_dir / name)


def default_data_dir() -> Path:
    return Path(os.getenv("LARK_DATA_DIR", "lark-data")).expanduser()


def load_config(data_dir: str | Path | None = None) -> Config:
    d = Path(data_dir).expanduser() if data_dir else default_data_dir()
    merged = deep_merge(_load_yaml(DEFAULTS_PATH), _load_yaml(d / "lark.yaml"))
    if tz := merged.get("timezone"):
        os.environ.setdefault("LARK_TZ", tz)
    return Config(data_dir=d, data=merged, profiles=load_profiles(d / "profiles"))
