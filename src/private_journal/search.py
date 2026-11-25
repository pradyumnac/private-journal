"""Transient in-memory full-text/tag search and fzf selection."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

from private_journal.daemon import SessionClient
from private_journal.storage import JournalEntry


class SearchError(Exception):
    """Raised on invalid search filters or fzf execution errors."""


def _parse_date_bound(date_str: str, end_of_day: bool) -> float:
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SearchError(f"Invalid date '{date_str}'; expected YYYY-MM-DD.") from exc
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt.timestamp()


def search_entries(
    client: SessionClient,
    query: str | None = None,
    tags: list[str] | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[JournalEntry]:
    """Filter unlocked entries in memory by full-text query, tags, and date range."""
    entries = client.list_entries()
    required_tags = {t.strip().lower() for t in (tags or []) if t.strip()}
    terms = [term for term in (query or "").lower().split() if term]
    after_ts = _parse_date_bound(after, end_of_day=False) if after else None
    before_ts = _parse_date_bound(before, end_of_day=True) if before else None

    matches: list[JournalEntry] = []
    for entry in entries:
        if required_tags and not required_tags.issubset(set(entry.tags)):
            continue
        if after_ts is not None and entry.created_at < after_ts:
            continue
        if before_ts is not None and entry.created_at > before_ts:
            continue
        if terms:
            searchable = f"{entry.body.lower()} {' '.join(entry.tags)}"
            if not all(term in searchable for term in terms):
                continue
        matches.append(entry)

    matches.sort(key=lambda e: e.created_at, reverse=True)
    return matches


def format_search_lines(entries: list[JournalEntry]) -> list[str]:
    """Format entries as tab-delimited lines for fzf or plain output."""
    lines: list[str] = []
    for entry in entries:
        date_str = datetime.fromtimestamp(entry.created_at, tz=timezone.utc).strftime("%Y-%m-%d")
        first_line = next((ln.strip() for ln in entry.body.splitlines() if ln.strip()), "")
        snippet = first_line[:80] + ("..." if len(first_line) > 80 else "")
        tags_bracket = f"[{', '.join(entry.tags)}]" if entry.tags else "[]"
        lines.append(f"{entry.id}\t{date_str}\t{snippet}\t{tags_bracket}")
    return lines


def resolve_entry_id(client: SessionClient, prefix: str) -> str:
    """Resolve a full entry ID or unique ID prefix."""
    needle = prefix.strip().lower()
    if not needle:
        raise SearchError("Entry ID must not be empty.")
    entries = client.list_entries()
    matches = [e.id for e in entries if e.id.lower().startswith(needle)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SearchError(f"Ambiguous entry ID prefix '{prefix}' ({len(matches)} matches).")
    raise SearchError(f"No entry found matching '{prefix}'.")


def select_entry_action_with_fzf(entries: list[JournalEntry]) -> tuple[str, str | None]:
    """Launch fzf with multi-action keybinds (Enter=view, Ctrl-E=edit, Ctrl-D=delete)."""
    if not entries:
        return "cancel", None
    lines = format_search_lines(entries)
    cmd = [
        "fzf",
        "--no-history",
        "--delimiter=\t",
        "--with-nth=2..",
        "--expect=ctrl-e,ctrl-d",
        "--header=Enter: view | Ctrl-E: edit | Ctrl-D: delete",
    ]
    result = subprocess.run(
        cmd,
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode in (1, 130):
        return "cancel", None
    if result.returncode != 0:
        raise SearchError(f"fzf exited with status {result.returncode}.")
    out_lines = result.stdout.splitlines()
    if not out_lines:
        return "cancel", None
    if len(out_lines) == 1:
        key = ""
        selected = out_lines[0].strip()
    else:
        key = out_lines[0].strip().lower()
        selected = out_lines[1].strip()
    if not selected:
        return "cancel", None
    entry_id = selected.split("\t", 1)[0]
    action = {"ctrl-e": "edit", "ctrl-d": "delete"}.get(key, "view")
    return action, entry_id


def select_entry_with_fzf(entries: list[JournalEntry]) -> str | None:
    """Launch fzf with --no-history and return the selected entry ID, or None."""
    if not entries:
        return None
    lines = format_search_lines(entries)
    cmd = ["fzf", "--no-history", "--delimiter=\t", "--with-nth=2.."]
    result = subprocess.run(
        cmd,
        input="\n".join(lines) + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode in (1, 130):
        return None
    if result.returncode != 0:
        raise SearchError(f"fzf exited with status {result.returncode}.")
    selected = result.stdout.strip()
    if not selected:
        return None
    return selected.split("\t", 1)[0]
