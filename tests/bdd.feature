Feature: First-run Setup & Vault Initialization

  Scenario: Initialize a new vault in the default XDG path
    Given no journal vault exists at the configured XDG data path
    When the user runs first-run setup with a valid password
    Then a new Argon2id salt and vault metadata are created at the XDG data path
    And a Google Authenticator-compatible TOTP secret and provisioning URI are generated
    And the TOTP secret is stored encrypted with a subkey derived from the Master Key
    And no plaintext password or plaintext TOTP secret is written to disk

  Scenario: Initialize a new vault in a custom data path
    Given a user-configured data path
    When the user runs first-run setup with a valid password and custom path
    Then the vault metadata and encrypted TOTP secret are stored under the custom path
    And the default config records the custom data path and 15m unlock timeout

  Scenario: Reject re-initialization of an existing vault
    Given an already initialized journal vault
    When first-run setup is invoked again on the same data path
    Then setup fails with an already-initialized error without overwriting existing data

  Scenario: Reject weak or empty password during setup
    Given no journal vault exists
    When the user attempts first-run setup with an empty password
    Then setup fails with a validation error and writes nothing to disk


Feature: Crypto, Key Derivation, TOTP & Memory Safety

  Scenario: Derive Master Key with Argon2id and per-item subkeys with HKDF
    Given a password and a 16-byte random salt
    When the Master Key is derived using Argon2id
    Then the same password and salt deterministically produce the same 32-byte Master Key
    And a different password or salt produces a different Master Key
    And HKDF derives distinct 32-byte subkeys for different context labels or entry IDs

  Scenario: Encrypt and decrypt payloads with AEAD (ChaCha20-Poly1305)
    Given a 32-byte subkey, plaintext bytes, and associated data
    When the payload is encrypted and then decrypted with the same subkey and associated data
    Then the decrypted output matches the original plaintext
    And tampering with the ciphertext, nonce, or associated data causes decryption to fail
    And encrypting the same plaintext twice produces different ciphertexts due to unique nonces

  Scenario: Verify TOTP and reject replayed timecodes
    Given a provisioned TOTP secret
    When a valid TOTP code is verified within valid_window=1
    Then verification succeeds and records the accepted timecode
    And submitting the same TOTP code again for the same timecode is rejected as a replay
    And an invalid TOTP code or code outside valid_window=1 is rejected

  Scenario: Secure key buffer mlock, zeroization, and core-dump protection
    Given process core dumps are disabled (RLIMIT_CORE=0)
    When a 32-byte key is stored in a SecureKeyBuffer
    Then the buffer memory is locked against swapping (mlock) when permitted by RLIMIT_MEMLOCK
    And closing or zeroizing the buffer overwrites all key bytes with zeros in place


Feature: Encrypted Storage & Filename Encryption

  Scenario: Save an entry as a single encrypted blob with an encrypted filename
    Given an unlocked Master Key and a vault data directory
    When a journal entry with body and tags is saved
    Then a single encrypted file is written under the entries directory with 0600 permissions
    And the filename on disk is an opaque HMAC-derived identifier that reveals no entry ID, date, title, or tags
    And the file contents contain no plaintext body, tags, timestamps, or entry ID

  Scenario: Load and decrypt an entry by ID
    Given a saved encrypted entry in the vault
    When the entry is loaded using the Master Key and entry ID
    Then the decrypted entry returns the exact id, created_at, updated_at, tags, and body
    And loading with a wrong Master Key fails with a storage error

  Scenario: Update an existing entry preserving created_at and updating updated_at
    Given a saved encrypted entry
    When the entry body or tags are updated and saved
    Then the encrypted file is updated atomically in place
    And created_at remains unchanged while updated_at advances
    And loading the entry returns the updated body and tags

  Scenario: List all decrypted entries from encrypted storage
    Given multiple saved encrypted entries in the vault
    When all entries are listed using the Master Key
    Then every saved entry is decrypted and returned with its metadata and body
    And no plaintext files or index manifests exist on disk

  Scenario: Delete an entry and reject missing or tampered files
    Given a saved encrypted entry in the vault
    When the entry is deleted by ID
    Then its encrypted file is removed from disk and subsequent loads fail with a storage error
    And tampering with an encrypted entry file or swapping two entry files fails authentication


Feature: Local Session Daemon & Timed Unlock

  Scenario: Unlock session with valid password and TOTP
    Given an initialized vault and a stopped session daemon
    When unlock is invoked with the valid password and a valid TOTP code
    Then the daemon starts on a 0600 Unix domain socket under XDG_RUNTIME_DIR
    And holds the Master Key in a locked SecureKeyBuffer with the configured timeout
    And status reports unlocked with a positive remaining TTL

  Scenario: Reject unlock on wrong password, invalid TOTP, or replayed TOTP
    Given an initialized vault
    When unlock is invoked with an invalid password or invalid TOTP code
    Then unlock fails with an authentication error and no unlocked session is held
    And replaying the same TOTP code within the same window is rejected

  Scenario: Extend session TTL when unlock is called while already unlocked
    Given an active unlocked session
    When unlock is invoked again with valid password and TOTP
    Then the session timeout is extended from the current time

  Scenario: Lock session immediately and wipe key material
    Given an active unlocked session
    When lock is invoked
    Then the daemon zeroizes the Master Key buffer, clears transient state, and shuts down
    And status reports locked

  Scenario: Auto-lock when the unlock timeout expires
    Given an active unlocked session with a short timeout
    When the timeout elapses without activity
    Then the daemon automatically zeroizes the Master Key buffer and locks the session
    And subsequent status reports locked

  Scenario: Enforce peer UID check and reject operations while locked
    Given a locked or non-running session daemon
    When any protected operation (save, load, list, delete) is requested via the session client
    Then the request is rejected with a session-locked error


Feature: Sandboxed nvim Integration & Read-Only View

  Scenario: Create a new entry via sandboxed nvim
    Given an active unlocked session
    When the user creates a new entry with tags via sandboxed nvim
    Then nvim is invoked with -n -i NONE and nobackup/noundofile/noswapfile on a 0600 temp file under XDG_RUNTIME_DIR
    And the saved content is encrypted and stored via the session daemon
    And the temp file is overwritten with zeros and deleted immediately after nvim exits

  Scenario: Abort new entry when buffer is empty or unchanged
    Given an active unlocked session
    When the user exits nvim without writing content
    Then no entry is saved to the vault
    And the temp file is wiped and deleted immediately

  Scenario: Edit an existing entry via sandboxed nvim
    Given an active unlocked session and an existing encrypted entry
    When the user edits the entry via sandboxed nvim and saves changes
    Then the temp file is populated with the decrypted content under XDG_RUNTIME_DIR
    And the updated content is re-encrypted and saved while preserving created_at
    And the temp file is overwritten with zeros and deleted immediately even if nvim fails

  Scenario: Display an entry read-only with view
    Given an active unlocked session and an existing encrypted entry
    When the user views the entry by ID
    Then the entry metadata (id, timestamps, tags) and body are formatted for read-only output
    And no temp file is created on disk or tmpfs


Feature: Transient Full-Text & Tag Search with fzf

  Scenario: Build transient in-memory index and search by full-text query
    Given an active unlocked session with multiple encrypted entries
    When the user searches with a full-text query
    Then a transient in-memory index matches entries containing the query terms (case-insensitive)
    And no index file is written to disk
    And locking the session clears the transient index from memory

  Scenario: Filter entries by tags and date range, or list all when unfiltered
    Given an active unlocked session with entries across multiple tags and dates
    When the user searches with tag filters and/or after/before date bounds
    Then only entries matching all specified tags and within the date range are returned
    And searching with no query or filters lists all entries sorted newest first

  Scenario: Select an entry interactively via fzf with history disabled
    Given matching search results
    When interactive fzf selection is invoked
    Then fzf is launched with --no-history and displays date, one-line snippet, and tags appended in brackets ([tag1, tag2])
    And selecting a line returns the corresponding entry ID
    And cancelling fzf returns None without error


Feature: Config Management & Thin CLI (pj)

  Scenario: Show help and version without an unlocked session
    Given the pj CLI entry point
    When the user runs pj --help or pj --version
    Then the CLI exits 0 with usage or version output without requiring unlock

  Scenario: Initialize vault via pj setup and require setup before unlock
    Given no initialized vault exists
    When the user runs pj status before setup
    Then status reports Not initialized (run 'pj setup')
    When the user runs pj unlock before setup
    Then unlock fails with an error instructing the user to run pj setup
    When the user runs pj setup and completes password prompts
    Then the vault is initialized, the TOTP secret, URI, and terminal QR code are shown (without writing QR files or plaintext secrets to disk), and pj unlock succeeds

  Scenario: Reject setup on existing vault unless --reset is confirmed
    Given an already initialized vault
    When the user runs pj setup without --reset
    Then setup fails with an already-initialized error
    When the user runs pj setup --reset and confirms the challenge reset
    Then the vault challenge (password and TOTP secret) is reset and a new TOTP URI is shown

  Scenario: Opt-in TOTP during setup and password-only unlock when disabled
    Given no initialized vault exists
    When the user runs pj setup and declines TOTP (or omits --totp)
    Then config.json records totp_enabled as false and no TOTP secret is provisioned
    And pj unlock succeeds with password only and rejects wrong passwords
    When the user runs pj setup --reset --totp
    Then config.json records totp_enabled as true and a TOTP secret and URI are provisioned

  Scenario: Reject protected CLI commands while locked
    Given a locked or unauthenticated session
    When the user runs new, edit, view, delete, search, or config get/set
    Then the CLI exits non-zero with a locked-session error

  Scenario: Read and update configuration when unlocked
    Given an active unlocked session
    When the user runs pj config get unlock_timeout and pj config set unlock_timeout 30m
    Then config get returns the current value and config set validates and persists the new value
    And unknown config keys or invalid timeouts are rejected

  Scenario: Execute full CLI entry workflow (new, view, edit, search, delete, status, lock)
    Given an active unlocked session
    When the user creates, views, edits, searches (--plain and fzf), and deletes entries via pj
    Then each command delegates to the library and returns expected exit codes and output
    And pj status reports unlocked before pj lock, and locked after pj lock

  Scenario: Support short semantic aliases for all CLI subcommands
    Given the pj CLI entry point
    When the user invokes short aliases (ul, lk, st, n, e, v, d, rm, s, ls, cfg)
    Then each alias dispatches to its corresponding subcommand identically

  Scenario: Verify full lifecycle demo script runs cleanly end-to-end
    Given the standalone demo script scripts/demo_lifecycle.py
    When executed with --fast in an isolated tmp sandbox
    Then it exits 0, completes all 7 lifecycle stages, and removes its tmp sandbox


Feature: Click CLI, Shell Completion & ID-less Discovery

  Scenario: Resolve entries by unique ID prefix and reject ambiguous prefixes
    Given an active unlocked session with multiple encrypted entries
    When the user passes a unique 4-character ID prefix to view, edit, or delete
    Then the matching entry is resolved and operated on
    And an ambiguous or unknown prefix fails with a clear error

  Scenario: Launch fzf automatically when view, edit, or delete is called without an ID
    Given an active unlocked session with existing entries
    When the user runs pj view, pj edit, or pj delete with no ID argument
    Then fzf is launched to select an entry
    And delete prompts for confirmation unless --yes is passed

  Scenario: Support multi-action keybinds in interactive search
    Given an active unlocked session with matching entries
    When the user selects an entry in fzf with Enter, Ctrl-E, or Ctrl-D
    Then Enter displays the entry, Ctrl-E opens it in sandboxed nvim, and Ctrl-D deletes it after confirmation

  Scenario: Generate shell completion scripts for bash, zsh, and fish
    Given the click-based pj CLI
    When the user runs pj completion bash, zsh, or fish
    Then the corresponding click shell completion script is printed to stdout

  Scenario: Manage tags on an existing entry without opening nvim
    Given an active unlocked session with an existing entry
    When the user runs pj tag (or t) with --add, --remove, --set, --clear, or no flags
    Then tags are updated and re-encrypted without launching nvim while preserving created_at and body
    And omitting the entry ID launches fzf to select the entry
