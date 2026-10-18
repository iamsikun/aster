"""Where Aster keeps its configuration and data.

The configuration is one YAML file; the data folder holds the append-only log and its index.
Both live outside the installed code, so updating or reinstalling never touches them.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    value = os.getenv(var)
    return Path(value).expanduser() if value else Path.home() / fallback


def config_file() -> Path:
    if value := os.getenv("ASTER_CONFIG"):
        return Path(value).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "aster" / "config.yaml"


def data_dir() -> Path:
    # Not ~/.local/share/aster: the installer keeps the code there.
    if value := os.getenv("ASTER_DATA_DIR"):
        return Path(value).expanduser()
    return _xdg("XDG_DATA_HOME", ".local/share") / "aster-data"
