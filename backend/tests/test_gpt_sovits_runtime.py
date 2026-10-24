from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


def test_gpt_sovits_audio_runtime_survives_inherited_disabled_jit() -> None:
    workspace = Path(__file__).resolve().parents[2]
    tool_root = workspace / "models/tts_tools/gpt_sovits"
    model_python = tool_root / ".venv/Scripts/python.exe"
    if not model_python.is_file():
        pytest.skip("Local GPT-SoVITS environment is not installed")
    environment = os.environ.copy()
    environment["NUMBA_DISABLE_JIT"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            str(model_python),
            str(workspace / "model_workers/gpt_sovits_server.py"),
            "--tool-root", str(tool_root), "--check-audio-runtime",
        ],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "samples=16000" in result.stdout
    assert "NUMBA_DISABLE_JIT=0" in result.stdout
