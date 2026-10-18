"""Everything a command needs, passed explicitly."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from aster.config import Config, load_config
from aster.store import Store
from aster.utils.timez import now_local


@dataclass
class Context:
    config: Config
    store: Store
    offline: bool = False  # collectors read fixtures instead of the network
    fixtures: Path | None = None  # fixture folder for offline runs (tests)
    run_date: str = field(default_factory=lambda: now_local().strftime("%Y-%m-%d"))

    @property
    def data_dir(self) -> Path:
        return self.config.data_dir


def open_context(
    config_path: str | Path | None = None, data_dir: str | Path | None = None, *, rebuild: bool = False, **kw
) -> Context:
    cfg = load_config(config_path, data_dir)
    if kw.get("offline") and kw.get("fixtures") is None and os.getenv("ASTER_FIXTURES_DIR"):
        kw["fixtures"] = Path(os.environ["ASTER_FIXTURES_DIR"])
    return Context(config=cfg, store=Store(cfg.data_dir, rebuild=rebuild), **kw)
