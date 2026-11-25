"""Tests for Feature: Encrypted Storage & Filename Encryption."""

import os
import time

import pytest

from private_journal.crypto import SecureKeyBuffer
from private_journal.storage import (
    JournalEntry,
    StorageError,
    delete_entry,
    list_entries,
    load_entry,
    save_entry,
)


def _make_key(seed: bytes | None = None) -> SecureKeyBuffer:
    buf = SecureKeyBuffer(32)
    buf.write(seed or os.urandom(32))
    return buf


# Traceability: Feature "Encrypted Storage & Filename Encryption"
# Scenario: Save an entry as a single encrypted blob with an encrypted filename
def test_save_entry_encrypted_blob_and_opaque_filename(tmp_path):
    with _make_key() as mk:
        entry = JournalEntry.new(body="Secret thoughts about today", tags=["Work", "  focus "])
        path = save_entry(tmp_path, mk, entry)

        assert path.exists()
        assert path.parent == tmp_path / "entries"
        assert (path.stat().st_mode & 0o777) == 0o600

        # Filename reveals no entry ID, date, or tags
        assert entry.id not in path.name
        assert "work" not in path.name
        assert "focus" not in path.name
        assert len(path.stem) == 64

        # Raw file bytes contain no plaintext metadata or body
        raw = path.read_bytes()
        assert b"Secret thoughts" not in raw
        assert b"focus" not in raw
        assert entry.id.encode("utf-8") not in raw


# Traceability: Feature "Encrypted Storage & Filename Encryption"
# Scenario: Load and decrypt an entry by ID
def test_load_entry_and_reject_wrong_key(tmp_path):
    with _make_key() as mk, _make_key() as wrong_mk:
        entry = JournalEntry.new(body="Entry body", tags=["alpha", "beta"])
        save_entry(tmp_path, mk, entry)

        loaded = load_entry(tmp_path, mk, entry.id)
        assert loaded.id == entry.id
        assert loaded.created_at == entry.created_at
        assert loaded.updated_at == entry.updated_at
        assert loaded.tags == ["alpha", "beta"]
        assert loaded.body == "Entry body"

        with pytest.raises(StorageError):
            load_entry(tmp_path, wrong_mk, entry.id)


# Traceability: Feature "Encrypted Storage & Filename Encryption"
# Scenario: Update an existing entry preserving created_at and updating updated_at
def test_update_entry_preserves_created_at(tmp_path):
    with _make_key() as mk:
        entry = JournalEntry.new(body="Initial", tags=["v1"])
        save_entry(tmp_path, mk, entry)

        time.sleep(0.01)
        updated = entry.updated(body="Edited body", tags=["v1", "v2"])
        save_entry(tmp_path, mk, updated)

        loaded = load_entry(tmp_path, mk, entry.id)
        assert loaded.created_at == entry.created_at
        assert loaded.updated_at > entry.created_at
        assert loaded.body == "Edited body"
        assert loaded.tags == ["v1", "v2"]


# Traceability: Feature "Encrypted Storage & Filename Encryption"
# Scenario: List all decrypted entries from encrypted storage
def test_list_entries_decrypts_all(tmp_path):
    with _make_key() as mk:
        e1 = JournalEntry.new(body="First", tags=["one"])
        e2 = JournalEntry.new(body="Second", tags=["two"])
        save_entry(tmp_path, mk, e1)
        save_entry(tmp_path, mk, e2)

        entries = list_entries(tmp_path, mk)
        by_id = {e.id: e for e in entries}
        assert set(by_id.keys()) == {e1.id, e2.id}
        assert by_id[e1.id].body == "First"
        assert by_id[e2.id].body == "Second"

        # Only encrypted entry files in entries/
        files = list((tmp_path / "entries").iterdir())
        assert len(files) == 2
        assert all(f.suffix == ".enc" for f in files)


# Traceability: Feature "Encrypted Storage & Filename Encryption"
# Scenario: Delete an entry and reject missing or tampered files
def test_delete_and_tamper_or_swap_rejection(tmp_path):
    with _make_key() as mk:
        e1 = JournalEntry.new(body="One", tags=[])
        e2 = JournalEntry.new(body="Two", tags=[])
        p1 = save_entry(tmp_path, mk, e1)
        p2 = save_entry(tmp_path, mk, e2)

        # Swapping files must fail AEAD associated data check
        b1 = p1.read_bytes()
        b2 = p2.read_bytes()
        p1.write_bytes(b2)
        with pytest.raises(StorageError):
            load_entry(tmp_path, mk, e1.id)

        # Restore and tamper byte
        p1.write_bytes(b1[:-1] + bytes([b1[-1] ^ 0xFF]))
        with pytest.raises(StorageError):
            load_entry(tmp_path, mk, e1.id)

        # Delete entry
        delete_entry(tmp_path, mk, e1.id)
        assert not p1.exists()
        with pytest.raises(StorageError):
            load_entry(tmp_path, mk, e1.id)
        with pytest.raises(StorageError):
            delete_entry(tmp_path, mk, e1.id)
