# AGENTS.md

## Documentation & Scope

- `AGENTS.md` and `README.md` are the only markdown files in this setup. Do not create other `.md` files.
- Keep all writing minimal, understated, and clear.
- Resist scope creep strictly. Polish the core workflow only.

## Project

- Private, single-user, CLI-only encrypted journal.
- Single CLI entry point: `pj` (`src/private_journal/`).
- Tooling: `mise` (`mise.toml`) for version pinning and tasks (`mise run test`, `mise run demo`, `mise run bump`, `mise run release`), `uv` for dependency management and packaging (`uv build`).
- Release: Build `pip`-installable wheel and sdist with `uv build` and distribute via tagged GitHub Releases (`v*` + floating `latest` tag, `gh release create`) with `git-cliff` release notes (do not create `CHANGELOG.md`). Do not upload to PyPI.
- Pre-release gate: Before every release (`mise run release`), run `uv run pytest -v` and `uv run python scripts/demo_lifecycle.py -v --fast` and agentically verify the full lifecycle text output matches expected behavior.
- CI & Hooks: No GitHub Actions/CI executions. Pre-commit hook (`.githooks/pre-commit`) runs `uv run pytest -q`.
- Runtime: Python, `cryptography`, `pyotp`.
- External tools: `nvim` (editor), `fzf` (interactive search/list).

## Testing & Workflow

- **Strict TDD:** Follow red-green-refactor strictly.
- **BDD-first specs:** Start every feature with full-English BDD test cases approved by the user.
- **Dedicated spec file:** Maintain approved English BDD test cases in a dedicated non-markdown file (e.g., `tests/bdd.feature`).
- **Traceability:** Derive all automated code tests directly from those BDD test cases and keep them traceable.

## Architecture

- **Library-first:** Keep all core logic (crypto, storage, session daemon, indexing, CRUD, config) in a modular library.
- **Thin CLI:** The CLI parses arguments, handles user prompts, and invokes `nvim`. No business logic in the CLI.
- **Session daemon:** A local daemon over a Unix domain socket holds the unlocked Master Key and transient index in memory, enforces the unlock timeout, and clears secrets on `lock`, timeout, or exit.
- **Sandboxed `nvim`:** Launch `nvim` with `-n -i NONE` and `nobackup`/`noundofile` on a memory-backed temp file (`$XDG_RUNTIME_DIR` / `tmpfs`). Wipe and delete the temp file immediately on exit.

## Security & Storage

- **Encrypt at rest:** Encrypt all entry contents, metadata (`id`, `created_at`, `updated_at`, `tags`), and file names on disk as one encrypted blob per entry. Never write plaintext to disk.
- **Encrypt in flight:** Keep transient index and IPC data protected; decrypt only during an active unlocked session.
- **Key derivation & 2FA:** First-run setup configures a password and a Google Authenticator-compatible TOTP secret. Derive the Master Key with Argon2id (`cryptography`). Encrypt the TOTP secret at rest and verify it on `unlock`.
- **Standard crypto:** Use `cryptography` (Argon2id, HKDF per-file subkeys, ChaCha20-Poly1305 or AES-GCM) and `pyotp`. Do not roll custom crypto.
- **TOTP hardening:** Enforce `valid_window=1` max and reject replayed timecodes within the same window.
- **Memory safety:** Prevent memory leaks. Disable core dumps (`RLIMIT_CORE=0`). Hold cryptographic keys in `mlock`ed mutable buffers (scoped to keys to respect `RLIMIT_MEMLOCK`) and zeroize them on `lock`, timeout, or drop. Minimize plaintext lifetimes in Python memory.
- **Storage path:** Use a fixed XDG data/config path, or a user-configured data path.
- **Tag-only organization:** Use tags for all categorization and discovery. Do not add folder or category hierarchies.

## CLI Contract

All operations require an active timeout-based `unlock` session, except `unlock`, `lock`, `status`, and QoL flags (`--help`, `--version`).

- `unlock` (`ul`) — Verify password + TOTP and start/extend the timed session.
- `lock` (`lk`) — Lock immediately and wipe session memory.
- `status` (`st`) — Show lock state and remaining timeout.
- `new` (`n`) — Create a new entry via sandboxed `nvim`.
- `edit` (`e`) — Edit an entry via sandboxed `nvim`.
- `view` (`v`) — Display an entry read-only.
- `delete` (`d`, `rm`) — Delete an entry.
- `search` (`s`, `ls`) — Run transient full-text search with tag/date filters and list entries via `fzf` (`--no-history`, opens `view` on select, `--edit`/`--id`/`--plain` options).
- `config get` / `config set` (`cfg g` / `cfg s`) — Read and update configuration (`unlock_timeout` default `15m`, `data_dir` default XDG).

## Roadmap

| Feature | Type | Status |
| --- | --- | --- |
| No pending items (core roadmap complete) | — | — |
