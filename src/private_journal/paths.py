"""XDG data and configuration path helpers."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "private-journal"


def get_xdg_data_dir() -> Path:
    """Return the XDG data directory for private-journal."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


def get_xdg_config_dir() -> Path:
    """Return the XDG config directory for private-journal."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def get_config_path() -> Path:
    """Return the path to config.json."""
    return get_xdg_config_dir() / "config.json"


def get_xdg_runtime_dir() -> Path:
    """Return the XDG runtime directory for private-journal."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / APP_NAME
    import tempfile

    return Path(tempfile.gettempdir()) / f"{APP_NAME}-{os.getuid()}"
