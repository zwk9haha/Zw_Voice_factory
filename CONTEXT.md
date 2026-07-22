# Zw Voice Factory

Zw Voice Factory converts long-form fiction into a reviewed voice cast and an executable performance score. Story understanding, casting, reference production, rendering, and playback are separate domains.

## Language

**Character Voice Bible**:
The authoritative, book- or series-level record of accepted characters, aliases, evidence, importance, voice profile, canonical reference, emotion variants, and rendering policy.
_Avoid_: Impression file, character list, director file

**Director Document**:
The sentence-level performance score. It references a character by stable `character_id` and records text, emotion, tone, pause, speed, pitch, and energy without copying character identity assets.
_Avoid_: Character bible, subtitle file

**Character Candidate**:
A possible character inferred from text before alias reconciliation, evidence review, and confidence filtering. A candidate is not allowed to own voice assets.
_Avoid_: Character

**Canonical Reference**:
The user-approved neutral audio that anchors a character's audible identity. All emotion variants and synthetic training material descend from it.
_Avoid_: Training clip, generated sentence

**Emotion Variant**:
A user-reviewable child reference generated from a canonical reference for a named emotion and intensity. It preserves the parent identity while changing performance.
_Avoid_: Separate character voice

**Archetype Voice**:
A reusable generic voice for low-importance or uncertain characters, grouped by gender, age, and role rather than sharing the narrator indiscriminately.
_Avoid_: Narrator fallback

**Fast Route**:
A lightweight packaged TTS creates performance audio and RVC renders the selected character identity. Its product promise is low latency and a stable custom cast.
_Avoid_: Low-quality route

**Quality Route**:
GPT-SoVITS renders a sentence from an approved canonical or emotion reference. RVC is optional and only acts as a measured stability layer.
_Avoid_: GSV base voice

**Voice Asset Review**:
The workflow in which a user previews, regenerates, accepts, or batch-accepts canonical references and emotion variants before downstream rendering or training.
_Avoid_: Model training
