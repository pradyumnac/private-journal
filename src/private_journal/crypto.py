"""Standard cryptographic primitives, TOTP verification, and key buffer safety."""

from __future__ import annotations

import ctypes
import ctypes.util
import hmac
import os
import resource
import time

import pyotp
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_SIZE = 32
SALT_SIZE = 16
NONCE_SIZE = 12

_LIBC_NAME = ctypes.util.find_library("c")
_LIBC = ctypes.CDLL(_LIBC_NAME) if _LIBC_NAME else None
if _LIBC is not None:
    _LIBC.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    _LIBC.mlock.restype = ctypes.c_int
    _LIBC.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    _LIBC.munlock.restype = ctypes.c_int


class CryptoError(Exception):
    """Raised on cryptographic, TOTP, or key-buffer failures."""


def disable_core_dumps() -> None:
    """Disable process core dumps to prevent secret leakage."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class SecureKeyBuffer:
    """Mutable key buffer with best-effort mlock and in-place zeroization."""

    def __init__(self, size: int = KEY_SIZE) -> None:
        if size <= 0:
            raise CryptoError("Key buffer size must be positive.")
        self._size = size
        self._buf = (ctypes.c_ubyte * size)()
        self._addr = ctypes.addressof(self._buf)
        self._locked = False
        self._zeroized = False

        if _LIBC is not None:
            if _LIBC.mlock(self._addr, self._size) == 0:
                self._locked = True

    def __len__(self) -> int:
        return self._size

    def write(self, data: bytes | bytearray) -> None:
        """Copy key material into the buffer."""
        if self._zeroized:
            raise CryptoError("Cannot write to a zeroized SecureKeyBuffer.")
        if len(data) != self._size:
            raise CryptoError(f"Expected {self._size} bytes, got {len(data)}.")
        ctypes.memmove(self._addr, bytes(data), self._size)

    def read_bytes(self) -> bytes:
        """Return a copy of the buffer contents."""
        return ctypes.string_at(self._addr, self._size)

    def zeroize(self) -> None:
        """Overwrite the buffer with zeros in place and unlock memory."""
        if not self._zeroized:
            ctypes.memset(self._addr, 0, self._size)
            if self._locked and _LIBC is not None:
                _LIBC.munlock(self._addr, self._size)
                self._locked = False
            self._zeroized = True

    def __enter__(self) -> SecureKeyBuffer:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.zeroize()

    def __del__(self) -> None:
        if hasattr(self, "_zeroized") and not self._zeroized:
            self.zeroize()


def derive_master_key(password: bytes, salt: bytes) -> SecureKeyBuffer:
    """Derive a 32-byte Master Key from password and salt using Argon2id."""
    if not password:
        raise CryptoError("Password must not be empty.")
    if len(salt) != SALT_SIZE:
        raise CryptoError(f"Salt must be {SALT_SIZE} bytes.")

    kdf = Argon2id(
        salt=salt,
        length=KEY_SIZE,
        iterations=2,
        lanes=4,
        memory_cost=65536,
    )
    raw = bytearray(kdf.derive(password))
    buf = SecureKeyBuffer(KEY_SIZE)
    try:
        buf.write(raw)
    finally:
        for i in range(len(raw)):
            raw[i] = 0
    return buf


def derive_subkey(master_key: SecureKeyBuffer, info: bytes) -> bytes:
    """Derive a 32-byte context-bound subkey using HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=None,
        info=info,
    )
    return hkdf.derive(master_key.read_bytes())


def aead_encrypt(key: bytes, plaintext: bytes, associated_data: bytes = b"") -> bytes:
    """Encrypt plaintext with ChaCha20-Poly1305, prepending a 12-byte random nonce."""
    if len(key) != KEY_SIZE:
        raise CryptoError(f"Key must be {KEY_SIZE} bytes.")
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, associated_data)
    return nonce + ciphertext


def aead_decrypt(key: bytes, blob: bytes, associated_data: bytes = b"") -> bytes:
    """Decrypt a nonce-prefixed ChaCha20-Poly1305 blob."""
    if len(key) != KEY_SIZE:
        raise CryptoError(f"Key must be {KEY_SIZE} bytes.")
    if len(blob) <= NONCE_SIZE:
        raise CryptoError("Encrypted blob is too short.")
    nonce = blob[:NONCE_SIZE]
    ciphertext = blob[NONCE_SIZE:]
    try:
        return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, associated_data)
    except InvalidTag as exc:
        raise CryptoError("Decryption failed: authentication tag mismatch.") from exc


def generate_totp_secret() -> str:
    """Generate a Google Authenticator-compatible base32 TOTP secret."""
    return pyotp.random_base32()


def verify_totp_code(
    secret: str,
    code: str,
    last_timecode: int | None,
    now: int | None = None,
) -> int:
    """Verify a TOTP code within valid_window=1 and reject replayed timecodes."""
    totp = pyotp.TOTP(secret)
    check_time = int(now) if now is not None else int(time.time())
    base_tc = totp.timecode(check_time)

    for offset in (0, -1, 1):
        candidate_tc = base_tc + offset
        if candidate_tc < 0:
            continue
        expected = totp.generate_otp(candidate_tc)
        if hmac.compare_digest(expected, code.strip()):
            if last_timecode is not None and candidate_tc <= last_timecode:
                raise CryptoError("TOTP code already used in this time window.")
            return candidate_tc

    raise CryptoError("Invalid TOTP code.")
