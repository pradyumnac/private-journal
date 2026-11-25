"""Tests for Feature: Click CLI, Shell Completion & ID-less Discovery."""

from pathlib import Path

import pyotp
import pytest

from private_journal.cli import main
from private_journal.daemon import SessionClient
from private_journal.search import SearchError, resolve_entry_id, select_entry_action_with_fzf
from private_journal.setup import initialize_vault
from private_journal.storage import JournalEntry


def _setup_unlocked(tmp_path, monkeypatch):
    xdg_config = tmp_path / "xdg_config"
    xdg_data = tmp_path / "xdg_data"
    xdg_runtime = tmp_path / "xdg_runtime"
    xdg_runtime.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    res = initialize_vault(password=b"disc-pass-123")
    client = SessionClient()
    client.unlock(password=b"disc-pass-123", totp_code=pyotp.TOTP(res.totp_secret).now())
    return client


# Traceability: Feature "Click CLI, Shell Completion & ID-less Discovery"
# Scenario: Resolve entries by unique ID prefix and reject ambiguous prefixes
def test_resolve_entry_id_prefix_and_ambiguity(tmp_path, monkeypatch):
    client = _setup_unlocked(tmp_path, monkeypatch)
    try:
        e1 = JournalEntry(id="abcd1111" + "0" * 24, created_at=1.0, updated_at=1.0, tags=["t1"], body="One")
        e2 = JournalEntry(id="abcd2222" + "0" * 24, created_at=2.0, updated_at=2.0, tags=["t2"], body="Two")
        e3 = JournalEntry(id="ef993333" + "0" * 24, created_at=3.0, updated_at=3.0, tags=["t3"], body="Three")
        for e in (e1, e2, e3):
            client.save_entry(entry=e)

        # Unique 4-char prefix works
        assert resolve_entry_id(client, "ef99") == e3.id
        # Unique 5-char prefix works
        assert resolve_entry_id(client, "abcd1") == e1.id
        # Exact full ID works
        assert resolve_entry_id(client, e2.id) == e2.id

        # Ambiguous prefix fails
        with pytest.raises(SearchError, match="Ambiguous"):
            resolve_entry_id(client, "abcd")

        # Unknown prefix fails
        with pytest.raises(SearchError, match="No entry"):
            resolve_entry_id(client, "zzzz")
    finally:
        client.lock()


# Traceability: Feature "Click CLI, Shell Completion & ID-less Discovery"
# Scenario: Launch fzf automatically when view, edit, or delete is called without an ID
def test_view_edit_delete_fzf_fallback_and_confirm(tmp_path, monkeypatch, capsys):
    client = _setup_unlocked(tmp_path, monkeypatch)
    try:
        e1 = client.save_entry(body="Fallback note", tags=["fb"])

        def fake_subprocess(cmd, input=None, text=False, capture_output=False, check=False):
            if cmd[0] == "fzf":
                class _FzfRes:
                    returncode = 0
                    stdout = input.splitlines()[0] + "\n"

                return _FzfRes()
            Path(cmd[-1]).write_text("Edited fallback", encoding="utf-8")

            class _NvimRes:
                returncode = 0

            return _NvimRes()

        monkeypatch.setattr("private_journal.search.subprocess.run", fake_subprocess)

        # view without ID
        assert main(["v"]) == 0
        assert "Fallback note" in capsys.readouterr().out

        # edit without ID
        assert main(["e"]) == 0
        capsys.readouterr()

        # delete without ID, aborted by confirmation
        monkeypatch.setattr("click.confirm", lambda *a, **kw: False)
        assert main(["d"]) == 0
        assert client.load_entry(e1.id).body == "Edited fallback"

        # delete without ID, confirmed with --yes
        assert main(["d", "--yes"]) == 0
        assert client.list_entries() == []
    finally:
        client.lock()


# Traceability: Feature "Click CLI, Shell Completion & ID-less Discovery"
# Scenario: Support multi-action keybinds in interactive search
def test_search_multi_action_keybinds(tmp_path, monkeypatch, capsys):
    client = _setup_unlocked(tmp_path, monkeypatch)
    try:
        e1 = client.save_entry(body="Multi action note", tags=["ma"])

        # Direct unit check of select_entry_action_with_fzf
        def fake_fzf_ctrl_e(cmd, input=None, text=False, capture_output=False, check=False):
            assert "--expect=ctrl-e,ctrl-d" in cmd

            class _R:
                returncode = 0
                stdout = f"ctrl-e\n{input.splitlines()[0]}\n"

            return _R()

        monkeypatch.setattr("private_journal.search.subprocess.run", fake_fzf_ctrl_e)
        action, picked_id = select_entry_action_with_fzf([e1])
        assert action == "edit"
        assert picked_id == e1.id

        # Ctrl-D via CLI search deletes with --yes or confirm
        def fake_fzf_ctrl_d(cmd, input=None, text=False, capture_output=False, check=False):
            class _R:
                returncode = 0
                stdout = f"ctrl-d\n{input.splitlines()[0]}\n"

            return _R()

        monkeypatch.setattr("private_journal.search.subprocess.run", fake_fzf_ctrl_d)
        monkeypatch.setattr("click.confirm", lambda *a, **kw: True)
        assert main(["s"]) == 0
        assert client.list_entries() == []
    finally:
        client.lock()


# Traceability: Feature "Click CLI, Shell Completion & ID-less Discovery"
# Scenario: Generate shell completion scripts for bash, zsh, and fish
@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_cli_shell_completion(shell, capsys):
    assert main(["completion", shell]) == 0
    out = capsys.readouterr().out
    assert "_PJ_COMPLETE" in out


# Traceability: Feature "Click CLI, Shell Completion & ID-less Discovery"
# Scenario: Manage tags on an existing entry without opening nvim
def test_cli_tag_add_remove_set_clear_and_fzf_fallback(tmp_path, monkeypatch, capsys):
    client = _setup_unlocked(tmp_path, monkeypatch)
    try:
        e1 = client.save_entry(body="Untouched body", tags=["work"])
        prefix = e1.id[:4]

        # Add tags by prefix
        assert main(["tag", prefix, "-a", "urgent", "-a", "Work"]) == 0
        assert "urgent" in capsys.readouterr().out
        assert client.load_entry(e1.id).tags == ["work", "urgent"]

        # Remove tag
        assert main(["t", prefix, "-r", "work"]) == 0
        capsys.readouterr()
        assert client.load_entry(e1.id).tags == ["urgent"]

        # Set tags
        assert main(["t", prefix, "-s", "alpha", "-s", "beta"]) == 0
        capsys.readouterr()
        assert client.load_entry(e1.id).tags == ["alpha", "beta"]

        # Show tags with no flags and fzf fallback when ID omitted
        def fake_fzf(cmd, input=None, text=False, capture_output=False, check=False):
            class _R:
                returncode = 0
                stdout = input.splitlines()[0] + "\n"

            return _R()

        monkeypatch.setattr("private_journal.search.subprocess.run", fake_fzf)
        assert main(["t"]) == 0
        assert "alpha, beta" in capsys.readouterr().out

        # Clear tags with fzf fallback
        assert main(["t", "--clear"]) == 0
        capsys.readouterr()
        reloaded = client.load_entry(e1.id)
        assert reloaded.tags == []
        assert reloaded.body == "Untouched body"
        assert reloaded.created_at == e1.created_at
    finally:
        client.lock()
