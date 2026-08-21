from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field


class ProgramLoudnessPolicy(BaseModel):
    schema_version: int = 1
    enabled: bool = True
    target_lufs: float = Field(default=-18.0, ge=-28.0, le=-12.0)
    true_peak_dbtp: float = Field(default=-1.0, ge=-6.0, le=-0.1)
    target_lra: float = Field(default=11.0, ge=3.0, le=20.0)
    max_segment_gain_db: float = Field(default=4.0, ge=0.0, le=12.0)


class LoudnessMetrics(BaseModel):
    processor: str
    status: str
    input_lufs: float | None = None
    output_lufs: float | None = None
    input_true_peak_dbtp: float | None = None
    output_true_peak_dbtp: float | None = None
    loudness_range_lu: float | None = None
    applied_gain_db: float = 0.0
    constrained_by_peak: bool = False
    final_program_pass: bool = False
    detail: str | None = None


class LoudnessProcessingError(RuntimeError):
    pass


class LoudnessProcessor:
    def __init__(self, workspace_root: Path, runtime_logger: logging.Logger | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.runtime_logger = runtime_logger or logging.getLogger(__name__)
        self.temp_root = self.workspace_root / "outputs" / "runtime" / "loudness"
        self._rolling_lufs: dict[str, deque[float]] = {}

    def process_segment_bytes(
        self,
        audio: bytes,
        policy: ProgramLoudnessPolicy,
    ) -> tuple[bytes, LoudnessMetrics]:
        if not policy.enabled:
            return audio, LoudnessMetrics(processor="disabled", status="skipped", detail="program loudness disabled")
        executable = self._ffmpeg_path()
        if executable is None:
            return audio, LoudnessMetrics(
                processor="unavailable",
                status="unavailable",
                detail="project-local FFmpeg is not installed",
            )
        self.temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="segment-", dir=self.temp_root) as temporary_dir:
            source = Path(temporary_dir) / "input.wav"
            target = Path(temporary_dir) / "output.wav"
            source.write_bytes(audio)
            source_metrics = self._measure(executable, source, policy)
            if source_metrics["lufs"] is None:
                return audio, LoudnessMetrics(
                    processor="ffmpeg_loudnorm",
                    status="skipped",
                    detail="no measurable speech loudness",
                )
            intended_gain = policy.target_lufs - source_metrics["lufs"]
            applied_gain = max(-policy.max_segment_gain_db, min(policy.max_segment_gain_db, intended_gain))
            constrained_by_peak = False
            source_peak = source_metrics["true_peak"]
            if source_peak is not None:
                safe_gain = policy.true_peak_dbtp - source_peak
                if applied_gain > safe_gain:
                    applied_gain = safe_gain
                    constrained_by_peak = True
            if abs(applied_gain) < 0.01:
                shutil.copyfile(source, target)
            else:
                limit = 10 ** (policy.true_peak_dbtp / 20)
                self._run(
                    executable,
                    [
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source),
                        "-af",
                        f"volume={applied_gain:+.4f}dB,alimiter=limit={limit:.8f}:level=0",
                        "-c:a",
                        "pcm_s16le",
                        str(target),
                    ],
                )
            output_metrics = self._measure(executable, target, policy)
            return target.read_bytes(), LoudnessMetrics(
                processor="ffmpeg_loudnorm",
                status="corrected",
                input_lufs=source_metrics["lufs"],
                output_lufs=output_metrics["lufs"],
                input_true_peak_dbtp=source_peak,
                output_true_peak_dbtp=output_metrics["true_peak"],
                loudness_range_lu=output_metrics["lra"],
                applied_gain_db=round(applied_gain, 3),
                constrained_by_peak=constrained_by_peak,
            )

    def normalize_program_file(
        self,
        source: Path,
        target: Path,
        policy: ProgramLoudnessPolicy,
    ) -> LoudnessMetrics:
        if not policy.enabled:
            shutil.copyfile(source, target)
            return LoudnessMetrics(processor="disabled", status="skipped", final_program_pass=True)
        executable = self._ffmpeg_path()
        if executable is None:
            shutil.copyfile(source, target)
            return LoudnessMetrics(
                processor="unavailable",
                status="unavailable",
                final_program_pass=True,
                detail="project-local FFmpeg is not installed",
            )
        source_metrics = self._measure(executable, source, policy)
        required = ("lufs", "true_peak", "lra", "threshold", "target_offset")
        if any(source_metrics[key] is None for key in required):
            shutil.copyfile(source, target)
            return LoudnessMetrics(
                processor="ffmpeg_loudnorm",
                status="skipped",
                final_program_pass=True,
                detail="no measurable program loudness",
            )
        filter_value = (
            f"loudnorm=I={policy.target_lufs}:TP={policy.true_peak_dbtp}:LRA={policy.target_lra}:"
            f"measured_I={source_metrics['lufs']}:measured_TP={source_metrics['true_peak']}:"
            f"measured_LRA={source_metrics['lra']}:measured_thresh={source_metrics['threshold']}:"
            f"offset={source_metrics['target_offset']}:linear=true:print_format=summary"
        )
        self._run(
            executable,
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-af",
                filter_value,
                "-c:a",
                "pcm_s16le",
                str(target),
            ],
        )
        output_metrics = self._measure(executable, target, policy)
        return LoudnessMetrics(
            processor="ffmpeg_loudnorm",
            status="corrected",
            input_lufs=source_metrics["lufs"],
            output_lufs=output_metrics["lufs"],
            input_true_peak_dbtp=source_metrics["true_peak"],
            output_true_peak_dbtp=output_metrics["true_peak"],
            loudness_range_lu=output_metrics["lra"],
            applied_gain_db=round((output_metrics["lufs"] or 0.0) - (source_metrics["lufs"] or 0.0), 3),
            constrained_by_peak=(output_metrics["true_peak"] or -99.0) >= policy.true_peak_dbtp,
            final_program_pass=True,
        )

    def record(self, metrics: LoudnessMetrics | None, program_id: str | None = None) -> None:
        if metrics is None or metrics.input_lufs is None or not math.isfinite(metrics.input_lufs):
            return
        history = self._rolling_lufs.setdefault(program_id or "__default__", deque(maxlen=48))
        history.append(metrics.input_lufs)

    def rolling_gain_db(self, policy: ProgramLoudnessPolicy, program_id: str | None = None) -> float:
        history = self._rolling_lufs.get(program_id or "__default__")
        if not policy.enabled or not history:
            return 0.0
        mean_lufs = sum(history) / len(history)
        return round(max(-policy.max_segment_gain_db, min(policy.max_segment_gain_db, policy.target_lufs - mean_lufs)), 3)

    def _ffmpeg_path(self) -> Path | None:
        configured = os.getenv("ZW_VOICE_FFMPEG_PATH")
        candidates = [
            Path(configured) if configured else None,
            self.workspace_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            self.workspace_root / "tools" / "ffmpeg" / "ffmpeg.exe",
        ]
        for candidate in candidates:
            if candidate is not None and candidate.is_file():
                return candidate
        system_ffmpeg = shutil.which("ffmpeg")
        return Path(system_ffmpeg) if system_ffmpeg else None

    def _measure(
        self,
        executable: Path,
        source: Path,
        policy: ProgramLoudnessPolicy,
    ) -> dict[str, float | None]:
        completed = self._run(
            executable,
            [
                "-hide_banner",
                "-nostats",
                "-i",
                str(source),
                "-af",
                f"loudnorm=I={policy.target_lufs}:TP={policy.true_peak_dbtp}:LRA={policy.target_lra}:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
        )
        matches = re.findall(r"\{\s*\"input_i\".*?\}", completed.stderr, flags=re.DOTALL)
        if not matches:
            raise LoudnessProcessingError("FFmpeg did not return loudnorm measurement data")
        try:
            payload = json.loads(matches[-1])
        except json.JSONDecodeError as error:
            raise LoudnessProcessingError("FFmpeg returned invalid loudnorm measurement data") from error
        return {
            "lufs": self._number(payload.get("input_i")),
            "true_peak": self._number(payload.get("input_tp")),
            "lra": self._number(payload.get("input_lra")),
            "threshold": self._number(payload.get("input_thresh")),
            "target_offset": self._number(payload.get("target_offset")),
        }

    def _run(
        self,
        executable: Path,
        arguments: list[str],
        *,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [str(executable), *arguments],
                check=True,
                capture_output=capture_output,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
            raise LoudnessProcessingError(f"FFmpeg loudness processing failed: {detail}") from error

    @staticmethod
    def _number(value: object) -> float | None:
        try:
            parsed = float(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None
