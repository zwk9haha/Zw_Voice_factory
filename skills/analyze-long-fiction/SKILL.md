---
name: analyze-long-fiction
description: Analyze long fiction without sending the whole manuscript in one request. Use for chapter-boundary detection, sentence-safe character-count splitting, batch-local character importance, compact model inputs, concurrent director analysis, and streaming preparation of novels that may contain hundreds of chapters or multi-megabyte TXT files.
---

# Analyze Long Fiction

Convert a large novel into ordered analysis batches while preserving characters who are important inside one arc but minor across the whole book.

## Workflow

1. Measure decoded character count before model inference.
2. Keep short texts as one batch.
3. Prefer explicit chapter headings when at least two reliable headings exist.
4. For nonstandard headings, extract short standalone candidate lines locally and send only candidate IDs, line numbers, offsets, titles, and rule scores to the model.
5. If fewer than two headings survive validation, split near the configured character target and extend to the next complete sentence boundary.
6. Scan character evidence independently inside each batch.
7. Merge canonical characters across batches, retaining total counts and the highest batch-local importance.
8. Analyze director passages in source order. Parallelize API sub-batches inside the current long-form batch, then persist that batch before moving forward.

## Invariants

- Never submit the complete long-form manuscript merely to detect chapters.
- Never cut in the middle of a sentence when a nearby terminal boundary exists.
- Never demote a character solely because their total-book share is low when they dominate one batch.
- Keep batch offsets stable and non-overlapping; concatenating batches must reproduce the source text exactly.
- Use explicit attributions and reviewed aliases before model inference.
- Keep API payloads compact, schema-constrained, and independently retryable.
- Preserve source order when merging concurrent results.

## Structure Classification

Read [runtime-system-prompt.md](references/runtime-system-prompt.md) before selecting nonstandard chapter headings. Return only the schema supplied by the caller.

Treat a line as a chapter boundary only when its sequence and formatting are consistent with multiple neighboring candidates. Reject ordinary dialogue, narration fragments, scene slogans, timestamps, and accidental short lines.

## Batch Weighting

For each character, retain:

- total mentions across all batches;
- peak mentions in one batch;
- number of batches containing the character;
- highest normalized importance within any batch.

Use the highest batch-local normalized importance for production eligibility. Use total counts only as supporting evidence.

## Director Streaming

Process long-form batches sequentially so earlier chapters become usable first. Within one batch, run independent API sub-batches concurrently up to the configured limit. Persist completed segments and batch state before starting the next long-form batch.
