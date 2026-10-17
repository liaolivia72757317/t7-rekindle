import os
from pathlib import Path
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "start_rust_battle_dev.ps1"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell development launcher is Windows-only")
def test_development_launcher_requires_isolated_stage_and_new_capture_file():
    stage_root = ROOT / "artifacts" / "test-results"
    stage_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rust-launcher-", dir=stage_root) as stage_name:
        with tempfile.TemporaryDirectory(prefix="rust-capture-") as capture_name:
            stage = Path(stage_name)
            capture = Path(capture_name) / "battle.jsonl"
            for name in ("T7-Rekindle.exe", "T7R.NativeBridge.dll", "t7r_battle_ffi.dll"):
                (stage / name).touch()

            def dry_run(stage_path, capture_path):
                return subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                     str(SCRIPT), "-StageDirectory", str(stage_path), "-CapturePath",
                     str(capture_path), "-WhatIf"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                )

            valid = dry_run(stage, capture)
            assert valid.returncode == 0, valid.stderr
            assert "Start Rust battle development session" in valid.stdout
            assert not capture.exists()

            capture.touch()
            reused = dry_run(stage, capture)
            assert reused.returncode != 0
            assert "CapturePath already exists" in reused.stderr

            relative = dry_run(stage, "battle.jsonl")
            assert relative.returncode != 0
            assert "CapturePath must be an absolute path" in relative.stderr

            outside = dry_run(Path(capture_name), capture)
            assert outside.returncode != 0
            assert "StageDirectory must be an isolated directory" in outside.stderr
