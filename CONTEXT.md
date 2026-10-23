# Zw Voice Factory

Zw Voice Factory converts long-form fiction into a reviewed voice cast and an executable performance score. Story understanding, casting, reference production, rendering, and playback are separate domains.

## Language

**Voice Platform**:
The product boundary that accepts text from multiple content scenarios and turns it into reviewed, reusable voice production tasks. Novel dubbing is one scenario adapter, not the definition of the platform.
_Avoid_: Novel dubbing application, TTS wrapper

**Voice Project**:
The durable workspace for one piece of content and its related analysis, voice bindings, production runs, revisions, and exported audio. A project may be created from a novel, a short text, a script, subtitles, or another supported source.
_Avoid_: Novel project, one-click run

**Voice Task**:
A user-requested operation inside a Voice Project that converts one bounded text selection into a reviewed or rendered audio result. Tasks can be quick text previews or long-running slice production runs.
_Avoid_: API request, sentence job, project

**Source Document**:
The user-provided text source attached to a Voice Project. Its scenario adapter may expose chapters, scenes, subtitles, or plain text segments without changing the shared production contract.
_Avoid_: Novel, input file

**Text Segment**:
A bounded, ordered unit of a Source Document sent through analysis, speaker assignment, direction, and rendering. A Production Slice is the long-form variant of a Text Segment with independent preparation state.
_Avoid_: Chunk, arbitrary token window

**Speaker Assignment**:
The explicit decision that connects a Text Segment to a stable character, an Archetype Voice, a narrator route, or a recorded fallback. It is separate from the voice asset selected for rendering.
_Avoid_: Character detection, voice prompt

**Voice Catalog**:
The platform-level registry of reusable voice assets, including canonical references, emotion variants, RVC revisions, quality metadata, provenance, and publication state. Catalog entries are immutable by version.
_Avoid_: Audio folder, project cache, model list

**Project Voice Snapshot**:
The project-owned reference to a specific Voice Catalog version, with project-local overrides and approval state. A catalog update never silently changes an existing snapshot.
_Avoid_: Global voice, current voice file

**Match-or-Generate**:
The platform policy that searches approved project snapshots and the Voice Catalog before generating a new voice asset. Generation is a fallback for a missing or unsuitable match, not the default first step.
_Avoid_: Always generate, voice cloning

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

**Workflow Definition**:
The versioned JSON graph that declares reusable capability nodes, their inputs, configuration, dependency edges, and recovery policy. It describes what may run, not one particular execution.
_Avoid_: Page flow, model pipeline, run history

**Workflow Canvas**:
The visual editor for a Workflow Definition. It owns node placement, port connections, draft validation, and publishing; it does not execute model work or own produced audio.
_Avoid_: Production page, workflow runtime, diagram screenshot

**Workflow Node**:
A typed capability or control-flow step in a Workflow Definition. A node has a stable type/version, declared input and output ports, configuration schema, and execution policy; a visual node is only one presentation of that definition.
_Avoid_: UI card, model process, page button

**Workflow Edge**:
An explicit typed connection from one Workflow Node output port to another node input port. Edges define data flow; dependency order is derived from edges rather than inferred only from opaque input strings.
_Avoid_: depends_on list, callback, page event

**Conditional Branch**:
A control-flow decision whose mutually exclusive outcomes select which downstream path becomes eligible to run. Paths that are not selected are intentionally skipped, while a later convergence may continue when another selected path has produced its result.
_Avoid_: Boolean data field, frontend visibility toggle, failed dependency

**Condition Group**:
The set of Condition Rules evaluated by one Conditional Branch and combined with either all-match or any-match semantics. A group produces one route decision and does not create nested workflow runs.
_Avoid_: Workflow subgraph, filter list, multiple branch nodes

**Condition Rule**:
A comparison that reads one value from the branch input using a stable field path and applies one operator. A missing path resolves to an empty value so emptiness rules can handle incomplete data explicitly.
_Avoid_: Executable expression, template code, hidden data binding

**Branch Edge**:
A Workflow Edge that carries route eligibility from a control-flow node to a downstream node without binding a data input port. Multiple incoming Branch Edges use any-match semantics so independently selected paths can converge.
_Avoid_: Data binding, implicit dependency, error fallback

**Node Manifest**:
The versioned contract published by a node provider. It describes ports, configuration fields, validation rules, execution mode, retry/idempotency policy, resource group, and human-review requirements so the canvas and runtime share one interface.
_Avoid_: Prompt template, frontend form schema, model README

**Human Checkpoint**:
A Workflow Node state that intentionally pauses execution until a user reviews or approves a proposed asset, match, or decision. Approval is an explicit domain event and cannot be simulated by node completion.
_Avoid_: Loading state, automatic approval, modal dialog

**Published Workflow Version**:
An immutable, runnable snapshot of a Workflow Definition. Canvas edits create a new draft or version; a Workflow Run always points to one Published Workflow Version and never follows later edits.
_Avoid_: Current canvas, mutable workflow, run settings

**Workflow Run**:
One persisted execution of a Workflow Definition. It owns input data, overall status, progress, retry count, and the ordered Node Runs without owning the underlying voice assets.
_Avoid_: Voice Project, one-shot API request

**Node Run**:
The persisted execution record for one node in a Workflow Run, including resolved inputs, outputs, cache key, attempt, elapsed time, and failure state.
_Avoid_: UI node, model process

**Project Workflow Binding**:
The Voice Project's reference to one Published Workflow Version that is used by the project's run surface. A project has one current binding; changing it is explicit and does not rewrite existing Workflow Runs.
_Avoid_: Current canvas, workflow preference, run input

**Project Workflow Surface**:
The project-facing UI generated from the bound workflow's input and output declarations. It starts runs, shows node progress and history, and exposes produced audio while leaving graph editing to the Workflow Canvas.
_Avoid_: Workflow Canvas, project dashboard, node inspector
