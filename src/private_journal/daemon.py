"""Local Unix-socket session daemon and client for timed unlock sessions."""

from __future__ import annotations

import argparse
import base64
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from private_journal.crypto import (
    CryptoError,
    SecureKeyBuffer,
    aead_decrypt,
    derive_master_key,
    derive_subkey,
    disable_core_dumps,
    verify_totp_code,
)
from private_journal.paths import get_config_path, get_xdg_data_dir, get_xdg_runtime_dir
from private_journal.setup import TOTP_AD
from private_journal.storage import (
    JournalEntry,
    StorageError,
    delete_entry,
    list_entries,
    load_entry,
    save_entry,
)

_HEADER_STRUCT = struct.Struct("!I")
_PEERCRED_STRUCT = struct.Struct("3i")


class SessionError(Exception):
    """Raised on session authentication, lock state, or IPC errors."""


@dataclass(frozen=True)
class SessionStatus:
    """Current lock state and remaining timeout in seconds."""

    unlocked: bool
    remaining_seconds: float


def parse_timeout(spec: str) -> float:
    """Parse timeout strings like '15m', '30s', '2h', or plain seconds."""
    text = spec.strip().lower()
    if not text:
        raise SessionError("Empty timeout specification.")
    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0}
    unit = text[-1]
    if unit in multipliers:
        num_str = text[:-1]
        mult = multipliers[unit]
    else:
        num_str = text
        mult = 1.0
    try:
        value = float(num_str) * mult
    except ValueError as exc:
        raise SessionError(f"Invalid timeout specification: {spec}") from exc
    if value <= 0:
        raise SessionError("Timeout must be positive.")
    return value


def _send_msg(conn: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    conn.sendall(_HEADER_STRUCT.pack(len(data)) + data)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = conn.recv(remaining)
        if not chunk:
            raise SessionError("Connection closed unexpectedly.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_msg(conn: socket.socket) -> dict[str, Any]:
    raw_len = _recv_exact(conn, _HEADER_STRUCT.size)
    (length,) = _HEADER_STRUCT.unpack(raw_len)
    if length <= 0 or length > 16 * 1024 * 1024:
        raise SessionError("Invalid IPC message size.")
    raw_body = _recv_exact(conn, length)
    return json.loads(raw_body.decode("utf-8"))


def _entry_to_dict(entry: JournalEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "tags": entry.tags,
        "body": entry.body,
    }


def _dict_to_entry(data: dict[str, Any]) -> JournalEntry:
    return JournalEntry(
        id=str(data["id"]),
        created_at=float(data["created_at"]),
        updated_at=float(data["updated_at"]),
        tags=list(data.get("tags", [])),
        body=str(data["body"]),
    )


class _SessionDaemon:
    """In-memory session holder running over a 0600 Unix domain socket."""

    def __init__(self, socket_path: Path, config_path: Path) -> None:
        self.socket_path = socket_path
        self.config_path = config_path
        self._master_key: SecureKeyBuffer | None = None
        self._expires_at: float = 0.0
        self._last_timecode: int | None = None
        self._running = True

    def _load_config(self) -> tuple[Path, float]:
        data_dir = get_xdg_data_dir()
        timeout_sec = 900.0
        if self.config_path.exists():
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            if "data_dir" in cfg:
                data_dir = Path(cfg["data_dir"])
            if "unlock_timeout" in cfg:
                timeout_sec = parse_timeout(str(cfg["unlock_timeout"]))
        return data_dir, timeout_sec

    def _wipe_session(self) -> None:
        if self._master_key is not None:
            self._master_key.zeroize()
            self._master_key = None
        self._expires_at = 0.0

    def _check_expiry(self) -> None:
        if self._master_key is not None and time.monotonic() >= self._expires_at:
            self._wipe_session()

    def _remaining(self) -> float:
        self._check_expiry()
        if self._master_key is None:
            return 0.0
        return max(0.0, self._expires_at - time.monotonic())

    def _require_unlocked(self) -> tuple[SecureKeyBuffer, Path]:
        self._check_expiry()
        if self._master_key is None:
            raise SessionError("Session is locked.")
        data_dir, _ = self._load_config()
        return self._master_key, data_dir

    def _handle_unlock(self, req: dict[str, Any]) -> dict[str, Any]:
        password = base64.b64decode(req["password_b64"])
        totp_code = str(req["totp_code"])
        data_dir, timeout_sec = self._load_config()
        vault_path = data_dir / "vault.json"
        if not vault_path.exists():
            raise SessionError("Vault is not initialized.")

        meta = json.loads(vault_path.read_text(encoding="utf-8"))
        salt = base64.b64decode(meta["salt"])
        encrypted_totp = base64.b64decode(meta["encrypted_totp"])

        candidate_mk = derive_master_key(password, salt)
        try:
            totp_subkey = derive_subkey(candidate_mk, b"totp-secret")
            secret = aead_decrypt(totp_subkey, encrypted_totp, TOTP_AD).decode("utf-8")
            accepted_tc = verify_totp_code(secret, totp_code, self._last_timecode)
        except CryptoError as exc:
            candidate_mk.zeroize()
            raise SessionError(f"Authentication failed: {exc}") from exc

        self._last_timecode = accepted_tc
        if self._master_key is not None:
            self._master_key.zeroize()
        self._master_key = candidate_mk
        self._expires_at = time.monotonic() + timeout_sec
        return {"ok": True, "unlocked": True, "remaining_seconds": timeout_sec}

    def _handle_request(self, req: dict[str, Any]) -> dict[str, Any]:
        cmd = req.get("cmd")
        if cmd == "status":
            rem = self._remaining()
            return {"ok": True, "unlocked": rem > 0.0, "remaining_seconds": rem}
        if cmd == "unlock":
            return self._handle_unlock(req)
        if cmd == "lock":
            self._wipe_session()
            self._running = False
            return {"ok": True, "unlocked": False, "remaining_seconds": 0.0}

        mk, data_dir = self._require_unlocked()
        if cmd == "save_entry":
            entry = (
                _dict_to_entry(req["entry"])
                if "entry" in req
                else JournalEntry.new(body=str(req["body"]), tags=list(req.get("tags", [])))
            )
            save_entry(data_dir, mk, entry)
            return {"ok": True, "entry": _entry_to_dict(entry)}
        if cmd == "load_entry":
            entry = load_entry(data_dir, mk, str(req["entry_id"]))
            return {"ok": True, "entry": _entry_to_dict(entry)}
        if cmd == "list_entries":
            entries = list_entries(data_dir, mk)
            return {"ok": True, "entries": [_entry_to_dict(e) for e in entries]}
        if cmd == "delete_entry":
            delete_entry(data_dir, mk, str(req["entry_id"]))
            return {"ok": True}

        raise SessionError(f"Unknown command: {cmd}")

    def serve(self) -> None:
        disable_core_dumps()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        if self.socket_path.exists():
            self.socket_path.unlink()

        def _stop_handler(_signum, _frame) -> None:
            self._running = False

        signal.signal(signal.SIGTERM, _stop_handler)
        signal.signal(signal.SIGINT, _stop_handler)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)
        try:
            server.bind(str(self.socket_path))
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o600)
        server.listen(16)
        server.setblocking(False)

        try:
            while self._running:
                self._check_expiry()
                readable, _, _ = select.select([server], [], [], 0.25)
                if not readable:
                    continue
                conn, _ = server.accept()
                conn.setblocking(True)
                with conn:
                    cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _PEERCRED_STRUCT.size)
                    _, peer_uid, _ = _PEERCRED_STRUCT.unpack(cred)
                    if peer_uid != os.getuid():
                        _send_msg(conn, {"ok": False, "error": "Unauthorized peer UID."})
                        continue
                    try:
                        req = _recv_msg(conn)
                        resp = self._handle_request(req)
                    except (SessionError, StorageError, CryptoError, KeyError, ValueError) as exc:
                        resp = {"ok": False, "error": str(exc)}
                    try:
                        _send_msg(conn, resp)
                    except OSError:
                        pass
        finally:
            self._wipe_session()
            server.close()
            if self.socket_path.exists():
                self.socket_path.unlink()


class SessionClient:
    """Client for communicating with the local session daemon."""

    def __init__(self, socket_path: Path | None = None, config_path: Path | None = None) -> None:
        self._socket_path = socket_path
        self._config_path = config_path

    @property
    def socket_path(self) -> Path:
        return self._socket_path or (get_xdg_runtime_dir() / "daemon.sock")

    @property
    def config_path(self) -> Path:
        return self._config_path or get_config_path()

    def _connect(self) -> socket.socket | None:
        if not self.socket_path.exists():
            return None
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.socket_path))
            return sock
        except OSError:
            sock.close()
            return None

    def _ensure_daemon(self) -> None:
        if self._connect() is not None:
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "private_journal.daemon",
                "--socket",
                str(self.socket_path),
                "--config",
                str(self.config_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            conn = self._connect()
            if conn is not None:
                conn.close()
                return
            time.sleep(0.02)
        raise SessionError("Timed out waiting for session daemon to start.")

    def _request(self, payload: dict[str, Any], start_if_needed: bool = False) -> dict[str, Any]:
        if start_if_needed:
            self._ensure_daemon()
        conn = self._connect()
        if conn is None:
            raise SessionError("Session is locked.")
        with conn:
            _send_msg(conn, payload)
            resp = _recv_msg(conn)
        if not resp.get("ok"):
            raise SessionError(resp.get("error", "Session request failed."))
        return resp

    def status(self) -> SessionStatus:
        conn = self._connect()
        if conn is None:
            return SessionStatus(unlocked=False, remaining_seconds=0.0)
        with conn:
            _send_msg(conn, {"cmd": "status"})
            resp = _recv_msg(conn)
        return SessionStatus(
            unlocked=bool(resp.get("unlocked", False)),
            remaining_seconds=float(resp.get("remaining_seconds", 0.0)),
        )

    def unlock(self, password: bytes, totp_code: str) -> SessionStatus:
        resp = self._request(
            {
                "cmd": "unlock",
                "password_b64": base64.b64encode(password).decode("ascii"),
                "totp_code": totp_code,
            },
            start_if_needed=True,
        )
        return SessionStatus(
            unlocked=bool(resp["unlocked"]),
            remaining_seconds=float(resp["remaining_seconds"]),
        )

    def lock(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        with conn:
            _send_msg(conn, {"cmd": "lock"})
            _recv_msg(conn)

    def save_entry(
        self,
        body: str | None = None,
        tags: list[str] | None = None,
        entry: JournalEntry | None = None,
    ) -> JournalEntry:
        if entry is not None:
            payload: dict[str, Any] = {"cmd": "save_entry", "entry": _entry_to_dict(entry)}
        else:
            payload = {"cmd": "save_entry", "body": body or "", "tags": tags or []}
        resp = self._request(payload)
        return _dict_to_entry(resp["entry"])

    def load_entry(self, entry_id: str) -> JournalEntry:
        resp = self._request({"cmd": "load_entry", "entry_id": entry_id})
        return _dict_to_entry(resp["entry"])

    def list_entries(self) -> list[JournalEntry]:
        resp = self._request({"cmd": "list_entries"})
        return [_dict_to_entry(item) for item in resp["entries"]]

    def delete_entry(self, entry_id: str) -> None:
        self._request({"cmd": "delete_entry", "entry_id": entry_id})


def _main() -> None:
    parser = argparse.ArgumentParser(prog="private-journal-daemon")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    daemon = _SessionDaemon(Path(args.socket), Path(args.config))
    daemon.serve()


if __name__ == "__main__":
    _main()
