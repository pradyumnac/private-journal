"""Thin Click-based CLI wrapper for private-journal (pj)."""

from __future__ import annotations

import io
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click
import qrcode
from click.shell_completion import get_completion_class

from private_journal.config import ConfigError, get_config_value, load_config, set_config_value
from private_journal.crypto import CryptoError, disable_core_dumps
from private_journal.daemon import SessionClient, SessionError
from private_journal.editor import (
    EditorError,
    create_entry_with_editor,
    edit_entry_with_editor,
    format_entry_view,
)
from private_journal.search import (
    SearchError,
    format_search_lines,
    resolve_entry_id,
    search_entries,
    select_entry_action_with_fzf,
    select_entry_with_fzf,
)
from private_journal.setup import SetupError, initialize_vault
from private_journal.storage import StorageError


def _get_version() -> str:
    try:
        return version("private-journal")
    except PackageNotFoundError:
        return "0.1.1"


class AliasedGroup(click.Group):
    """Click Group supporting short command aliases displayed in --help."""

    def __init__(self, *args, aliases: dict[str, str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._aliases = aliases or {}
        self._reverse_aliases: dict[str, list[str]] = {}
        for alias, target in self._aliases.items():
            self._reverse_aliases.setdefault(target, []).append(alias)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        target = self._aliases.get(cmd_name)
        if target is not None:
            return super().get_command(ctx, target)
        return None

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands = []
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            aliases = self._reverse_aliases.get(subcommand, [])
            label = f"{subcommand} ({', '.join(aliases)})" if aliases else subcommand
            commands.append((label, cmd))

        if commands:
            limit = formatter.width - 6 - max(len(label) for label, _ in commands)
            rows = [(label, cmd.get_short_help_str(limit=max(limit, 20))) for label, cmd in commands]
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def _require_unlocked(client: SessionClient) -> None:
    if not client.status().unlocked:
        raise SessionError("Session is locked. Run 'pj unlock' first.")


def _pick_or_resolve_id(client: SessionClient, entry_id: str | None) -> str | None:
    if entry_id is not None:
        return resolve_entry_id(client, entry_id)
    entries = search_entries(client)
    return select_entry_with_fzf(entries)


def _delete_with_confirm(client: SessionClient, entry_id: str, yes: bool) -> None:
    if not yes and not click.confirm(f"Delete entry {entry_id}?", default=False, err=True):
        return
    client.delete_entry(entry_id)
    click.echo(f"Deleted {entry_id}")


@click.group(
    cls=AliasedGroup,
    aliases={
        "su": "setup",
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
    },
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Private, single-user, 2FA-gated encrypted CLI journal.",
    epilog=(
        "\b\n"
        "Examples:\n"
        "  pj setup (su)                   Initialize vault password + TOTP\n"
        "  pj unlock (ul)                  Start timed 2FA session\n"
        "  pj new (n) --tag work           Create a new entry in sandboxed nvim\n"
        "  pj search (s, ls) \"argon2\"      Search entries and pick via fzf\n"
        "  pj view (v) / edit (e) / d      Act on entry (uses fzf if ID omitted)\n"
        "  pj status (st) / pj lock (lk)   Check TTL or lock immediately"
    ),
)
@click.version_option(version=_get_version(), prog_name="pj")
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.obj = SessionClient()


@cli.command(help="Initialize a new vault with password and optional TOTP.")
@click.option("--reset", is_flag=True, help="Reset existing vault password/TOTP challenge after confirmation.")
@click.option("--totp/--no-totp", default=None, help="Enable or disable Google Authenticator TOTP 2FA.")
def setup(reset: bool, totp: bool | None) -> None:
    cfg = load_config()
    data_dir = Path(str(cfg["data_dir"]))
    vault_file = data_dir / "vault.json"

    if vault_file.exists():
        if not reset:
            raise SetupError(f"Vault already exists at {vault_file}. Use --reset to replace the challenge.")
        if not click.confirm(
            f"Vault already exists at {vault_file}. Resetting replaces the password/TOTP challenge and makes existing entries unreadable. Continue?",
            default=False,
            err=True,
        ):
            return

    pw1 = click.prompt("Create vault password", hide_input=True, err=True)
    pw2 = click.prompt("Confirm vault password", hide_input=True, err=True)
    if pw1 != pw2:
        raise SetupError("Passwords do not match.")
    enable_totp = (
        totp
        if totp is not None
        else click.confirm("Enable Google Authenticator TOTP 2FA?", default=False, err=True)
    )
    res = initialize_vault(
        password=pw1.encode("utf-8"),
        data_dir=data_dir,
        reset=reset,
        enable_totp=enable_totp,
    )
    click.echo(f"Vault initialized at {res.data_dir}")
    if enable_totp:
        click.echo(f"TOTP Secret: {res.totp_secret}")
        click.echo(f"TOTP URI:    {res.totp_uri}")
        qr = qrcode.QRCode(border=1)
        qr.add_data(res.totp_uri)
        qr_buf = io.StringIO()
        qr.print_ascii(out=qr_buf, invert=True)
        click.echo(qr_buf.getvalue(), nl=False)
        del qr, qr_buf, res


@cli.command(help="Verify credentials and start timed session.")
@click.pass_obj
def unlock(client: SessionClient) -> None:
    cfg = load_config()
    data_dir = Path(str(cfg["data_dir"]))
    vault_file = data_dir / "vault.json"

    if not vault_file.exists():
        raise SetupError("Vault is not initialized. Run 'pj setup' first.")

    meta = json.loads(vault_file.read_text(encoding="utf-8"))
    totp_enabled = bool(meta.get("totp_enabled", cfg.get("totp_enabled", False)))

    password = click.prompt("Password", hide_input=True, err=True).encode("utf-8")
    totp_code = click.prompt("TOTP code", err=True).strip() if totp_enabled else ""
    status = client.unlock(password=password, totp_code=totp_code)
    click.echo(f"Unlocked for {int(status.remaining_seconds)}s.")


@cli.command(help="Lock immediately and wipe session memory. (alias: lk)")
@click.pass_obj
def lock(client: SessionClient) -> None:
    client.lock()
    click.echo("Locked.")


@cli.command(help="Show lock state and remaining timeout.")
@click.pass_obj
def status(client: SessionClient) -> None:
    st = client.status()
    if st.unlocked:
        click.secho(f"Unlocked ({int(st.remaining_seconds)}s remaining)", fg="green")
    else:
        data_dir = Path(load_config()["data_dir"])
        if not (data_dir / "vault.json").exists():
            click.secho("Not initialized (run 'pj setup')", fg="cyan")
        else:
            click.secho("Locked", fg="yellow")


@cli.command(help="Create a new journal entry via sandboxed nvim. (alias: n)")
@click.option("--tag", "tags", multiple=True, help="Tag for the entry (repeatable).")
@click.pass_obj
def new(client: SessionClient, tags: tuple[str, ...]) -> None:
    _require_unlocked(client)
    entry = create_entry_with_editor(client, tags=list(tags))
    if entry is not None:
        click.echo(entry.id)


@cli.command(help="Edit an entry via sandboxed nvim (uses fzf if ID omitted). (alias: e)")
@click.argument("entry_id", metavar="[ID]", required=False, default=None)
@click.option("--tag", "tags", multiple=True, help="Replace entry tags (repeatable).")
@click.pass_obj
def edit(client: SessionClient, entry_id: str | None, tags: tuple[str, ...]) -> None:
    _require_unlocked(client)
    resolved = _pick_or_resolve_id(client, entry_id)
    if resolved is None:
        return
    tag_list = list(tags) if tags else None
    entry = edit_entry_with_editor(client, resolved, tags=tag_list)
    click.echo(entry.id)


@cli.command(help="Display an entry read-only (uses fzf if ID omitted). (alias: v)")
@click.argument("entry_id", metavar="[ID]", required=False, default=None)
@click.pass_obj
def view(client: SessionClient, entry_id: str | None) -> None:
    _require_unlocked(client)
    resolved = _pick_or_resolve_id(client, entry_id)
    if resolved is None:
        return
    entry = client.load_entry(resolved)
    click.echo(format_entry_view(entry))


@cli.command(help="Delete an entry (uses fzf if ID omitted). (aliases: d, rm)")
@click.argument("entry_id", metavar="[ID]", required=False, default=None)
@click.option("-y", "--yes", is_flag=True, help="Skip delete confirmation.")
@click.pass_obj
def delete(client: SessionClient, entry_id: str | None, yes: bool) -> None:
    _require_unlocked(client)
    resolved = _pick_or_resolve_id(client, entry_id)
    if resolved is None:
        return
    _delete_with_confirm(client, resolved, yes=yes)


@cli.command(help="Search/list entries via transient index and fzf. (aliases: s, ls)")
@click.argument("query", required=False, default=None)
@click.option("--tag", "tags", multiple=True, help="Filter by tag (repeatable).")
@click.option("--after", default=None, help="Start date (YYYY-MM-DD, UTC).")
@click.option("--before", default=None, help="End date (YYYY-MM-DD, UTC).")
@click.option("--plain", is_flag=True, help="Print matching lines to stdout.")
@click.option("--id", "id_only", is_flag=True, help="Print selected entry ID.")
@click.option("--edit", "open_edit", is_flag=True, help="Open selected entry in nvim.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation on Ctrl-D delete.")
@click.pass_obj
def search(
    client: SessionClient,
    query: str | None,
    tags: tuple[str, ...],
    after: str | None,
    before: str | None,
    plain: bool,
    id_only: bool,
    open_edit: bool,
    yes: bool,
) -> None:
    _require_unlocked(client)
    matches = search_entries(
        client,
        query=query,
        tags=list(tags),
        after=after,
        before=before,
    )
    if plain:
        for line in format_search_lines(matches):
            click.echo(line)
        return
    if id_only:
        selected = select_entry_with_fzf(matches)
        if selected is not None:
            click.echo(selected)
        return
    if open_edit:
        selected = select_entry_with_fzf(matches)
        if selected is not None:
            edit_entry_with_editor(client, selected)
            click.echo(selected)
        return

    action, selected = select_entry_action_with_fzf(matches)
    if selected is None or action == "cancel":
        return
    if action == "edit":
        edit_entry_with_editor(client, selected)
        click.echo(selected)
    elif action == "delete":
        _delete_with_confirm(client, selected, yes=yes)
    else:
        click.echo(format_entry_view(client.load_entry(selected)))


@cli.group(
    cls=AliasedGroup,
    aliases={"g": "get", "s": "set"},
    help="Read or update configuration (unlock_timeout, data_dir). (alias: cfg)",
)
def config() -> None:
    pass


@config.command(name="get", help="Get a config value. (alias: g)")
@click.argument("key")
@click.pass_obj
def config_get(client: SessionClient, key: str) -> None:
    _require_unlocked(client)
    click.echo(get_config_value(key))


@config.command(name="set", help="Set a config value. (alias: s)")
@click.argument("key")
@click.argument("value")
@click.pass_obj
def config_set(client: SessionClient, key: str, value: str) -> None:
    _require_unlocked(client)
    set_config_value(key, value)


@cli.command(help="Generate shell completion script for bash, zsh, or fish.")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"], case_sensitive=False))
def completion(shell: str) -> None:
    comp_cls = get_completion_class(shell.lower())
    if comp_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")
    comp = comp_cls(cli, {}, "pj", "_PJ_COMPLETE")
    click.echo(comp.source())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for pj."""
    disable_core_dumps()
    if argv is not None and any(flag in argv for flag in ("-h", "--help", "--version")):
        cli.main(args=argv, prog_name="pj")
        return 0
    try:
        cli.main(args=argv, prog_name="pj", standalone_mode=False)
        return 0
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        click.secho(f"Error: {exc.format_message()}", fg="red", err=True)
        return int(exc.exit_code)
    except (SessionError, SetupError, CryptoError, StorageError, EditorError, SearchError, ConfigError) as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
