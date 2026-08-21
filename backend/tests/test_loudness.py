from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

import pytest

from app.loudness import LoudnessMetrics, LoudnessProcessor, ProgramLoudnessPolicy


def write_tone(path: Path, amplitude: float = 0.05, duration_seconds: float = 3.0) -> None:
    sample_rate = 48_000
    frame_count = round(sample_rate * duration_seconds)
    samples = array(
        "h",
        (
            round(32_767 * amplitude * math.sin(2 * math.pi * 440 * frame / sample_rate))
            for frame in range(frame_count)
        ),
    )
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())


def test_ffmpeg_segment_cap_and_two_pass_program_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = Path(__file__).resolve().parents[2]
    ffmpeg = workspace_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    assert ffmpeg.is_file()
    monkeypatch.setenv("ZW_VOICE_FFMPEG_PATH", str(ffmpeg))
    processor = LoudnessProcessor(tmp_path)
    policy = ProgramLoudnessPolicy()
    source = tmp_path / "source.wav"
    program = tmp_path / "program.wav"
    write_tone(source)

    corrected_audio, segment_metrics = processor.process_segment_bytes(source.read_bytes(), policy)

    assert corrected_audio[:4] == b"RIFF"
    assert segment_metrics.status == "corrected"
    assert segment_metrics.applied_gain_db == pytest.approx(4.0, abs=0.05)
    assert segment_metrics.output_lufs is not None
    assert segment_metrics.input_lufs is not None
    assert segment_metrics.output_lufs > segment_metrics.input_lufs
    assert segment_metrics.output_true_peak_dbtp is not None
    assert segment_metrics.output_true_peak_dbtp <= policy.true_peak_dbtp + 0.1

    program_metrics = processor.normalize_program_file(source, program, policy)

    assert program.read_bytes()[:4] == b"RIFF"
    assert program_metrics.status == "corrected"
    assert program_metrics.final_program_pass is True
    assert program_metrics.output_lufs == pytest.approx(policy.target_lufs, abs=0.3)
    assert program_metrics.output_true_peak_dbtp is not None
    assert program_metrics.output_true_peak_dbtp <= policy.true_peak_dbtp + 0.1


def test_rolling_loudness_history_is_isolated_per_project(tmp_path: Path) -> None:
    processor = LoudnessProcessor(tmp_path)
    policy = ProgramLoudnessPolicy()
    metrics = LoudnessMetrics(
        processor="test",
        status="corrected",
        input_lufs=-24.0,
    )

    processor.record(metrics, "project-a")

    assert processor.rolling_gain_db(policy, "project-a") == 4.0
    assert processor.rolling_gain_db(policy, "project-b") == 0.0
