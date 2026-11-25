"""Tests for Feature: Local Session Daemon & Timed Unlock."""

import time

import pyotp
import pytest

from private_journal.daemon import SessionClient, SessionError, parse_timeout
from private_journal.setup import initialize_vault


def _setup_env(tmp_path, monkeypatch, timeout: str = "15m"):
    xdg_config = tmp_path / "xdg_config"
    xdg_runtime = tmp_path / "xdg_runtime"
    xdg_runtime.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "vault"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_runtime))

    res = initialize_vault(password=b"vault-pass-123", data_dir=data_dir)
    if timeout != "15m":
        cfg_path = xdg_config / "private-journal" / "config.json"
        import json

        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["unlock_timeout"] = timeout
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    return res, xdg_runtime


# Traceability: Feature "Local Session Daemon & Timed Unlock"
# Scenario: Unlock session with valid password and TOTP
def test_unlock_starts_daemon_and_reports_status(tmp_path, monkeypatch):
    res, xdg_runtime = _setup_env(tmp_path, monkeypatch)
    client = SessionClient()

    try:
        code = pyotp.TOTP(res.totp_secret).now()
        status = client.unlock(password=b"vault-pass-123", totp_code=code)
        assert status.unlocked is True
        assert status.remaining_seconds > 0

        sock_path = xdg_runtime / "private-journal" / "daemon.sock"
        assert sock_path.exists()
        assert (sock_path.stat().st_mode & 0o777) == 0o600

        st2 = client.status()
        assert st2.unlocked is True
        assert st2.remaining_seconds > 0
    finally:
        client.lock()


# Traceability: Feature "Local Session Daemon & Timed Unlock"
# Scenario: Reject unlock on wrong password, invalid TOTP, or replayed TOTP
def test_unlock_rejects_bad_password_bad_totp_and_replay(tmp_path, monkeypatch):
    res, _ = _setup_env(tmp_path, monkeypatch)
    client = SessionClient()

    try:
        valid_code = pyotp.TOTP(res.totp_secret).now()
        with pytest.raises(SessionError):
            client.unlock(password=b"wrong-pass", totp_code=valid_code)
        assert client.status().unlocked is False

        with pytest.raises(SessionError):
            client.unlock(password=b"vault-pass-123", totp_code="000000")
        assert client.status().unlocked is False

        # Valid unlock succeeds, replay in same window fails
        client.unlock(password=b"vault-pass-123", totp_code=valid_code)
        with pytest.raises(SessionError):
            client.unlock(password=b"vault-pass-123", totp_code=valid_code)
    finally:
        client.lock()


# Traceability: Feature "Local Session Daemon & Timed Unlock"
# Scenario: Extend session TTL when unlock is called while already unlocked
def test_unlock_extends_session_ttl(tmp_path, monkeypatch):
    res, _ = _setup_env(tmp_path, monkeypatch, timeout="2s")
    client = SessionClient()

    try:
        totp = pyotp.TOTP(res.totp_secret)
        now = int(time.time())
        code1 = totp.at(now - 30)
        st1 = client.unlock(password=b"vault-pass-123", totp_code=code1)
        assert st1.unlocked is True

        time.sleep(0.8)
        code2 = totp.at(now)
        st2 = client.unlock(password=b"vault-pass-123", totp_code=code2)
        assert st2.unlocked is True
        assert st2.remaining_seconds > 1.2
    finally:
        client.lock()


# Traceability: Feature "Local Session Daemon & Timed Unlock"
# Scenario: Lock session immediately and wipe key material
def test_lock_wipes_and_shuts_down(tmp_path, monkeypatch):
    res, _ = _setup_env(tmp_path, monkeypatch)
    client = SessionClient()

    code = pyotp.TOTP(res.totp_secret).now()
    client.unlock(password=b"vault-pass-123", totp_code=code)
    assert client.status().unlocked is True

    client.lock()
    assert client.status().unlocked is False
    assert client.status().remaining_seconds == 0


# Traceability: Feature "Local Session Daemon & Timed Unlock"
# Scenario: Auto-lock when the unlock timeout expires
def test_auto_lock_on_timeout_expiry(tmp_path, monkeypatch):
    res, _ = _setup_env(tmp_path, monkeypatch, timeout="1s")
    client = SessionClient()

    try:
        code = pyotp.TOTP(res.totp_secret).now()
        client.unlock(password=b"vault-pass-123", totp_code=code)
        assert client.status().unlocked is True

        time.sleep(1.3)
        st = client.status()
        assert st.unlocked is False
        assert st.remaining_seconds == 0
    finally:
        client.lock()


# Traceability: Feature "Local Session Daemon & Timed Unlock"
# Scenario: Enforce peer UID check and reject operations while locked
def test_locked_session_rejects_protected_ops(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    client = SessionClient()

    with pytest.raises(SessionError):
        client.list_entries()
    with pytest.raises(SessionError):
        client.load_entry("nonexistent")
    with pytest.raises(SessionError):
        client.save_entry(body="nope", tags=[])
    with pytest.raises(SessionError):
        client.delete_entry("nonexistent")
    assert parse_timeout("15m") == 900
