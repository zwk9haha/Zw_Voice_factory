# ADR 0005: Prepare RVC Stability Without Blocking Quality Playback

Continuous Production treats RVC stability preparation as a non-blocking, reuse-first side operation rather than another required Production Stage. The Quality Route may render from Provisional References, but RVC training waits for an accepted Canonical Reference, trains only a bounded set of eligible independent voices, yields to foreground rendering, runs the quality benchmark automatically, and still requires human approval before production use.

This keeps first-slice playback latency independent from training cost and prevents temporary or shared voices from silently becoming character-bound RVC assets. Failures preserve Base Render, approved matching revisions are reused, and earlier caches are only reprocessed by an explicit user action.
