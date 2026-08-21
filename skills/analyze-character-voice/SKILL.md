---
name: analyze-character-voice
description: Generate evidence-backed Character Voice Profiles from fiction excerpts using controlled acoustic attributes plus distinctive, performable speech habits. Use when analyzing a character's timbre, delivery, age impression, stable personality cues, signature speaking behavior, synthesis constraints, or when reviewing and improving renderer voice prompts for Zw Voice Factory.
---

# Analyze Character Voice

Generate a stable, reviewable voice identity from bounded character evidence. Keep character identity separate from scene emotion and renderer-specific syntax.

## Workflow

1. Build a Character Evidence Pack containing the character name, aliases, mention count, dialogue count, gender hint, optional user-directed attributes, and at most eight representative excerpts. Never send a full novel.
2. Read [voice-attribute-taxonomy.json](references/voice-attribute-taxonomy.json) and [runtime-system-prompt.md](references/runtime-system-prompt.md). Select only IDs defined in the taxonomy and follow the runtime evidence hierarchy.
3. Separate evidence into stable acoustic cues, repeated speech habits, personality cues that affect delivery, and uncertain claims.
4. When user-directed attributes are present, translate them into audible dimensions first, then use the excerpts to make those attributes specific to the character's repeated sentence structure and speaking behavior.
5. Prefer `unknown` over unsupported gender or age claims. Do not infer anatomy, ethnicity, attractiveness, or health.
6. Select one value for every acoustic and delivery dimension, one or two texture values, up to four personality tags, and one to three constraints.
7. Write a distinctive core that connects acoustic placement with observable speaking behavior, then list two or three performable habits. Apply the runtime prompt's name-removal test before accepting it.
8. Compile a neutral voice description in this order: distinctive core; stable habits; acoustic profile; delivery baseline; synthesis constraints.
9. State confidence and a short evidence rationale. Do not include plot summary or current scene emotion.

## Output Contract

Return JSON with these fields:

```json
{
  "gender": "male|female|unknown",
  "age_range": "taxonomy ID",
  "personality_tags": ["taxonomy ID"],
  "pitch": "taxonomy ID",
  "weight": "taxonomy ID",
  "brightness": "taxonomy ID",
  "texture": ["taxonomy ID"],
  "resonance": "taxonomy ID",
  "articulation": "taxonomy ID",
  "breath": "taxonomy ID",
  "pace": "taxonomy ID",
  "rhythm": "taxonomy ID",
  "dynamics": "taxonomy ID",
  "baseline": "taxonomy ID",
  "constraints": ["taxonomy ID"],
  "signature_core": "20-100 个汉字，连接声学落点与稳定表达行为",
  "signature_habits": ["2-3 个可观察、可表演的稳定说话习惯"],
  "confidence": 0.0,
  "rationale": "不超过 120 个汉字的证据说明"
}
```

## Quality Rules

- Treat words such as “好听、自然、有辨识度、磁性” as insufficient unless decomposed into controlled acoustic dimensions.
- Do not copy personality adjectives directly into timbre fields.
- Do not let anger, sadness, excitement, whispering, or shouting become part of the neutral identity unless evidence shows a stable habitual baseline.
- Resolve conflicting excerpts by favoring repeated neutral behavior. Lower confidence when evidence remains contradictory.
- Reject a distinctive core that still fits half of ordinary characters after removing the name. Rewrite it with evidence-backed sentence structure, pauses, turns, or interaction habits.
- Keep the compiled description concrete enough for synthesis and short enough to edit manually.
