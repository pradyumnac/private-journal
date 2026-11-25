"""Tests for Feature: Crypto, Key Derivation, TOTP & Memory Safety."""

import os
import resource
import time

import pyotp
import pytest

from private_journal.crypto import (
    CryptoError,
    SecureKeyBuffer,
    aead_decrypt,
    aead_encrypt,
    derive_master_key,
    derive_subkey,
    disable_core_dumps,
    generate_totp_secret,
    verify_totp_code,
)


# Traceability: Feature "Crypto, Key Derivation, TOTP & Memory Safety"
# Scenario: Derive Master Key with Argon2id and per-item subkeys with HKDF
def test_argon2id_master_key_and_hkdf_subkeys():
    salt = os.urandom(16)
    other_salt = os.urandom(16)
    password = b"correct-horse-battery-staple"

    mk1 = derive_master_key(password, salt)
    mk2 = derive_master_key(password, salt)
    mk_diff_pw = derive_master_key(b"different-password", salt)
    mk_diff_salt = derive_master_key(password, other_salt)

    try:
        assert len(mk1) == 32
        assert mk1.read_bytes() == mk2.read_bytes()
        assert mk1.read_bytes() != mk_diff_pw.read_bytes()
        assert mk1.read_bytes() != mk_diff_salt.read_bytes()

        sub1 = derive_subkey(mk1, b"totp-secret")
        sub2 = derive_subkey(mk1, b"entry:001")
        sub3 = derive_subkey(mk1, b"entry:002")
        assert len(sub1) == 32
        assert len({sub1, sub2, sub3}) == 3
    finally:
        for buf in (mk1, mk2, mk_diff_pw, mk_diff_salt):
            buf.zeroize()


# Traceability: Feature "Crypto, Key Derivation, TOTP & Memory Safety"
# Scenario: Encrypt and decrypt payloads with AEAD (ChaCha20-Poly1305)
def test_aead_encrypt_decrypt_and_tamper_detection():
    subkey = os.urandom(32)
    plaintext = b"private journal entry content"
    ad = b"entry-v1"

    blob1 = aead_encrypt(subkey, plaintext, ad)
    blob2 = aead_encrypt(subkey, plaintext, ad)

    assert blob1 != blob2
    assert aead_decrypt(subkey, blob1, ad) == plaintext
    assert aead_decrypt(subkey, blob2, ad) == plaintext

    # Tamper ciphertext
    tampered_ct = bytearray(blob1)
    tampered_ct[-1] ^= 0xFF
    with pytest.raises(CryptoError):
        aead_decrypt(subkey, bytes(tampered_ct), ad)

    # Tamper nonce
    tampered_nonce = bytearray(blob1)
    tampered_nonce[0] ^= 0xFF
    with pytest.raises(CryptoError):
        aead_decrypt(subkey, bytes(tampered_nonce), ad)

    # Tamper associated data
    with pytest.raises(CryptoError):
        aead_decrypt(subkey, blob1, b"wrong-ad")


# Traceability: Feature "Crypto, Key Derivation, TOTP & Memory Safety"
# Scenario: Verify TOTP and reject replayed timecodes
def test_totp_verify_window_and_replay_rejection():
    secret = generate_totp_secret()
    totp = pyotp.TOTP(secret)
    now = int(time.time())
    code = totp.at(now)

    accepted_tc = verify_totp_code(secret, code, last_timecode=None, now=now)
    assert accepted_tc is not None

    # Replay in same timecode window must fail
    with pytest.raises(CryptoError):
        verify_totp_code(secret, code, last_timecode=accepted_tc, now=now)

    # Invalid code must fail
    with pytest.raises(CryptoError):
        verify_totp_code(secret, "000000", last_timecode=None, now=now)

    # Code outside valid_window=1 (e.g., 90s old) must fail
    old_code = totp.at(now - 90)
    with pytest.raises(CryptoError):
        verify_totp_code(secret, old_code, last_timecode=None, now=now)


# Traceability: Feature "Crypto, Key Derivation, TOTP & Memory Safety"
# Scenario: Secure key buffer mlock, zeroization, and core-dump protection
def test_secure_key_buffer_mlock_zeroize_and_core_dumps():
    disable_core_dumps()
    soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    assert (soft, hard) == (0, 0)

    raw_key = os.urandom(32)
    buf = SecureKeyBuffer(32)
    buf.write(raw_key)
    assert buf.read_bytes() == raw_key

    buf.zeroize()
    assert buf.read_bytes() == b"\x00" * 32
    with pytest.raises(CryptoError):
        buf.write(raw_key)
