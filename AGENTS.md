# AGENTS.md

## Documentation & Scope

- `AGENTS.md` is the only markdown file in this setup. Do not create other `.md` files.
- Keep all writing minimal, understated, and clear.
- Resist scope creep strictly. Polish the core workflow only.

## Project

- Private, single-user, CLI-only encrypted journal.
- Single CLI entry point: `pj` (`src/private_journal/`).
- Tooling: `mise` (`mise.toml`) for version pinning, `uv` for dependency management.
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

- `unlock` — Verify password + TOTP and start/extend the timed session.
- `lock` — Lock immediately and wipe session memory.
- `status` — Show lock state and remaining timeout.
- `new` — Create a new entry via sandboxed `nvim`.
- `edit` — Edit an entry via sandboxed `nvim`.
- `view` — Display an entry read-only.
- `delete` — Delete an entry.
- `search` — Run transient full-text search with tag/date filters and list entries via `fzf` (`--no-history`, opens `view` on select, `--edit`/`--id`/`--plain` options).
- `config get` / `config set` — Read and update configuration (`unlock_timeout` default `15m`, `data_dir` default XDG).

## Roadmap

1. First-run setup (password, TOTP provisioning, XDG/custom path)
2. Crypto layer (`cryptography` Argon2id, HKDF, AEAD, `pyotp` replay check, `mlock`/zeroize key buffers, `RLIMIT_CORE=0`)
3. Encrypted storage and filename encryption
4. Local session daemon (Unix socket, timed `unlock`, `lock`, `status`)
5. Sandboxed `nvim` integration (`new`, `edit`) and read-only `view`
6. Transient full-text and tag indexing (`search` + filters)
7. Config management (`config get` / `config set`) and CLI QoL flags
