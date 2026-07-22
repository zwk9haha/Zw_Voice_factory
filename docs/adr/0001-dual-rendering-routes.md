# ADR 0001: Separate Fast and Quality Rendering Routes

## Status

Accepted for the Factory baseline.

## Context

No single local TTS backend currently satisfies low latency, expressive direction, stable custom identity, and inexpensive multi-character scaling. Applying RVC after a reference-cloned GPT-SoVITS voice can be redundant and can introduce artifacts, while lightweight packaged voices cannot create an unlimited custom cast on their own.

## Decision

Use one shared casting and reference-review pipeline followed by two rendering routes:

- Fast route: lightweight packaged TTS plus RVC for important custom characters; archetype TTS alone for minor or uncertain characters.
- Quality route: GPT-SoVITS with an accepted canonical or emotion reference; RVC only when repeatable benchmarks show that a stability layer improves the character.

VoxCPM2 and IndexTTS2 are offline asset-production backends for canonical references, emotion variants, and RVC training material.

## Consequences

- Character identity and sentence performance must be separate documents.
- Reference review becomes a required checkpoint before rendering or RVC material generation.
- The reader needs route-independent sentence caching and ahead-of-playback generation.
- RVC is not assigned solely from character importance; importance only decides whether custom-asset investment is justified.
