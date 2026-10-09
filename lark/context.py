"""Everything a pipeline stage needs, passed explicitly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lark.config import Config, load_config
from lark.store import Store
from lark.utils.timez import now_local


@dataclass
class RunContext:
    config: Config
    store: Store
    dry_run: bool = False  # no network sends (email etc.); still writes local state
    fake_llm: bool = False  # route every role to the fake worker
    offline: bool = False  # collectors read fixtures instead of the network
    run_date: str = field(default_factory=lambda: now_local().strftime("%Y-%m-%d"))

    @property
    def data_dir(self) -> Path:
        return self.config.data_dir


def make_context(data_dir: str | Path | None = None, **kw) -> RunContext:
    cfg = load_config(data_dir)
    return RunContext(config=cfg, store=Store(cfg.data_dir, rebuild=kw.pop("rebuild", False)), **kw)
