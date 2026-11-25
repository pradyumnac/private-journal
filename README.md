# private-journal

Single-user, 2FA-gated encrypted CLI journal. Entries, metadata, and file names stay encrypted on disk. Editing runs in sandboxed `nvim`, and search runs over a transient in-memory index via `fzf`.

## Overview

- **Encrypted at rest:** Argon2id Master Key, HKDF per-file subkeys, ChaCha20-Poly1305 AEAD, HMAC-SHA256 opaque filenames, `0600` files.
- **2FA unlock:** Password + Google Authenticator TOTP (`valid_window=1`, replay-checked) via a local `0600` Unix socket daemon (15m default TTL).
- **Memory & editor safety:** `mlock`ed key buffer, in-place zeroization, `RLIMIT_CORE=0`, sandboxed `nvim` (`-n -i NONE`, `nobackup`/`noundofile`/`noswapfile`) in `$XDG_RUNTIME_DIR`.
- **Transient search:** In-memory full-text, tag, and date search via `fzf --no-history`.
- **Local stats:** 31/31 BDD-traceable tests passing across 9 library modules.

## Installation

Install from the private GitHub repository using SSH or a configured Git credential helper:

```bash
pip install "git+ssh://git@github.com/pradyumnac/private-journal.git@v0.1.0"
```

Or install as an isolated CLI tool with `uv`:

```bash
uv tool install "git+ssh://git@github.com/pradyumnac/private-journal.git@v0.1.0"
```

## User Documentation

### 1. First-Run Setup and Unlock

All operations except `unlock`, `lock`, `status`, `--help`, and `--version` require an active unlocked session.

```bash
pj unlock
```

On the first run, `pj unlock` prompts for a vault password, prints the TOTP secret and `otpauth://` provisioning URI for Google Authenticator, and prompts for your first 6-digit TOTP code.

### 2. Creating, Viewing, Editing, and Deleting Entries

`pj new` prints the new entry `<id>` on save. You can also get an `<id>` from `pj search --id` or `pj search --plain`, or skip IDs altogether by using `pj search` (opens `view`) and `pj search --edit` (opens `edit`).

```bash
pj new --tag work --tag notes   # Create an entry in sandboxed nvim (prints <id>)
pj view <id>                     # Display decrypted entry read-only
pj edit <id>                     # Edit entry in sandboxed nvim
pj delete <id>                   # Delete encrypted entry
```

`nvim` runs with `-n -i NONE` and `nobackup`/`nowritebackup`/`noundofile`/`noswapfile` on a `0600` file in `$XDG_RUNTIME_DIR`, zero-wiped and deleted on exit.

### 3. Searching and Listing

`list` is folded into `search`. Search runs against a transient in-memory index (never written to disk) and uses `fzf --no-history`.

```bash
pj search                        # Interactive fzf list (select opens view)
pj search "argon2" --tag security
pj search --after 2026-01-01 --before 2026-12-31
pj search --edit                 # Open selected entry in nvim
pj search --id                   # Print selected entry ID
pj search --plain                # Non-interactive stdout output
```

### 4. Session and Configuration

```bash
pj status                        # Show lock state and remaining TTL
pj lock                          # Lock immediately and wipe session memory
pj config get unlock_timeout     # Read config (default: 15m)
pj config set unlock_timeout 30m # Update unlock timeout
pj config set data_dir ~/vault   # Set custom vault directory
```

## Development

```bash
mise run test                    # Run pytest suite (31 tests)
mise run release                 # Test, build wheel/sdist, tag, and create GitHub Release
```
