#!/usr/bin/env python3
"""Full lifecycle ASCII-movie demonstration of private-journal (pj) in a /tmp sandbox."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pyotp

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


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

            # 2. First-run setup + TOTP unlock via pj setup and pj unlock
            self.scene(2, 7, "FIRST-RUN SETUP & 2FA TIMED UNLOCK")
            self._type_line("$ pj setup")
            totp_secret = self._setup_via_cli(password)
            self.note(f"TOTP Secret: {totp_secret}")

            # Unlock via CLI using a live TOTP code
            totp_code = pyotp.TOTP(totp_secret).now()
            self._type_line("$ pj unlock")
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
            self.run_pj(["delete", entry_id, "--yes"])
            self.run_pj(["search", "--plain"])

            # 7. Lock & teardown
            self.scene(7, 7, "LOCK SESSION, WIPE KEYS & TEARDOWN")
            self.run_pj(["lock"])
            self.run_pj(["status"])
            self.run_pj(["view", entry_id], expect_ok=False)
        finally:
            self.teardown()
            print(f"\n[teardown] Removed sandbox {self.root} (exists={self.root.exists()})")

    def _setup_via_cli(self, password: str) -> str:
        """Invoke pj setup in the isolated sandbox env without touching host XDG_CONFIG_HOME."""
        proc = subprocess.run(
            [sys.executable, "-m", "private_journal.cli", "setup", "--totp"],
            env=self.env,
            input=f"{password}\n{password}\n",
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
            raise RuntimeError(f"setup failed: {proc.stderr}")
        for line in proc.stdout.splitlines():
            if line.startswith("TOTP Secret:"):
                return line.split(":", 1)[1].strip()
        raise RuntimeError("TOTP Secret not found in pj setup output")

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


def render_cast_to_svg(
    cast_path: Path,
    svg_path: Path,
    cols: int = 76,
    rows: int = 24,
    speed: float = 0.75,
) -> None:
    """Render an asciinema .cast file (v2/v3) into an animated terminal SVG."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    raw_lines = [ln for ln in cast_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not raw_lines:
        raise RuntimeError(f"Empty cast file: {cast_path}")
    header = json.loads(raw_lines[0])
    is_v3 = int(header.get("version", 2)) >= 3

    frames: list[tuple[float, list[str]]] = []
    screen_lines: list[str] = [""]
    elapsed = 0.0
    last_captured = -1.0

    for line in raw_lines[1:]:
        ev = json.loads(line)
        t_val, ev_type, text = float(ev[0]), ev[1], str(ev[2])
        elapsed = (elapsed + t_val) if is_v3 else t_val
        if ev_type != "o":
            continue
        cleaned = _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
        for ch in cleaned:
            if ch == "\n":
                screen_lines.append("")
            elif ch == "\b":
                if screen_lines[-1]:
                    screen_lines[-1] = screen_lines[-1][:-1]
            else:
                screen_lines[-1] += ch
        # Sample frames at >= 0.08s intervals to keep SVG compact and smooth
        if elapsed - last_captured >= 0.08:
            visible = [ln[:cols] for ln in screen_lines[-rows:]]
            frames.append((elapsed, visible))
            last_captured = elapsed

    final_visible = [ln[:cols] for ln in screen_lines[-rows:]]
    scale = 1.0 / speed
    scaled_frames = [(t * scale, vis) for t, vis in frames]
    total_dur = max(elapsed * scale + 2.0, 2.0)
    scaled_frames.append((elapsed * scale + 0.05, final_visible))
    frames = scaled_frames

    char_w = 8.4
    line_h = 18
    pad_x = 18
    pad_y = 42
    width = int(cols * char_w + pad_x * 2)
    height = int(rows * line_h + pad_y + 18)

    css_rules: list[str] = []
    frame_groups: list[str] = []
    for idx, (t_start, vis) in enumerate(frames):
        t_end = frames[idx + 1][0] if idx + 1 < len(frames) else total_dur
        p_start = (t_start / total_dur) * 100.0
        p_end = (t_end / total_dur) * 100.0
        anim_name = f"f{idx}"
        if idx == len(frames) - 1:
            css_rules.append(
                f"@keyframes {anim_name}{{0%,{p_start:.3f}%{{opacity:0}}{p_start + 0.001:.3f}%,100%{{opacity:1}}}}"
            )
        else:
            css_rules.append(
                f"@keyframes {anim_name}{{0%,{p_start:.3f}%{{opacity:0}}"
                f"{p_start + 0.001:.3f}%,{p_end:.3f}%{{opacity:1}}"
                f"{p_end + 0.001:.3f}%,100%{{opacity:0}}}}"
            )
        tspans = []
        for r_idx, ln in enumerate(vis):
            y = pad_y + r_idx * line_h
            color = "#7ee787" if ln.startswith("$ ") else ("#79c0ff" if ln.startswith("| [") else "#e6edf3")
            tspans.append(
                f'<text x="{pad_x}" y="{y}" fill="{color}">{html.escape(ln)}</text>'
            )
        frame_groups.append(
            f'<g style="opacity:0;animation:{anim_name} {total_dur:.2f}s infinite">{"".join(tspans)}</g>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="private-journal lifecycle demo">\n'
        f"<style>\n"
        f'  text {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; white-space: pre; }}\n'
        f"  {''.join(css_rules)}\n"
        f"</style>\n"
        f'<rect width="{width}" height="{height}" rx="10" fill="#0d1117"/>\n'
        f'<rect width="{width}" height="28" rx="10" fill="#161b22"/>\n'
        f'<circle cx="18" cy="14" r="5" fill="#ff5f56"/>\n'
        f'<circle cx="36" cy="14" r="5" fill="#ffbd2e"/>\n'
        f'<circle cx="54" cy="14" r="5" fill="#27c93f"/>\n'
        f'<text x="{width // 2}" y="18" text-anchor="middle" fill="#8b949e">pj — private-journal lifecycle</text>\n'
        f"{''.join(frame_groups)}\n"
        f"</svg>\n"
    )
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full private-journal ASCII-movie lifecycle demo.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show extra cryptographic and socket details.")
    parser.add_argument("--fast", action="store_true", help="Disable typewriter pacing delays.")
    parser.add_argument("--record-cast", type=Path, default=None, help="Record session to asciinema .cast via mise asciinema.")
    parser.add_argument("--render-svg", type=Path, default=None, help="Render recorded .cast to an animated SVG.")
    args = parser.parse_args()

    if args.record_cast is not None:
        args.record_cast.parent.mkdir(parents=True, exist_ok=True)
        inner_cmd = f"{sys.executable} {Path(__file__).resolve()}"
        if args.verbose:
            inner_cmd += " -v"
        if args.fast:
            inner_cmd += " --fast"
        subprocess.run(
            [
                "asciinema",
                "rec",
                "--overwrite",
                "--window-size",
                "76x24",
                "--command",
                inner_cmd,
                str(args.record_cast),
            ],
            check=True,
        )
        if args.render_svg is not None:
            render_cast_to_svg(args.record_cast, args.render_svg)
        return 0

    DemoRunner(verbose=args.verbose, fast=args.fast).play()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
