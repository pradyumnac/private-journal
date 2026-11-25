"""Sandboxed nvim editing and read-only entry formatting."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from private_journal.daemon import SessionClient
from private_journal.paths import get_xdg_runtime_dir
from private_journal.storage import JournalEntry


class EditorError(Exception):
    """Raised when sandboxed nvim editing fails."""


def _secure_wipe_and_unlink(path: Path) -> None:
    """Overwrite file contents with zeros before unlinking."""
    if not path.exists():
        return
    try:
        size = path.stat().st_size
        if size > 0:
            with open(path, "r+b") as handle:
                handle.write(b"\x00" * size)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if path.exists():
            path.unlink()


def _edit_in_sandboxed_nvim(initial_text: str) -> str:
    runtime_dir = get_xdg_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o700)

    fd, tmp_name = tempfile.mkstemp(prefix="entry-", suffix=".txt", dir=runtime_dir)
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(initial_text)
            handle.flush()

        cmd = [
            "nvim",
            "-n",
            "-i",
            "NONE",
            "-c",
            "setlocal nobackup nowritebackup noundofile noswapfile",
            str(tmp_path),
        ]
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            raise EditorError(f"nvim exited with status {result.returncode}.")
        return tmp_path.read_text(encoding="utf-8")
    finally:
        _secure_wipe_and_unlink(tmp_path)


def create_entry_with_editor(
    client: SessionClient,
    tags: list[str] | None = None,
) -> JournalEntry | None:
    """Create a new entry using sandboxed nvim; returns None if aborted/empty."""
    content = _edit_in_sandboxed_nvim("")
    if not content.strip():
        return None
    return client.save_entry(body=content, tags=tags or [])


def edit_entry_with_editor(
    client: SessionClient,
    entry_id: str,
    tags: list[str] | None = None,
) -> JournalEntry:
    """Edit an existing entry using sandboxed nvim."""
    existing = client.load_entry(entry_id)
    new_body = _edit_in_sandboxed_nvim(existing.body)
    updated = existing.updated(body=new_body, tags=tags)
    return client.save_entry(entry=updated)


def format_entry_view(entry: JournalEntry) -> str:
    """Format a decrypted entry for read-only display."""
    created = datetime.fromtimestamp(entry.created_at, tz=timezone.utc).isoformat()
    updated = datetime.fromtimestamp(entry.updated_at, tz=timezone.utc).isoformat()
    tags_str = ", ".join(entry.tags) if entry.tags else "(none)"
    return (
        f"ID:      {entry.id}\n"
        f"Created: {created}\n"
        f"Updated: {updated}\n"
        f"Tags:    {tags_str}\n"
        f"---\n"
        f"{entry.body}"
    )
