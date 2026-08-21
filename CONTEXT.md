# Zw Voice Factory

Zw Voice Factory converts long-form fiction into a reviewed voice cast and an executable performance score. Story understanding, casting, reference production, rendering, and playback are separate domains.

## Language

**Analysis Audit**:
The reviewable record of source-text structure, character candidates, supporting evidence, rejected false positives, and parser warnings produced before accepted identities enter the Character Voice Bible.
_Avoid_: Analysis result, character list, director file

**Character Voice Bible**:
The authoritative, book- or series-level record of accepted characters, aliases, evidence, importance, voice profile, canonical reference, emotion variants, and rendering policy.
_Avoid_: Impression file, character list, director file

**Character Voice Profile**:
An evidence-backed semantic description of a character's stable vocal identity, including inferred vocal attributes, delivery tendencies, constraints, confidence, and supporting passages. It is independent of any synthesis model.
_Avoid_: Voice prompt, character summary, personality description

**Renderer Voice Prompt**:
A synthesis-model-specific instruction compiled from a Character Voice Profile for reference generation or sentence rendering. It is disposable output and never the authoritative character record.
_Avoid_: Character Voice Profile, universal prompt, voice identity

**Voice Analysis Backend**:
The selectable service that converts a bounded Character Evidence Pack into a validated Character Voice Profile. Cloud and local implementations obey the same analysis contract.
_Avoid_: Voice model, prompt generator, renderer

**Cloud Voice Analyzer**:
A Voice Analysis Backend that sends a bounded Character Evidence Pack to a user-configured remote language-model provider. Provider availability and quotas do not change the resulting profile contract.
_Avoid_: Free model, Gemini mode, online prompt

**Local Voice Analyzer**:
A Voice Analysis Backend that analyzes Character Evidence Packs through a model hosted on the user's machine, without transmitting source evidence externally.
_Avoid_: Ollama prompt, offline renderer, trained voice model

**Character Evidence Pack**:
A bounded, reviewable selection of identity clues, representative dialogue, narrative characterization, and source references used to infer a Character Voice Profile.
_Avoid_: Full novel, context window, training data

**Director Document**:
The sentence-level performance score. It references a character by stable `character_id` and records text, emotion, tone, pause, speed, pitch, and energy without copying character identity assets.
_Avoid_: Character bible, subtitle file

**Character Candidate**:
A possible character inferred from text before alias reconciliation, evidence review, and confidence filtering. A candidate is not allowed to own voice assets.
_Avoid_: Character

**Canonical Reference**:
The user-approved neutral audio that anchors a character's audible identity. All emotion variants and synthetic training material descend from it.
_Avoid_: Training clip, generated sentence

**RVC Training Set Revision**:
An immutable, reviewable collection of accepted audio clips derived from one Canonical Reference. Every clip records its provenance and quality decision; historical, rejected, or identity-divergent audio cannot enter implicitly.
_Avoid_: Materials folder, all generated audio, reference history

**Emotion Variant**:
A user-reviewable child reference generated from a canonical reference for a named emotion and intensity. It preserves the parent identity while changing performance.
_Avoid_: Separate character voice

**Emotion Production Plan**:
The project-level selection of default and custom Emotion Variants to generate. It owns skip policy, importance threshold, selection locks, generation state, and fallback to the Canonical Reference.
_Avoid_: Emotion list, Director Document

**Archetype Voice**:
A reusable generic voice for low-importance or uncertain characters, grouped by gender, age, and role rather than sharing the narrator indiscriminately.
_Avoid_: Narrator fallback

**Fast Route**:
A lightweight packaged TTS creates performance audio and RVC renders the selected character identity. Its product promise is low latency and a stable custom cast.
_Avoid_: Low-quality route

**RVC Identity Layer**:
The Fast Route conversion stage that applies an approved character RVC model to a lightweight base voice. It is responsible for character identity, not merely for correcting drift in an already cloned voice.
_Avoid_: Stability Layer, quality enhancer, optional post-processing

**Quality Route**:
The selected quality model renders a sentence from an approved canonical or emotion reference. GPT-SoVITS V1, V2, V2 Pro, V2 Pro Plus, V3, and V4 prioritize compatibility, stability, or fidelity; IndexTTS2 adds natural-language emotion control. RVC is optional and only acts as a measured stability layer.
_Avoid_: GSV base voice

**Quality Model Profile**:
The persisted workspace choice of a locally available GPT-SoVITS release or IndexTTS2 for Quality Route rendering, together with availability and effect metadata. It does not own character identity or sentence direction.
_Avoid_: Inference Template, Character Voice Bible

**Voice Asset Review**:
The workflow in which a user previews, regenerates, accepts, or batch-accepts canonical references and emotion variants before downstream rendering or training.
_Avoid_: Model training

**Inference Template**:
A project-level production preset selected before text import. It fixes analysis policy and model roles for reference production and rendering without containing character identity or sentence direction.
_Avoid_: Prompt, voice profile, director preset

**Production Stage**:
A reviewable checkpoint in the ordered path from template selection through rendering. A stage exposes its own work state without taking ownership of another stage's data.
_Avoid_: Page, tab, pipeline node

**Stability Layer**:
An optional Quality Route RVC pass that may reduce cross-sentence identity drift in an already cloned voice. It can run only with an approved RVC Model Revision whose benchmark shows an identity improvement without unacceptable intelligibility, expression, or artifact regressions.
_Avoid_: RVC Identity Layer, voice source, default RVC pass, manual enable switch

**Stability Preparation Policy**:
The Continuous Production Run choice to skip RVC preparation or to reuse approved Stability Layers and prepare benchmark-gated candidates for eligible voices. It never grants production approval by itself.
_Avoid_: RVC enable switch, automatic RVC approval, train every character

**RVC Model Revision**:
An immutable, character-bound RVC model version produced from one traceable training-set revision and inference profile. A revision remains a candidate until a Stability Benchmark approves it for a specific route; rejected or retired revisions cannot silently enter production.
_Avoid_: Model file, current PTH, character voice

**Stability Benchmark**:
A repeatable comparison of base and RVC-processed held-out sentences that records identity consistency, intelligibility, expression preservation, artifacts, latency, and human review. It is the approval gate for a Quality Route Stability Layer.
_Avoid_: Single preview, training loss, speaker similarity score

**Continuous Production Run**:
A resumable project operation that advances an imported novel from slice preparation into Quality Route playback while later slices continue preparing in the background. It owns orchestration state, not character identity, director decisions, or audio assets.
_Avoid_: One-click script, batch render, project

**Production Slice**:
An ordered, sentence-bound portion of a long-form source that can independently reach render-ready status while sharing the project Character Voice Bible. A Production Slice may follow chapters or a bounded text window.
_Avoid_: Chapter, model context, audio chunk

**Slice Revision**:
The immutable analysis and preparation version of one Production Slice. Corrections create a new revision and do not silently rewrite audio rendered from an earlier revision.
_Avoid_: Project revision, cache entry

**Rolling Preparation Window**:
The bounded set of Production Slices prepared ahead of the slice currently being rendered or played. Rendering has priority over work in this window.
_Avoid_: Playback buffer, cloud concurrency

**Provisional Reference**:
An automatically generated Canonical Reference candidate temporarily accepted by a Continuous Production Run so rendering can begin before user review. It never replaces an accepted Canonical Reference and cannot anchor an RVC Training Set Revision.
_Avoid_: Accepted Canonical Reference, fallback voice

**Production Fallback**:
An explicit, recorded substitution used when a required character or reference asset is unavailable. It records the intended asset, actual asset, reason, affected slice, and whether rerendering is pending.
_Avoid_: Silent replacement, default narrator

**Program Loudness Policy**:
The project-level target for perceived loudness, true-peak ceiling, and permitted dynamic range. It is measured across an exported or rolling production program and preserves intentional performance contrast between individual sentences.
_Avoid_: Per-sentence volume equalization, fixed waveform height
