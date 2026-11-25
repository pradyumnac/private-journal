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
    help_out = capsys.readouterr().out
    assert "pj" in help_out
    assert "unlock (ul)" in help_out
    assert "Examples:" in help_out

    with pytest.raises(SystemExit) as exc_ver:
        main(["--version"])
    assert exc_ver.value.code == 0
    from private_journal.cli import _get_version

    assert _get_version() in capsys.readouterr().out


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Initialize vault via pj setup and require setup before unlock
def test_cli_setup_and_unlock_requires_setup(tmp_path, monkeypatch, capsys):
    _, xdg_data, _ = _setup_cli_env(tmp_path, monkeypatch)

    # status before setup must report Not initialized
    assert main(["status"]) == 0
    assert "not initialized" in capsys.readouterr().out.lower()

    # unlock before setup must fail
    monkeypatch.setattr("click.prompt", lambda text, **kw: "init-pass-123" if kw.get("hide_input") else "000000")
    assert main(["unlock"]) != 0
    assert "setup" in capsys.readouterr().err.lower()

    # setup initializes vault (opt-in TOTP confirmed)
    prompts = iter(["init-pass-123", "init-pass-123"])
    monkeypatch.setattr("click.prompt", lambda text, **kw: next(prompts))
    monkeypatch.setattr("click.confirm", lambda *a, **kw: True)
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    secret = ""
    for token in out.split():
        if token.startswith("otpauth://"):
            secret = token.split("secret=")[1].split("&")[0]
    assert secret
    assert "█" in out or "▀" in out or "▄" in out
    assert (xdg_data / "private-journal" / "vault.json").exists()
    assert not list((xdg_data / "private-journal").glob("*.png"))

    # unlock now succeeds with password + TOTP
    monkeypatch.setattr(
        "click.prompt",
        lambda text, **kw: "init-pass-123" if kw.get("hide_input") else pyotp.TOTP(secret).now(),
    )
    try:
        assert main(["unlock"]) == 0
        assert main(["status"]) == 0
        assert "unlocked" in capsys.readouterr().out.lower()
    finally:
        main(["lock"])


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Reject setup on existing vault unless --reset is confirmed
def test_cli_setup_rejects_existing_and_supports_reset_confirm(tmp_path, monkeypatch, capsys):
    _setup_cli_env(tmp_path, monkeypatch)
    first = initialize_vault(password=b"old-pass-123")

    # setup without --reset must fail
    assert main(["setup"]) != 0
    assert "already" in capsys.readouterr().err.lower()

    # setup --reset aborted on decline
    monkeypatch.setattr("click.confirm", lambda *a, **kw: False)
    assert main(["setup", "--reset"]) == 0
    capsys.readouterr()

    # setup --reset confirmed resets challenge (new TOTP secret)
    monkeypatch.setattr("click.confirm", lambda *a, **kw: True)
    prompts = iter(["new-pass-456", "new-pass-456"])
    monkeypatch.setattr("click.prompt", lambda text, **kw: next(prompts))
    assert main(["su", "--reset"]) == 0
    out = capsys.readouterr().out
    assert first.totp_secret not in out
    assert "otpauth://totp/" in out


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Opt-in TOTP during setup and password-only unlock when disabled
def test_cli_opt_in_totp_disabled_password_only_unlock(tmp_path, monkeypatch, capsys):
    xdg_config, _, _ = _setup_cli_env(tmp_path, monkeypatch)

    prompts = iter(["pw-only-123", "pw-only-123"])
    monkeypatch.setattr("click.prompt", lambda text, **kw: next(prompts))
    monkeypatch.setattr("click.confirm", lambda *a, **kw: False)
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "otpauth://" not in out

    cfg = json.loads((xdg_config / "private-journal" / "config.json").read_text(encoding="utf-8"))
    assert cfg["totp_enabled"] is False

    # Wrong password still rejected
    monkeypatch.setattr("click.prompt", lambda text, **kw: "wrong-pw")
    assert main(["unlock"]) != 0
    capsys.readouterr()

    # Correct password unlocks without TOTP prompt
    monkeypatch.setattr("click.prompt", lambda text, **kw: "pw-only-123")
    try:
        assert main(["unlock"]) == 0
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

    monkeypatch.setattr(
        "click.prompt",
        lambda text, **kw: "cfg-pass-123" if kw.get("hide_input") else pyotp.TOTP(res.totp_secret).now(),
    )

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

    monkeypatch.setattr(
        "click.prompt",
        lambda text, **kw: "flow-pass-123" if kw.get("hide_input") else pyotp.TOTP(res.totp_secret).now(),
    )

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
        assert main(["delete", entry_id, "--yes"]) == 0
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

    monkeypatch.setattr(
        "click.prompt",
        lambda text, **kw: "alias-pass-123" if kw.get("hide_input") else pyotp.TOTP(res.totp_secret).now(),
    )

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

        assert main(["d", entry_id, "--yes"]) == 0
        capsys.readouterr()
    finally:
        assert main(["lk"]) == 0
