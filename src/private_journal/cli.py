"""Thin CLI wrapper for private-journal (pj)."""

from __future__ import annotations

import argparse
import getpass
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from private_journal.config import ConfigError, get_config_value, load_config, set_config_value
from private_journal.crypto import CryptoError, disable_core_dumps
from private_journal.daemon import SessionClient, SessionError
from private_journal.editor import (
    EditorError,
    create_entry_with_editor,
    edit_entry_with_editor,
    format_entry_view,
)
from private_journal.search import SearchError, format_search_lines, search_entries, select_entry_with_fzf
from private_journal.setup import SetupError, initialize_vault
from private_journal.storage import StorageError


def _get_version() -> str:
    try:
        return version("private-journal")
    except PackageNotFoundError:
        return "0.1.0"


_COMMAND_ALIASES = {
    "ul": "unlock",
    "lk": "lock",
    "st": "status",
    "n": "new",
    "e": "edit",
    "v": "view",
    "d": "delete",
    "rm": "delete",
    "s": "search",
    "ls": "search",
    "cfg": "config",
}

_CONFIG_ALIASES = {
    "g": "get",
    "s": "set",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pj",
        description="Private, single-user, 2FA-gated encrypted CLI journal.",
        epilog=(
            "examples:\n"
            "  pj unlock (ul)                    Start timed session (runs setup on first use)\n"
            "  pj new (n) --tag work             Create a new entry in sandboxed nvim\n"
            "  pj search (s, ls) \"argon2\"        Search entries and pick via fzf\n"
            "  pj view (v) <id>                  Display an entry read-only\n"
            "  pj edit (e) <id>                  Edit an entry in sandboxed nvim\n"
            "  pj delete (d, rm) <id>            Delete an entry\n"
            "  pj status (st) / pj lock (lk)     Check TTL or lock immediately\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("unlock", aliases=["ul"], help="Verify password + TOTP and start timed session.")
    sub.add_parser("lock", aliases=["lk"], help="Lock immediately and wipe session memory.")
    sub.add_parser("status", aliases=["st"], help="Show lock state and remaining timeout.")

    p_new = sub.add_parser("new", aliases=["n"], help="Create a new journal entry via sandboxed nvim.")
    p_new.add_argument("--tag", action="append", default=[], help="Tag for the entry (repeatable).")

    p_edit = sub.add_parser("edit", aliases=["e"], help="Edit an existing entry via sandboxed nvim.")
    p_edit.add_argument("id", help="Entry ID.")
    p_edit.add_argument("--tag", action="append", default=None, help="Replace entry tags (repeatable).")

    p_view = sub.add_parser("view", aliases=["v"], help="Display an entry read-only.")
    p_view.add_argument("id", help="Entry ID.")

    p_del = sub.add_parser("delete", aliases=["d", "rm"], help="Delete an entry.")
    p_del.add_argument("id", help="Entry ID.")

    p_search = sub.add_parser(
        "search",
        aliases=["s", "ls"],
        help="Search/list entries via transient index and fzf.",
        description="Search unlocked entries in memory. Lists all entries when no query or filter is given.",
    )
    p_search.add_argument("query", nargs="?", default=None, help="Full-text query.")
    p_search.add_argument("--tag", action="append", default=[], help="Filter by tag (repeatable).")
    p_search.add_argument("--after", default=None, help="Start date (YYYY-MM-DD, UTC).")
    p_search.add_argument("--before", default=None, help="End date (YYYY-MM-DD, UTC).")
    mode = p_search.add_mutually_exclusive_group()
    mode.add_argument("--plain", action="store_true", help="Print matching lines to stdout.")
    mode.add_argument("--id", action="store_true", help="Print selected entry ID.")
    mode.add_argument("--edit", action="store_true", help="Open selected entry in nvim.")

    p_cfg = sub.add_parser("config", aliases=["cfg"], help="Read or update configuration (unlock_timeout, data_dir).")
    cfg_sub = p_cfg.add_subparsers(dest="config_action", required=True)
    p_cget = cfg_sub.add_parser("get", aliases=["g"], help="Get a config value.")
    p_cget.add_argument("key", help="Config key (unlock_timeout, data_dir).")
    p_cset = cfg_sub.add_parser("set", aliases=["s"], help="Set a config value.")
    p_cset.add_argument("key", help="Config key (unlock_timeout, data_dir).")
    p_cset.add_argument("value", help="New config value.")

    return parser


def _require_unlocked(client: SessionClient) -> None:
    if not client.status().unlocked:
        raise SessionError("Session is locked. Run 'pj unlock' first.")


def _handle_unlock(client: SessionClient) -> int:
    cfg = load_config()
    data_dir = Path(cfg["data_dir"])
    vault_file = data_dir / "vault.json"

    if not vault_file.exists():
        pw1 = getpass.getpass("Create vault password: ")
        pw2 = getpass.getpass("Confirm vault password: ")
        if pw1 != pw2:
            raise SetupError("Passwords do not match.")
        res = initialize_vault(password=pw1.encode("utf-8"), data_dir=data_dir)
        print(f"Vault initialized at {res.data_dir}")
        print(f"TOTP Secret: {res.totp_secret}")
        print(f"TOTP URI:    {res.totp_uri}")
        password = pw1.encode("utf-8")
    else:
        password = getpass.getpass("Password: ").encode("utf-8")

    totp_code = input("TOTP code: ").strip()
    status = client.unlock(password=password, totp_code=totp_code)
    print(f"Unlocked for {int(status.remaining_seconds)}s.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for pj."""
    disable_core_dumps()
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = _COMMAND_ALIASES.get(args.command, args.command)
    client = SessionClient()

    try:
        if command == "unlock":
            return _handle_unlock(client)
        if command == "lock":
            client.lock()
            print("Locked.")
            return 0
        if command == "status":
            st = client.status()
            if st.unlocked:
                print(f"Unlocked ({int(st.remaining_seconds)}s remaining)")
            else:
                print("Locked")
            return 0

        _require_unlocked(client)

        if command == "new":
            entry = create_entry_with_editor(client, tags=args.tag)
            if entry is not None:
                print(entry.id)
            return 0
        if command == "edit":
            entry = edit_entry_with_editor(client, args.id, tags=args.tag)
            print(entry.id)
            return 0
        if command == "view":
            entry = client.load_entry(args.id)
            print(format_entry_view(entry))
            return 0
        if command == "delete":
            client.delete_entry(args.id)
            print(f"Deleted {args.id}")
            return 0
        if command == "search":
            matches = search_entries(
                client,
                query=args.query,
                tags=args.tag,
                after=args.after,
                before=args.before,
            )
            if args.plain:
                for line in format_search_lines(matches):
                    print(line)
                return 0
            selected_id = select_entry_with_fzf(matches)
            if selected_id is None:
                return 0
            if args.id:
                print(selected_id)
            elif args.edit:
                edit_entry_with_editor(client, selected_id)
                print(selected_id)
            else:
                print(format_entry_view(client.load_entry(selected_id)))
            return 0
        if command == "config":
            cfg_action = _CONFIG_ALIASES.get(args.config_action, args.config_action)
            if cfg_action == "get":
                print(get_config_value(args.key))
            elif cfg_action == "set":
                set_config_value(args.key, args.value)
            return 0

        return 1
    except (SessionError, SetupError, CryptoError, StorageError, EditorError, SearchError, ConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
