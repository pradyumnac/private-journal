"""Tests for Feature: First-run Setup & Vault Initialization."""

import json

import pytest

from private_journal.setup import SetupError, initialize_vault


# Traceability: Feature "First-run Setup & Vault Initialization"
# Scenario: Initialize a new vault in the default XDG path
def test_initialize_vault_default_xdg(tmp_path, monkeypatch):
    xdg_data = tmp_path / "xdg_data"
    xdg_config = tmp_path / "xdg_config"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    password = b"strong-passphrase-123"
    result = initialize_vault(password=password)

    vault_dir = xdg_data / "private-journal"
    vault_meta_path = vault_dir / "vault.json"
    assert vault_meta_path.exists()
    assert result.totp_uri.startswith("otpauth://totp/")
    assert len(result.totp_secret) >= 16

    meta = json.loads(vault_meta_path.read_text(encoding="utf-8"))
    assert "salt" in meta
    assert "encrypted_totp" in meta

    # No plaintext password or plaintext TOTP secret on disk
    disk_text = vault_meta_path.read_text(encoding="utf-8")
    assert "strong-passphrase-123" not in disk_text
    assert result.totp_secret not in disk_text


# Traceability: Feature "First-run Setup & Vault Initialization"
# Scenario: Initialize a new vault in a custom data path
def test_initialize_vault_custom_path(tmp_path, monkeypatch):
    xdg_config = tmp_path / "xdg_config"
    custom_data = tmp_path / "custom_vault"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

    result = initialize_vault(password=b"custom-pass-456", data_dir=custom_data)

    assert (custom_data / "vault.json").exists()
    assert result.data_dir == custom_data

    config_path = xdg_config / "private-journal" / "config.json"
    assert config_path.exists()
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["data_dir"] == str(custom_data)
    assert cfg["unlock_timeout"] == "15m"


# Traceability: Feature "First-run Setup & Vault Initialization"
# Scenario: Reject re-initialization of an existing vault
def test_reject_reinitialization(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    custom_data = tmp_path / "vault"

    initialize_vault(password=b"first-pass", data_dir=custom_data)
    original_meta = (custom_data / "vault.json").read_bytes()

    with pytest.raises(SetupError):
        initialize_vault(password=b"second-pass", data_dir=custom_data)

    assert (custom_data / "vault.json").read_bytes() == original_meta


# Traceability: Feature "First-run Setup & Vault Initialization"
# Scenario: Reject weak or empty password during setup
def test_reject_empty_password(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    custom_data = tmp_path / "empty_pw_vault"

    with pytest.raises(SetupError):
        initialize_vault(password=b"", data_dir=custom_data)

    assert not (custom_data / "vault.json").exists()
