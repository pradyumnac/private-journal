"""Encrypted journal entry storage and HMAC-derived opaque filenames."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from private_journal.crypto import (
    CryptoError,
    SecureKeyBuffer,
    aead_decrypt,
    aead_encrypt,
    derive_subkey,
)


class StorageError(Exception):
    """Raised on encrypted storage read, write, or authentication failures."""


def _normalize_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in tags:
        tag = raw.strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            normalized.append(tag)
    return normalized


@dataclass(frozen=True)
class JournalEntry:
    """Decrypted journal entry with metadata."""

    id: str
    created_at: float
    updated_at: float
    tags: list[str]
    body: str

    @classmethod
    def new(cls, body: str, tags: list[str] | None = None) -> JournalEntry:
        now = time.time()
        return cls(
            id=uuid.uuid4().hex,
            created_at=now,
            updated_at=now,
            tags=_normalize_tags(tags or []),
            body=body,
        )

    def updated(self, body: str | None = None, tags: list[str] | None = None) -> JournalEntry:
        return JournalEntry(
            id=self.id,
            created_at=self.created_at,
            updated_at=time.time(),
            tags=_normalize_tags(tags if tags is not None else self.tags),
            body=body if body is not None else self.body,
        )


def entry_filename(master_key: SecureKeyBuffer, entry_id: str) -> str:
    """Derive an opaque 64-hex-char encrypted filename for an entry ID."""
    fname_key = derive_subkey(master_key, b"filename-key")
    digest = hmac.new(fname_key, entry_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{digest}.enc"


def _decrypt_file(master_key: SecureKeyBuffer, file_path: Path) -> JournalEntry:
    filename = file_path.name
    subkey = derive_subkey(master_key, f"entry-file:{filename}".encode("utf-8"))
    try:
        blob = file_path.read_bytes()
        plaintext = aead_decrypt(subkey, blob, associated_data=filename.encode("utf-8"))
        data = json.loads(plaintext.decode("utf-8"))
    except (OSError, CryptoError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"Failed to decrypt entry {filename}.") from exc

    entry_id = data.get("id")
    if not isinstance(entry_id, str) or entry_filename(master_key, entry_id) != filename:
        raise StorageError("Entry ID does not match encrypted filename.")

    return JournalEntry(
        id=entry_id,
        created_at=float(data["created_at"]),
        updated_at=float(data["updated_at"]),
        tags=list(data.get("tags", [])),
        body=str(data["body"]),
    )


def save_entry(data_dir: Path, master_key: SecureKeyBuffer, entry: JournalEntry) -> Path:
    """Encrypt and atomically write a journal entry with an opaque filename."""
    entries_dir = Path(data_dir) / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(entries_dir, 0o700)

    filename = entry_filename(master_key, entry.id)
    target_path = entries_dir / filename
    subkey = derive_subkey(master_key, f"entry-file:{filename}".encode("utf-8"))

    payload = json.dumps(
        {
            "version": 1,
            "id": entry.id,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "tags": entry.tags,
            "body": entry.body,
        },
        separators=(",", ":"),
    ).encode("utf-8")

    encrypted = aead_encrypt(subkey, payload, associated_data=filename.encode("utf-8"))

    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=entries_dir)
    try:
        os.chmod(tmp_name, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target_path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

    return target_path


def load_entry(data_dir: Path, master_key: SecureKeyBuffer, entry_id: str) -> JournalEntry:
    """Load and decrypt a single journal entry by ID."""
    target_path = Path(data_dir) / "entries" / entry_filename(master_key, entry_id)
    if not target_path.exists():
        raise StorageError(f"Entry {entry_id} not found.")
    return _decrypt_file(master_key, target_path)


def list_entries(data_dir: Path, master_key: SecureKeyBuffer) -> list[JournalEntry]:
    """Decrypt and return all entries stored in the vault."""
    entries_dir = Path(data_dir) / "entries"
    if not entries_dir.exists():
        return []
    return [
        _decrypt_file(master_key, path)
        for path in sorted(entries_dir.iterdir())
        if path.is_file() and path.suffix == ".enc"
    ]


def delete_entry(data_dir: Path, master_key: SecureKeyBuffer, entry_id: str) -> None:
    """Delete an encrypted journal entry by ID."""
    target_path = Path(data_dir) / "entries" / entry_filename(master_key, entry_id)
    if not target_path.exists():
        raise StorageError(f"Entry {entry_id} not found.")
    target_path.unlink()
