"""Configuration management for private-journal."""

from __future__ import annotations

import json
import os
from pathlib import Path

from private_journal.daemon import SessionError, parse_timeout
from private_journal.paths import get_config_path, get_xdg_data_dir
from private_journal.setup import DEFAULT_UNLOCK_TIMEOUT

ALLOWED_CONFIG_KEYS = ("unlock_timeout", "data_dir")


class ConfigError(Exception):
    """Raised on invalid configuration keys or values."""


def load_config() -> dict[str, object]:
    """Load config.json with defaults."""
    cfg: dict[str, object] = {
        "data_dir": str(get_xdg_data_dir()),
        "unlock_timeout": DEFAULT_UNLOCK_TIMEOUT,
        "totp_enabled": False,
    }
    config_path = get_config_path()
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        for key in ALLOWED_CONFIG_KEYS:
            if key in raw:
                cfg[key] = str(raw[key])
        if "totp_enabled" in raw:
            cfg["totp_enabled"] = bool(raw["totp_enabled"])
    return cfg


def get_config_value(key: str) -> str:
    """Return the value for a readable configuration key."""
    if key == "totp_enabled":
        return "true" if load_config().get("totp_enabled") else "false"
    if key not in ALLOWED_CONFIG_KEYS:
        raise ConfigError(f"Unknown config key: {key}")
    return str(load_config()[key])


def set_config_value(key: str, value: str) -> None:
    """Validate and persist a configuration key-value pair."""
    if key not in ALLOWED_CONFIG_KEYS:
        raise ConfigError(f"Unknown config key: {key}")
    if key == "unlock_timeout":
        try:
            parse_timeout(value)
        except SessionError as exc:
            raise ConfigError(str(exc)) from exc
    if key == "data_dir":
        if not value.strip():
            raise ConfigError("data_dir must not be empty.")
        value = str(Path(value).expanduser())

    cfg = load_config()
    cfg[key] = value
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.chmod(config_path, 0o600)
