"""Tests for Feature: Transient Full-Text & Tag Search with fzf."""

from datetime import datetime, timezone

import pyotp

from private_journal.daemon import SessionClient
from private_journal.search import format_search_lines, search_entries, select_entry_with_fzf
from private_journal.setup import initialize_vault
from private_journal.storage import JournalEntry


def _unlock_client(tmp_path, monkeypatch):
    xdg_config = tmp_path / "xdg_config"
    xdg_runtime = tmp_path / "xdg_runtime"
    xdg_runtime.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "vault"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    res = initialize_vault(password=b"search-pass-123", data_dir=data_dir)
    client = SessionClient()
    code = pyotp.TOTP(res.totp_secret).now()
    client.unlock(password=b"search-pass-123", totp_code=code)
    return client, data_dir


# Traceability: Feature "Transient Full-Text & Tag Search with fzf"
# Scenario: Build transient in-memory index and search by full-text query
def test_transient_full_text_search_and_no_disk_index(tmp_path, monkeypatch):
    client, data_dir = _unlock_client(tmp_path, monkeypatch)
    try:
        e1 = client.save_entry(body="Cryptography uses Argon2id and ChaCha20", tags=["security"])
        client.save_entry(body="Grocery list: apples and coffee", tags=["personal"])

        results = search_entries(client, query="argon2id")
        assert [e.id for e in results] == [e1.id]

        # Ensure no plaintext index files exist on disk
        top_files = {p.name for p in data_dir.iterdir()}
        assert top_files == {"vault.json", "entries"}
    finally:
        client.lock()


# Traceability: Feature "Transient Full-Text & Tag Search with fzf"
# Scenario: Filter entries by tags and date range, or list all when unfiltered
def test_filter_by_tags_dates_and_list_all_sorted(tmp_path, monkeypatch):
    client, _ = _unlock_client(tmp_path, monkeypatch)
    try:
        t1 = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc).timestamp()
        t2 = datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc).timestamp()
        t3 = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc).timestamp()

        e1 = JournalEntry(id="a" * 32, created_at=t1, updated_at=t1, tags=["work", "rust"], body="Jan note")
        e2 = JournalEntry(id="b" * 32, created_at=t2, updated_at=t2, tags=["work", "python"], body="Feb note")
        e3 = JournalEntry(id="c" * 32, created_at=t3, updated_at=t3, tags=["personal"], body="Mar note")
        for e in (e1, e2, e3):
            client.save_entry(entry=e)

        # Unfiltered lists all newest first
        all_entries = search_entries(client)
        assert [e.id for e in all_entries] == [e3.id, e2.id, e1.id]

        # Tag filter (must match all specified tags)
        work_py = search_entries(client, tags=["work", "python"])
        assert [e.id for e in work_py] == [e2.id]

        # Date range filter
        feb_only = search_entries(client, after="2026-02-01", before="2026-02-28")
        assert [e.id for e in feb_only] == [e2.id]
    finally:
        client.lock()


# Traceability: Feature "Transient Full-Text & Tag Search with fzf"
# Scenario: Select an entry interactively via fzf with history disabled
def test_select_entry_with_fzf_no_history(tmp_path, monkeypatch):
    t1 = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc).timestamp()
    entry = JournalEntry(id="d" * 32, created_at=t1, updated_at=t1, tags=["focus"], body="First line\nSecond line")
    lines = format_search_lines([entry])
    assert len(lines) == 1
    assert "2026-04-01" in lines[0]
    assert "#focus" in lines[0]
    assert "First line" in lines[0]

    seen_cmd: list[str] = []

    def fake_fzf_select(cmd, input=None, text=False, capture_output=False, check=False):
        seen_cmd.extend(cmd)
        assert "--no-history" in cmd
        assert input is not None and entry.id in input

        class _Res:
            returncode = 0
            stdout = lines[0] + "\n"

        return _Res()

    monkeypatch.setattr("private_journal.search.subprocess.run", fake_fzf_select)
    selected = select_entry_with_fzf([entry])
    assert selected == entry.id

    def fake_fzf_cancel(cmd, input=None, text=False, capture_output=False, check=False):
        class _Res:
            returncode = 130
            stdout = ""

        return _Res()

    monkeypatch.setattr("private_journal.search.subprocess.run", fake_fzf_cancel)
    assert select_entry_with_fzf([entry]) is None
