"""Tests for Feature: Config Management & Thin CLI (pj)."""

import json
from pathlib import Path

import pyotp
import pytest

from private_journal.cli import main
from private_journal.setup import initialize_vault


def _setup_cli_env(tmp_path, monkeypatch):
    xdg_config = tmp_path / "xdg_config"
    xdg_data = tmp_path / "xdg_data"
    xdg_runtime = tmp_path / "xdg_runtime"
    xdg_runtime.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))
    return xdg_config, xdg_data, xdg_runtime


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Show help and version without an unlocked session
def test_cli_help_and_version(capsys):
    with pytest.raises(SystemExit) as exc_help:
        main(["--help"])
    assert exc_help.value.code == 0
    assert "pj" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_ver:
        main(["--version"])
    assert exc_ver.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Trigger first-run setup on unlock when no vault exists
def test_cli_first_run_setup_on_unlock(tmp_path, monkeypatch, capsys):
    _, xdg_data, _ = _setup_cli_env(tmp_path, monkeypatch)

    prompts = iter(["init-pass-123", "init-pass-123"])
    monkeypatch.setattr("private_journal.cli.getpass.getpass", lambda _prompt="": next(prompts))

    captured_secret: list[str] = []

    def fake_input(prompt=""):
        # Capture secret from printed URI before returning TOTP code
        out = capsys.readouterr().out
        for token in out.split():
            if token.startswith("otpauth://"):
                secret = token.split("secret=")[1].split("&")[0]
                captured_secret.append(secret)
        return pyotp.TOTP(captured_secret[0]).now()

    monkeypatch.setattr("builtins.input", fake_input)

    try:
        rc = main(["unlock"])
        assert rc == 0
        assert (xdg_data / "private-journal" / "vault.json").exists()
        assert len(captured_secret) == 1
        assert main(["status"]) == 0
        assert "unlocked" in capsys.readouterr().out.lower()
    finally:
        main(["lock"])


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Reject protected CLI commands while locked
def test_cli_rejects_protected_commands_when_locked(tmp_path, monkeypatch, capsys):
    _setup_cli_env(tmp_path, monkeypatch)
    initialize_vault(password=b"locked-pass")

    for argv in (
        ["new"],
        ["edit", "abc"],
        ["view", "abc"],
        ["delete", "abc"],
        ["search", "--plain"],
        ["config", "get", "unlock_timeout"],
        ["config", "set", "unlock_timeout", "30m"],
    ):
        rc = main(argv)
        assert rc != 0
        err = capsys.readouterr().err
        assert "locked" in err.lower()


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Read and update configuration when unlocked
def test_cli_config_get_and_set(tmp_path, monkeypatch, capsys):
    xdg_config, _, _ = _setup_cli_env(tmp_path, monkeypatch)
    res = initialize_vault(password=b"cfg-pass-123")

    monkeypatch.setattr("private_journal.cli.getpass.getpass", lambda _p="": "cfg-pass-123")
    monkeypatch.setattr("builtins.input", lambda _p="": pyotp.TOTP(res.totp_secret).now())

    try:
        assert main(["unlock"]) == 0
        capsys.readouterr()

        assert main(["config", "get", "unlock_timeout"]) == 0
        assert capsys.readouterr().out.strip() == "15m"

        assert main(["config", "set", "unlock_timeout", "30m"]) == 0
        capsys.readouterr()

        assert main(["config", "get", "unlock_timeout"]) == 0
        assert capsys.readouterr().out.strip() == "30m"

        # Invalid timeout and unknown key rejected
        assert main(["config", "set", "unlock_timeout", "bad"]) != 0
        assert main(["config", "get", "unknown_key"]) != 0
        capsys.readouterr()

        cfg = json.loads((xdg_config / "private-journal" / "config.json").read_text(encoding="utf-8"))
        assert cfg["unlock_timeout"] == "30m"
    finally:
        main(["lock"])


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Execute full CLI entry workflow (new, view, edit, search, delete, status, lock)
def test_cli_full_entry_workflow(tmp_path, monkeypatch, capsys):
    _setup_cli_env(tmp_path, monkeypatch)
    res = initialize_vault(password=b"flow-pass-123")

    monkeypatch.setattr("private_journal.cli.getpass.getpass", lambda _p="": "flow-pass-123")
    monkeypatch.setattr("builtins.input", lambda _p="": pyotp.TOTP(res.totp_secret).now())

    assert main(["unlock"]) == 0
    capsys.readouterr()

    try:
        # new via mocked nvim
        def fake_nvim_new(cmd, check=False):
            Path(cmd[-1]).write_text("First CLI note", encoding="utf-8")

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr("private_journal.editor.subprocess.run", fake_nvim_new)
        assert main(["new", "--tag", "cli"]) == 0
        entry_id = capsys.readouterr().out.strip()
        assert len(entry_id) == 32

        # view
        assert main(["view", entry_id]) == 0
        view_out = capsys.readouterr().out
        assert "First CLI note" in view_out
        assert "cli" in view_out

        # edit
        def fake_nvim_edit(cmd, check=False):
            Path(cmd[-1]).write_text("Updated CLI note", encoding="utf-8")

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr("private_journal.editor.subprocess.run", fake_nvim_edit)
        assert main(["edit", entry_id]) == 0
        capsys.readouterr()

        # search --plain
        assert main(["search", "Updated", "--tag", "cli", "--plain"]) == 0
        plain_out = capsys.readouterr().out
        assert entry_id in plain_out
        assert "Updated CLI note" in plain_out

        # search via fzf --id
        def fake_fzf(cmd, input=None, text=False, capture_output=False, check=False):
            class _R:
                returncode = 0
                stdout = input.splitlines()[0] + "\n"

            return _R()

        monkeypatch.setattr("private_journal.search.subprocess.run", fake_fzf)
        assert main(["search", "--id"]) == 0
        assert capsys.readouterr().out.strip() == entry_id

        # delete
        assert main(["delete", entry_id]) == 0
        capsys.readouterr()
        assert main(["view", entry_id]) != 0
        capsys.readouterr()

        # status before and after lock
        assert main(["status"]) == 0
        assert "unlocked" in capsys.readouterr().out.lower()
    finally:
        assert main(["lock"]) == 0

    assert main(["status"]) == 0
    assert "locked" in capsys.readouterr().out.lower()


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Support short semantic aliases for all CLI subcommands
def test_cli_short_aliases(tmp_path, monkeypatch, capsys):
    _setup_cli_env(tmp_path, monkeypatch)
    res = initialize_vault(password=b"alias-pass-123")

    monkeypatch.setattr("private_journal.cli.getpass.getpass", lambda _p="": "alias-pass-123")
    monkeypatch.setattr("builtins.input", lambda _p="": pyotp.TOTP(res.totp_secret).now())

    assert main(["ul"]) == 0
    capsys.readouterr()
    try:
        assert main(["st"]) == 0
        assert "unlocked" in capsys.readouterr().out.lower()

        def fake_nvim(cmd, check=False):
            Path(cmd[-1]).write_text("Alias note", encoding="utf-8")

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr("private_journal.editor.subprocess.run", fake_nvim)
        assert main(["n", "--tag", "alias"]) == 0
        entry_id = capsys.readouterr().out.strip()

        assert main(["v", entry_id]) == 0
        assert "Alias note" in capsys.readouterr().out

        assert main(["e", entry_id]) == 0
        capsys.readouterr()

        assert main(["s", "--plain"]) == 0
        assert entry_id in capsys.readouterr().out
        assert main(["ls", "--plain"]) == 0
        assert entry_id in capsys.readouterr().out

        assert main(["cfg", "g", "unlock_timeout"]) == 0
        assert capsys.readouterr().out.strip() == "15m"
        assert main(["cfg", "s", "unlock_timeout", "20m"]) == 0
        capsys.readouterr()

        assert main(["d", entry_id]) == 0
        capsys.readouterr()
    finally:
        assert main(["lk"]) == 0
