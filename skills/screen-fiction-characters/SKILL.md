---
name: screen-fiction-characters
description: Screen noisy Chinese-fiction character candidates before expensive character profiling. Use when parser fragments, action phrases, generic roles, aliases, addressees, or narration words have been mistaken for named speakers, especially in long novels and hybrid local-to-cloud analysis.
---

# Screen Fiction Characters

Convert parser candidates into a conservative, reviewable set of canonical named characters before voice profiling.

## Workflow

1. Read [runtime-system-prompt.md](references/runtime-system-prompt.md).
2. Review each bounded candidate using its name shape, dialogue attribution, evidence, mention counts, dialogue counts, and cross-batch presence.
3. Return `keep` only for a named identity supported by the evidence.
4. Return `merge` when the candidate is an alias or shortened form of another input or anchor candidate.
5. Return `reject` for actions, connectors, descriptions, generic roles, addressees mistaken for speakers, and parser fragments.
6. Preserve uncertain but plausible low-frequency names with lower confidence. Frequency alone cannot reject a candidate.
7. Use only candidate IDs supplied by the caller. Emit exactly one decision for every current-batch candidate.

## Output

Return only the JSON schema supplied by the caller. A merge target must be a retained candidate from the current batch or canonical anchors.
