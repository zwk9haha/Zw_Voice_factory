# Voice platform boundary and reusable catalog

The product is expanded from a novel-only workbench into a Voice Platform with a shared `Voice Project`/`Voice Task` model. Novel processing remains a scenario adapter that contributes chapter parsing, long-form slicing, character evidence, and Director Documents; short text, scripts, and subtitles can use the same production kernel without pretending to be novels.

Reusable voices are owned by a platform-level `Voice Catalog`, while each project stores a `Project Voice Snapshot` pinned to an immutable catalog version. The platform uses Match-or-Generate: it first searches approved project and catalog assets, then generates a new asset only when no suitable match exists. This prevents repeated generation while ensuring later catalog edits cannot silently alter an existing project.

## Consequences

- Existing novel assets remain valid and are migrated by adapters rather than rewritten in one large change.
- Voice metadata needs provenance, version, quality, compatibility, and publication state in addition to an audio path.
- A catalog update requires an explicit project rebind or upgrade action.
- The first release remains local-first and single-user; accounts, collaboration, and a public marketplace are outside this phase.
