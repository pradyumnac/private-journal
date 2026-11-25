"""Tests for Feature: Config Management & Thin CLI (pj) — Demo lifecycle script."""

import subprocess
import sys
from pathlib import Path


# Traceability: Feature "Config Management & Thin CLI (pj)"
# Scenario: Verify full lifecycle demo script runs cleanly end-to-end
def test_demo_lifecycle_script_runs_and_cleans_up():
    script = Path(__file__).resolve().parent.parent / "scripts" / "demo_lifecycle.py"
    proc = subprocess.run(
        [sys.executable, str(script), "-v", "--fast"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "[1/7] LOCKED BY DEFAULT" in proc.stdout
    assert "[7/7] LOCK SESSION, WIPE KEYS & TEARDOWN" in proc.stdout
    assert "opaque filename:" in proc.stdout
    assert "exists=False" in proc.stdout
