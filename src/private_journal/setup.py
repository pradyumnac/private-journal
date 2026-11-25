"""First-run vault setup and TOTP provisioning."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pyotp

from private_journal.crypto import (
    SALT_SIZE,
    aead_encrypt,
    derive_master_key,
    derive_subkey,
    generate_totp_secret,
)
from private_journal.paths import get_config_path, get_xdg_data_dir

DEFAULT_UNLOCK_TIMEOUT = "15m"
TOTP_AD = b"totp-secret-v1"


class SetupError(Exception):
    """Raised when vault initialization fails."""


@dataclass(frozen=True)
class SetupResult:
    """Result returned after successful first-run vault setup."""

    data_dir: Path
    totp_secret: str
    totp_uri: str


def initialize_vault(password: bytes, data_dir: Path | None = None) -> SetupResult:
    """Initialize a new encrypted journal vault and default configuration."""
    if not password:
        raise SetupError("Password must not be empty.")

    target_dir = Path(data_dir) if data_dir is not None else get_xdg_data_dir()
    vault_path = target_dir / "vault.json"
    if vault_path.exists():
        raise SetupError(f"Vault already exists at {vault_path}.")

    target_dir.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_SIZE)
    totp_secret = generate_totp_secret()
    totp_uri = pyotp.TOTP(totp_secret).provisioning_uri(
        name="journal",
        issuer_name="private-journal",
    )

    mk = derive_master_key(password, salt)
    try:
        totp_subkey = derive_subkey(mk, b"totp-secret")
        encrypted_totp = aead_encrypt(
            totp_subkey,
            totp_secret.encode("utf-8"),
            TOTP_AD,
        )
    finally:
        mk.zeroize()

    vault_payload = {
        "version": 1,
        "salt": base64.b64encode(salt).decode("ascii"),
        "encrypted_totp": base64.b64encode(encrypted_totp).decode("ascii"),
    }
    vault_path.write_text(json.dumps(vault_payload, indent=2), encoding="utf-8")
    os.chmod(vault_path, 0o600)

    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_payload = {
        "data_dir": str(target_dir),
        "unlock_timeout": DEFAULT_UNLOCK_TIMEOUT,
    }
    config_path.write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
    os.chmod(config_path, 0o600)

    return SetupResult(
        data_dir=target_dir,
        totp_secret=totp_secret,
        totp_uri=totp_uri,
    )
