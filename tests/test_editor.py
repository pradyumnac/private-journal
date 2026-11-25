"""Tests for Feature: Sandboxed nvim Integration & Read-Only View."""

from pathlib import Path

import pyotp
import pytest

from private_journal.daemon import SessionClient
from private_journal.editor import EditorError, create_entry_with_editor, edit_entry_with_editor, format_entry_view
from private_journal.setup import initialize_vault


def _unlock_client(tmp_path, monkeypatch):
    xdg_config = tmp_path / "xdg_config"
    xdg_runtime = tmp_path / "xdg_runtime"
    xdg_runtime.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "vault"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    res = initialize_vault(password=b"editor-pass-123", data_dir=data_dir)
    client = SessionClient()
    code = pyotp.TOTP(res.totp_secret).now()
    client.unlock(password=b"editor-pass-123", totp_code=code)
    return client, xdg_runtime


# Traceability: Feature "Sandboxed nvim Integration & Read-Only View"
# Scenario: Create a new entry via sandboxed nvim
def test_create_new_entry_via_sandboxed_nvim(tmp_path, monkeypatch):
    client, xdg_runtime = _unlock_client(tmp_path, monkeypatch)
    seen_cmd: list[str] = []
    seen_path: list[Path] = []

    def fake_run(cmd, check=False):
        seen_cmd.extend(cmd)
        tmp_file = Path(cmd[-1])
        seen_path.append(tmp_file)
        assert tmp_file.parent == xdg_runtime / "private-journal"
        assert (tmp_file.stat().st_mode & 0o777) == 0o600
        tmp_file.write_text("My new journal entry\nLine 2", encoding="utf-8")

        class _Res:
            returncode = 0

        return _Res()

    monkeypatch.setattr("private_journal.editor.subprocess.run", fake_run)
    try:
        entry = create_entry_with_editor(client, tags=["daily", "notes"])
        assert entry is not None
        assert entry.body == "My new journal entry\nLine 2"
        assert entry.tags == ["daily", "notes"]

        assert seen_cmd[0] == "nvim"
        assert "-n" in seen_cmd
        assert "-i" in seen_cmd
        assert "NONE" in seen_cmd
        assert any("nobackup" in arg and "noundofile" in arg and "noswapfile" in arg for arg in seen_cmd)
        assert not seen_path[0].exists()

        loaded = client.load_entry(entry.id)
        assert loaded.body == "My new journal entry\nLine 2"
    finally:
        client.lock()


# Traceability: Feature "Sandboxed nvim Integration & Read-Only View"
# Scenario: Abort new entry when buffer is empty or unchanged
def test_abort_new_entry_on_empty_buffer(tmp_path, monkeypatch):
    client, _ = _unlock_client(tmp_path, monkeypatch)
    seen_path: list[Path] = []

    def fake_run(cmd, check=False):
        tmp_file = Path(cmd[-1])
        seen_path.append(tmp_file)

        class _Res:
            returncode = 0

        return _Res()

    monkeypatch.setattr("private_journal.editor.subprocess.run", fake_run)
    try:
        entry = create_entry_with_editor(client, tags=["empty"])
        assert entry is None
        assert not seen_path[0].exists()
        assert client.list_entries() == []
    finally:
        client.lock()


# Traceability: Feature "Sandboxed nvim Integration & Read-Only View"
# Scenario: Edit an existing entry via sandboxed nvim
def test_edit_existing_entry_and_wipe_on_failure(tmp_path, monkeypatch):
    client, xdg_runtime = _unlock_client(tmp_path, monkeypatch)
    created = client.save_entry(body="Original body", tags=["alpha"])
    seen_path: list[Path] = []

    def fake_run_edit(cmd, check=False):
        tmp_file = Path(cmd[-1])
        seen_path.append(tmp_file)
        assert tmp_file.parent == xdg_runtime / "private-journal"
        assert tmp_file.read_text(encoding="utf-8") == "Original body"
        tmp_file.write_text("Updated body", encoding="utf-8")

        class _Res:
            returncode = 0

        return _Res()

    monkeypatch.setattr("private_journal.editor.subprocess.run", fake_run_edit)
    try:
        updated = edit_entry_with_editor(client, created.id, tags=["alpha", "beta"])
        assert updated.created_at == created.created_at
        assert updated.body == "Updated body"
        assert updated.tags == ["alpha", "beta"]
        assert not seen_path[0].exists()

        # Verify temp file is still wiped if nvim exits non-zero
        def fake_run_fail(cmd, check=False):
            tmp_file = Path(cmd[-1])
            seen_path.append(tmp_file)

            class _Res:
                returncode = 1

            return _Res()

        monkeypatch.setattr("private_journal.editor.subprocess.run", fake_run_fail)
        with pytest.raises(EditorError):
            edit_entry_with_editor(client, created.id)
        assert not seen_path[-1].exists()
    finally:
        client.lock()


# Traceability: Feature "Sandboxed nvim Integration & Read-Only View"
# Scenario: Display an entry read-only with view
def test_view_entry_read_only_no_temp_files(tmp_path, monkeypatch):
    client, xdg_runtime = _unlock_client(tmp_path, monkeypatch)
    created = client.save_entry(body="Read-only body", tags=["journal", "privacy"])

    try:
        loaded = client.load_entry(created.id)
        rendered = format_entry_view(loaded)
        assert created.id in rendered
        assert "journal, privacy" in rendered
        assert "Read-only body" in rendered

        runtime_dir = xdg_runtime / "private-journal"
        temp_files = [p for p in runtime_dir.iterdir() if p.name != "daemon.sock"]
        assert temp_files == []
    finally:
        client.lock()
