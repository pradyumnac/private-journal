#!/usr/bin/env python3
"""Full lifecycle ASCII-movie demonstration of private-journal (pj) in a /tmp sandbox."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pyotp


class DemoRunner:
    """Runs pj commands in an isolated /tmp sandbox with ASCII-movie pacing."""

    def __init__(self, verbose: bool, fast: bool) -> None:
        self.verbose = verbose
        self.fast = fast
        self.root = Path(tempfile.mkdtemp(prefix="pj-demo-"))
        self.bin_dir = self.root / "bin"
        self.xdg_config = self.root / "xdg_config"
        self.xdg_data = self.root / "xdg_data"
        self.xdg_runtime = self.root / "xdg_runtime"
        self.env = os.environ.copy()

    def setup_sandbox(self) -> None:
        self.bin_dir.mkdir(parents=True)
        self.xdg_config.mkdir(parents=True)
        self.xdg_data.mkdir(parents=True)
        self.xdg_runtime.mkdir(parents=True)
        os.chmod(self.xdg_runtime, 0o700)

        # Simulated nvim that writes deterministic journal content without blocking
        nvim_shim = self.bin_dir / "nvim"
        nvim_shim.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "from pathlib import Path\n"
            "target = Path(sys.argv[-1])\n"
            "existing = target.read_text(encoding='utf-8') if target.exists() else ''\n"
            "if not existing.strip():\n"
            "    target.write_text('Rotated vault keys and reviewed Argon2id parameters.\\nAll checks green.', encoding='utf-8')\n"
            "else:\n"
            "    target.write_text(existing + '\\nFollow-up: verified ChaCha20-Poly1305 tamper rejection.', encoding='utf-8')\n",
            encoding="utf-8",
        )
        os.chmod(nvim_shim, os.stat(nvim_shim).st_mode | stat.S_IXUSR)

        # Simulated fzf that picks the first candidate line
        fzf_shim = self.bin_dir / "fzf"
        fzf_shim.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "lines = [ln for ln in sys.stdin.read().splitlines() if ln.strip()]\n"
            "if not lines:\n"
            "    sys.exit(1)\n"
            "print(lines[0])\n",
            encoding="utf-8",
        )
        os.chmod(fzf_shim, os.stat(fzf_shim).st_mode | stat.S_IXUSR)

        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["XDG_CONFIG_HOME"] = str(self.xdg_config)
        self.env["XDG_DATA_HOME"] = str(self.xdg_data)
        self.env["XDG_RUNTIME_DIR"] = str(self.xdg_runtime)

    def _pause(self, seconds: float) -> None:
        if not self.fast:
            time.sleep(seconds)

    def _type_line(self, text: str, delay: float = 0.012) -> None:
        if self.fast:
            print(text)
            return
        for ch in text:
            sys.stdout.write(ch)
            sys.stdout.flush()
            time.sleep(delay)
        sys.stdout.write("\n")
        sys.stdout.flush()

    def scene(self, step: int, total: int, title: str) -> None:
        bar = "=" * 68
        print(f"\n+{bar}+")
        print(f"| [{step}/{total}] {title:<59}|")
        print(f"+{bar}+")
        self._pause(0.25)

    def note(self, msg: str) -> None:
        if self.verbose:
            print(f"  [detail] {msg}")

    def run_pj(
        self,
        args: list[str],
        stdin_text: str | None = None,
        expect_ok: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        self._type_line(f"$ pj {' '.join(args)}")
        proc = subprocess.run(
            [sys.executable, "-m", "private_journal.cli", *args],
            env=self.env,
            input=stdin_text,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.stdout.strip():
            for line in proc.stdout.strip().splitlines():
                print(f"  {line}")
        if proc.stderr.strip():
            for line in proc.stderr.strip().splitlines():
                print(f"  [stderr] {line}")
        if expect_ok and proc.returncode != 0:
            raise RuntimeError(f"pj {' '.join(args)} failed with exit code {proc.returncode}")
        self._pause(0.2)
        return proc

    def teardown(self) -> None:
        subprocess.run(
            [sys.executable, "-m", "private_journal.cli", "lock"],
            env=self.env,
            capture_output=True,
            check=False,
        )
        shutil.rmtree(self.root, ignore_errors=True)

    def play(self) -> None:
        self.setup_sandbox()
        password = "demo-master-password-2026"
        try:
            print("+--------------------------------------------------------------------+")
            print("|           private-journal (pj) :: Full Lifecycle Demo              |")
            print(f"| Sandbox: {str(self.root):<58}|")
            print("+--------------------------------------------------------------------+")

            # 1. Verify locked state blocks access
            self.scene(1, 7, "LOCKED BY DEFAULT (PRE-UNLOCK CHECK)")
            self.run_pj(["status"])
            self.run_pj(["search", "--plain"], expect_ok=False)

            # 2. First-run setup + TOTP unlock (using library setup + live TOTP into pj unlock)
            self.scene(2, 7, "FIRST-RUN SETUP & 2FA TIMED UNLOCK")
            from private_journal.setup import initialize_vault

            self._type_line("$ pj unlock  # (first-run setup: password + TOTP provisioning)")
            setup_res = initialize_vault(
                password=password.encode("utf-8"),
                data_dir=self.xdg_data / "private-journal",
            )
            print(f"  Vault initialized at {setup_res.data_dir}")
            print(f"  TOTP URI: {setup_res.totp_uri}")
            self.note(f"TOTP Secret: {setup_res.totp_secret}")

            # Unlock via CLI using a live TOTP code (via pty/stdin helper for getpass)
            totp_code = pyotp.TOTP(setup_res.totp_secret).now()
            self._unlock_via_cli(password, totp_code)
            self.run_pj(["status"])

            # 3. Create entry via sandboxed nvim
            self.scene(3, 7, "CREATE ENCRYPTED ENTRY (SANDBOXED NVIM)")
            created = self.run_pj(["new", "--tag", "security", "--tag", "crypto"])
            entry_id = created.stdout.strip()
            self.note(f"Created entry ID: {entry_id}")

            # 4. Inspect encrypted disk state
            self.scene(4, 7, "INSPECT ENCRYPTED STORAGE ON DISK")
            vault_dir = self.xdg_data / "private-journal"
            entries_dir = vault_dir / "entries"
            enc_files = list(entries_dir.iterdir())
            print(f"  vault.json exists: {(vault_dir / 'vault.json').exists()}")
            print(f"  entries/*.enc count: {len(enc_files)}")
            for f in enc_files:
                print(f"  opaque filename: {f.name} (mode={oct(f.stat().st_mode & 0o777)})")
                if self.verbose:
                    raw = f.read_bytes()
                    print(f"  [detail] ciphertext bytes={len(raw)}, head={raw[:24].hex()}...")
            if self.verbose:
                sock = self.xdg_runtime / "private-journal" / "daemon.sock"
                print(f"  [detail] daemon.sock mode={oct(sock.stat().st_mode & 0o777)}")
                print(f"  [detail] vault.json={json.loads((vault_dir / 'vault.json').read_text())}")

            # 5. Search & view
            self.scene(5, 7, "TRANSIENT SEARCH (FZF) & READ-ONLY VIEW")
            self.run_pj(["search", "Argon2id", "--tag", "security", "--plain"])
            self.run_pj(["search", "--id"])
            self.run_pj(["view", entry_id])

            # 6. Edit, config, and delete
            self.scene(6, 7, "EDIT ENTRY, UPDATE CONFIG & DELETE")
            self.run_pj(["edit", entry_id, "--tag", "security", "--tag", "verified"])
            self.run_pj(["view", entry_id])
            self.run_pj(["config", "get", "unlock_timeout"])
            self.run_pj(["config", "set", "unlock_timeout", "30m"])
            self.run_pj(["config", "get", "unlock_timeout"])
            self.run_pj(["delete", entry_id])
            self.run_pj(["search", "--plain"])

            # 7. Lock & teardown
            self.scene(7, 7, "LOCK SESSION, WIPE KEYS & TEARDOWN")
            self.run_pj(["lock"])
            self.run_pj(["status"])
            self.run_pj(["view", entry_id], expect_ok=False)
        finally:
            self.teardown()
            print(f"\n[teardown] Removed sandbox {self.root} (exists={self.root.exists()})")

    def _unlock_via_cli(self, password: str, totp_code: str) -> None:
        """Invoke pj unlock with password + TOTP without /dev/tty so getpass reads stdin."""
        proc = subprocess.run(
            [sys.executable, "-m", "private_journal.cli", "unlock"],
            env=self.env,
            input=f"{password}\n{totp_code}\n",
            text=True,
            capture_output=True,
            start_new_session=True,
            timeout=15,
            check=False,
        )
        if proc.stdout.strip():
            for line in proc.stdout.strip().splitlines():
                print(f"  {line}")
        if proc.returncode != 0:
            raise RuntimeError(f"unlock failed: {proc.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full private-journal ASCII-movie lifecycle demo.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show extra cryptographic and socket details.")
    parser.add_argument("--fast", action="store_true", help="Disable typewriter pacing delays.")
    args = parser.parse_args()
    DemoRunner(verbose=args.verbose, fast=args.fast).play()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
