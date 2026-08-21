# ADR 0003: Program Loudness Normalization

## Status

Accepted

## Context

Quality and fast-route sentences can differ substantially in perceived volume because they come from different references, models, and optional RVC passes. Normalizing every sentence to exactly the same level would remove intentional performance contrast, while leaving all levels untouched produces an inconsistent long-form program.

## Decision

- Store a project-level Program Loudness Policy with defaults of `-18 LUFS`, `-1 dBTP`, `11 LU` target range, and a maximum per-segment gain change of `4 dB`.
- Preserve the raw post-render, post-RVC WAV under `outputs/audio/raw/` before creating the playback cache derivative.
- Apply bounded segment correction after optional RVC and before writing the regular cache.
- Apply rolling gain in the Web Audio playback graph for streamed PCM without waiting for a complete sentence.
- Apply FFmpeg `loudnorm` as a measured two-pass operation when multiple completed sentences are merged into a program file.
- Install the pinned FFmpeg runtime under `tools/ffmpeg/bin/` on the project drive.
- Permit cached derivatives to be reprocessed from their raw WAV after policy changes without invoking TTS or RVC again.

## Consequences

Playback volume becomes more consistent without flattening all sentence dynamics. Raw and processed caches consume additional storage, and a project-local FFmpeg installation is required for precise offline correction. When FFmpeg is unavailable or processing fails, rendering still completes and records the loudness failure in job metadata.
