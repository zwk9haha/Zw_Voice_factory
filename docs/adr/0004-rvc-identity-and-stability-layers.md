# ADR 0004: Separate RVC Identity And Stability Layers

## Status

Accepted

## Context

RVC serves two materially different purposes in Zw Voice Factory. The Fast Route needs RVC to establish a custom character identity over a lightweight base speaker. The Quality Route already renders an accepted character identity through a reference-driven model, so an additional RVC pass is useful only when it measurably reduces cross-sentence drift without damaging intelligibility or expression.

The original implementation treated both purposes as boolean switches over a discovered `.pth` and `.index` pair. Training could mix reference history and generated emotion clips, inference parameters were global constants, a newly trained model was bound immediately, and cached audio did not preserve the pre-RVC render. This made quality regressions, stale cache reuse, and accidental model activation possible.

## Decision

- Define the Fast Route stage as an RVC Identity Layer and the optional Quality Route pass as a Stability Layer.
- Treat every trained or imported model as an immutable RVC Model Revision with explicit artifact paths, character ownership, training-set provenance, route-specific inference profiles, lifecycle state, and benchmark history.
- Keep new and imported revisions unapproved until a route-specific benchmark and human review promote them.
- Derive each immutable RVC Training Set Revision from one accepted Canonical Reference. Historical, rejected, or identity-divergent audio never enters implicitly.
- Generate missing training material through a renderer conditioned by the Canonical Reference. Unconditioned generated material requires separate identity review before inclusion.
- Store Base Render, RVC Derivative, and Loudness Derivative as separate cache stages with independent fingerprints and invalidation.
- Fail open for a Quality Route Stability Layer: preserve the Base Render, record the fallback, and continue production when RVC is unavailable or fails quality checks.
- Use route-specific inference profiles. Quality profiles are conservative and expression-preserving; Fast Route profiles prioritize identity conversion.
- Run sentence inference through a launcher-owned persistent worker with bounded model caching, timeout, cancellation, and health reporting.
- Keep sentence-level conversion as the baseline. Chunked real-time RVC remains out of scope until boundary artifacts can be measured and controlled.

## Consequences

- Users cannot enable a Quality Route Stability Layer merely by binding files or completing training.
- Training, model, benchmark, and cache manifests become first-class project assets.
- Changing a Canonical Reference, model revision, or inference profile invalidates only the affected derivatives; it does not require regenerating an unchanged Base Render.
- The first stability-priority sentence may wait for sentence-level conversion, while later playback relies on lookahead generation and model prewarming.
- Existing RVC files remain importable but enter as unverified candidate revisions and require explicit artifact pairing and approval.
