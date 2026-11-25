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
KEY_CHECK_AD = b"key-check-v1"
KEY_CHECK_PLAINTEXT = b"pj-vault-v1"


class SetupError(Exception):
    """Raised when vault initialization fails."""


@dataclass(frozen=True)
class SetupResult:
    """Result returned after successful first-run vault setup."""

    data_dir: Path
    totp_secret: str
    totp_uri: str


def initialize_vault(
    password: bytes,
    data_dir: Path | None = None,
    reset: bool = False,
    enable_totp: bool = True,
) -> SetupResult:
    """Initialize a new encrypted journal vault and default configuration."""
    if not password:
        raise SetupError("Password must not be empty.")

    target_dir = Path(data_dir) if data_dir is not None else get_xdg_data_dir()
    vault_path = target_dir / "vault.json"
    if vault_path.exists() and not reset:
        raise SetupError(f"Vault already exists at {vault_path}.")

    target_dir.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_SIZE)
    totp_secret = generate_totp_secret() if enable_totp else ""
    totp_uri = (
        pyotp.TOTP(totp_secret).provisioning_uri(
            name="journal",
            issuer_name="private-journal",
        )
        if enable_totp
        else ""
    )

    mk = derive_master_key(password, salt)
    try:
        key_check = aead_encrypt(
            derive_subkey(mk, b"key-check"),
            KEY_CHECK_PLAINTEXT,
            KEY_CHECK_AD,
        )
        encrypted_totp = (
            aead_encrypt(
                derive_subkey(mk, b"totp-secret"),
                totp_secret.encode("utf-8"),
                TOTP_AD,
            )
            if enable_totp
            else None
        )
    finally:
        mk.zeroize()

    vault_payload: dict[str, object] = {
        "version": 1,
        "salt": base64.b64encode(salt).decode("ascii"),
        "key_check": base64.b64encode(key_check).decode("ascii"),
        "totp_enabled": enable_totp,
    }
    if encrypted_totp is not None:
        vault_payload["encrypted_totp"] = base64.b64encode(encrypted_totp).decode("ascii")
    vault_path.write_text(json.dumps(vault_payload, indent=2), encoding="utf-8")
    os.chmod(vault_path, 0o600)

    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_payload = {
        "data_dir": str(target_dir),
        "unlock_timeout": DEFAULT_UNLOCK_TIMEOUT,
        "totp_enabled": enable_totp,
    }
    config_path.write_text(json.dumps(config_payload, indent=2), encoding="utf-8")
    os.chmod(config_path, 0o600)

    return SetupResult(
        data_dir=target_dir,
        totp_secret=totp_secret,
        totp_uri=totp_uri,
    )
