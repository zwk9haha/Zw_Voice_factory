# ADR 0002: Continuous Quality Production Uses a Rolling Slice Window

## Status

Accepted.

## Context

Waiting for an entire long novel to finish character analysis, reference production, emotion derivation, and director analysis defeats the product goal of quickly starting reliable multi-character playback. Starting too early without persisted slice boundaries and resource versions can instead create voice drift, silent substitutions, duplicated work, and audio that changes underneath the user.

## Decision

A Continuous Production Run blocks only until the first Production Slice is render-ready. It then enters the Quality Route while a Rolling Preparation Window prepares the next one or two slices.

- The project Character Voice Bible remains the authoritative identity store; slices contribute evidence and revisions but do not fork character identity.
- The first slice advances through analysis, candidate screening, voice profiling, provisional reference generation, optional emotion production, and Director Document generation before automatic navigation to Quality Route rendering.
- Emotion policy has three values: skip, generate in background, or require before rendering. Background generation is the default.
- Rendering and playback have resource priority. Background analysis, reference production, and emotion generation use bounded concurrency and yield when they contend with rendering models or GPU memory.
- Failures are isolated by slice, character, and stage. A recorded Production Fallback may unblock rendering, but it never silently replaces an accepted asset or rewrites completed audio.
- Every stage is persisted and content-addressed. Resume reuses completed artifacts and existing character-profile checkpoints.
- Completed slice audio remains attached to the Slice Revision that produced it. Later corrections require an explicit rerender decision.

## Consequences

- Users can begin listening after the first slice rather than after full-book preparation.
- The orchestration layer must expose pause, resume, retry, skip, cancellation, and per-stage progress.
- Quality Route needs a visible slice queue in addition to its existing sentence render queue.
- Resource scheduling must distinguish foreground rendering from background preparation.
- Provisional references and fallbacks require visible provenance and review state.
- Slice and project revisions must remain separate concepts.
