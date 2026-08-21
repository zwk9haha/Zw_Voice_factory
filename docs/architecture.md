# Architecture Baseline

## Shared Casting Pipeline

```text
inference template
  -> novel.txt
  -> story analysis
  -> character candidates with evidence and confidence
  -> alias reconciliation and false-positive rejection
  -> importance and archetype assignment
  -> character_voice_bible.json
  -> director_doc.json
  -> canonical-reference generation and user review
  -> emotion-variant generation and user review
```

## Inference Template Gate

An Inference Template is selected before text import. It fixes the analysis, segmentation, reference-text, and model-role defaults for a project while leaving character identity in the Character Voice Bible and sentence performance in the Director Document.

For the baseline Quality Route, the template assigns distinct responsibilities:

```text
VoxCPM2         -> canonical references and emotion variants
GPT-SoVITS      -> online sentence rendering from approved references
RVC (optional)  -> benchmark-gated post-render identity stability
```

Changing templates may change production defaults, but it must not silently replace an accepted Canonical Reference or rewrite reviewed Director Document segments.

The two public documents have separate ownership:

- `character_voice_bible.json` owns identity, importance, accepted references, emotion children, archetype fallback, and route policy.
- `director_doc.json` owns sentence performance and only references `character_id`.

Rejected candidates remain in an analysis audit report with their source evidence. They are not written as characters.

## Rendering Routes

### Fast

```text
director segment
  -> lightweight TTS speaker selected for gender/range/performance
  -> approved RVC Identity Layer when assigned
  -> sentence cache
  -> playback queue
```

Low-importance characters use an archetype TTS voice directly. Important custom characters use RVC. Playback should generate sentence `N+1` while sentence `N` is playing.

### Quality

```text
director segment
  -> emotion mapping
  -> approved emotion child or canonical fallback
  -> GPT-SoVITS
  -> Base Render cache
  -> optional, benchmark-approved RVC Stability Layer derivative
  -> program loudness derivative
  -> sentence cache
  -> playback queue
```

RVC model files are not production policy. A character-bound RVC Model Revision owns explicit model and index paths, training-set provenance, route-specific inference profiles, and approval state. Fast and Quality approval are independent.

Each route benchmark snapshots the active Canonical Reference hash and inference-profile fingerprint. Changing either value revokes only that route approval and disables its character binding. Existing Base Render caches remain valid and can be reprocessed into a new RVC Derivative and Loudness Derivative without another TTS request.

Continuous Production persists a Stability Preparation Policy separately from the main slice stage. The policy reuses current approved models first, waits for explicit Canonical Reference acceptance before creating a training set, limits automatic new candidates, and exposes per-character training, benchmark, and review progress without delaying render-ready slices. Provisional and reused references are never used to create a new character-bound quality-stability model.

GPT-SoVITS does not receive free-form director emotion as a reliable native control. Emotion is expressed primarily through the selected reference variant, punctuation, segmentation, speed, and pause policy.

## Reference Family

Every accepted character has one canonical parent. Emotion variants are children, never independent identities.

```json
{
  "reference_id": "xiao_yan_ref_neutral_v1",
  "character_id": "xiao_yan",
  "parent_reference_id": null,
  "emotion": "neutral",
  "intensity": 0.5,
  "review_status": "accepted"
}
```

An emotion child sets `parent_reference_id` to the canonical reference. The review UI supports individual regeneration, individual acceptance, and batch acceptance.

## Frontend Boundary

The frontend is a dense audio workstation, not a Gradio form collection. React owns interaction state; FastAPI owns domain state and long-running jobs. WebSocket or SSE carries job progress and log events.

The initial layout has three stable panes:

- Left: cast, voice profile, canonical reference, emotion children.
- Center: script and director annotations.
- Right: generation queue, waveform results, review actions, and versions.
