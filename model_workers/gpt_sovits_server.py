from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import runpy
import sys
import wave

from runtime_log import install_runtime_tee


def check_audio_runtime() -> None:
    """Exercise the reference-audio path before advertising model readiness."""
    import librosa
    import numba
    import numpy as np

    samples = (np.sin(np.arange(24_000) * (2 * np.pi * 220 / 24_000)) * 4096).astype("<i2")
    reference = io.BytesIO()
    with wave.open(reference, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(samples.tobytes())
    reference.seek(0)
    audio, sample_rate = librosa.load(reference, sr=16_000)
    spectrum = librosa.stft(audio, n_fft=512, hop_length=160)
    if sample_rate != 16_000 or audio.size != 16_000 or not np.isfinite(spectrum).all():
        raise RuntimeError("GPT-SoVITS reference-audio preprocessing failed")
    print(
        f"[GPT-SoVITS audio check] librosa={librosa.__version__} numba={numba.__version__} "
        f"NUMBA_DISABLE_JIT={os.environ.get('NUMBA_DISABLE_JIT', '0')} samples={audio.size}",
        flush=True,
    )


def main() -> None:
    # librosa's guvectorize/stencil helpers still compile when JIT is disabled;
    # their callees must remain Numba dispatchers rather than Python functions.
    # Set this before the first import of librosa or numba, even if inherited.
    os.environ["NUMBA_DISABLE_JIT"] = "0"
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-root", required=True, type=Path)
    parser.add_argument("--check-audio-runtime", action="store_true")
    args, upstream_args = parser.parse_known_args()
    if args.check_audio_runtime:
        check_audio_runtime()
        return
    tool_root = args.tool_root.resolve()
    workspace_root = Path(__file__).resolve().parents[1]
    install_runtime_tee(workspace_root, "gpt_sovits")
    check_audio_runtime()
    os.chdir(tool_root)
    sys.path.insert(0, str(tool_root))
    entry = tool_root / "api_v2.py"
    sys.argv = [str(entry), *upstream_args]
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
