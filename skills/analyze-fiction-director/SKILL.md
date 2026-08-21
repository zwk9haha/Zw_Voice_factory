---
name: analyze-fiction-director
description: Convert Chinese fiction into a speaker-accurate audiobook director script by separating narration from quoted dialogue, resolving dialogue attribution from local context, and assigning bounded performance directions. Use when generating, reviewing, or repairing Zw Voice Factory director files, especially when a character is mistaken for the narrator, an addressee is mistaken for the speaker, or speech verbs inside narration cause false dialogue detection.
---

# Analyze Fiction Director

Generate a reviewable director document from bounded fiction passages. Preserve source order and stable IDs while keeping narration, dialogue, speaker identity, and scene performance as separate decisions.

## Workflow

1. Split source text deterministically at chapter boundaries and matched quotation marks. Keep text outside quotes as narration, even when it contains words such as "道、说道、苦涩地道".
2. Remove quotation marks from spoken text. Keep attribution clauses as narrator text instead of making a character read "某某说道".
3. Build a character table from reviewed canonical names, aliases, and gender hints. Never invent a new named character in the director file.
4. Resolve each quoted passage from its local previous, current, and next source lines. Treat an explicit named attribution as binding.
5. Resolve pronouns against the nearest compatible active subject. Treat names used as forms of address as listeners, not automatically as speakers.
6. Use turn-taking only as supporting evidence. Do not alternate speakers mechanically when narration or a new attribution resets the scene.
7. For each dialogue, classify `speaker_gender` as `male`, `female`, or `unknown`, using reviewed character gender first and explicit local identity clues second.
8. Classify `speaker_kind` as `named`, `extra`, or `unknown`. Generic guards, attendants, passers-by, crowd voices, and one-line temporary speakers are `extra`; do not invent a name for them.
9. Return `未知角色` when evidence remains insufficient. Let the application expose the uncertainty instead of silently assigning a confident but wrong role.
10. Assign controlled emotion, intensity, and tone only after speaker attribution. Keep neutral lines natural and avoid turning narration adjectives into persistent voice identity.

## Quality Checks

- A sentence outside quotation marks remains narration regardless of speech-like verbs.
- "萧炎哥哥。" following "萧熏儿柔声道" belongs to 萧熏儿; 萧炎 is the addressee.
- "望着……萧炎苦涩的道，她……" remains narration when no quoted speech follows.
- Every input passage appears exactly once and in source order.
- Every speaker is a reviewed canonical character or `未知角色`.
- Explicit attribution overrides model inference.
- A reviewed named speaker is `named`; a clearly temporary generic speaker is `extra`; unresolved evidence is `unknown`.
- Downstream rendering reuses the matching male/female narrator when gender is clear, may diversify `extra` voices, and uses the narrator gender opposite the global narrator when gender is unknown.

## Runtime Prompt

Read [runtime-system-prompt.md](references/runtime-system-prompt.md) before producing structured director decisions. Return only the schema requested by the caller.
