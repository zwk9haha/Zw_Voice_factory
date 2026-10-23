# Read-only execution details alongside workflow authoring

The workflow canvas remains the editor for draft nodes, typed ports, and publication. A separate execution view borrows DSH's inspection approach: select a saved run, follow its recorded node activity, and inspect resolved input, execution events, output, and associated audio jobs.

## Decision

- `WorkflowRunDetails` is shared by the studio and project run surface. Its interface accepts a saved run and optional node selection / canvas location callback. It never changes the draft or executes nodes.
- The studio keeps its canvas mounted, with exactly its original toolbar and viewport inside the canvas shell. Switching views hides that layout and remeasures port anchors when returning.
- Execution order means latest recorded start time, with unstarted nodes at the end. The input → execution → output cards describe one node's recorded activity; they do not redefine graph dependencies or imply tool calls ran sequentially.
- Run version and project come from the saved run. Legacy records explicitly show missing version metadata. Current draft titles/configuration never stand in for historical evidence.
- Only the latest node attempt is stored. Retained events can explain earlier attempts; the UI does not invent earlier input/output snapshots. The backend retains at most 256 events.
- Audio job details load only on selection. Job records reflect their current state, which may include later RVC or loudness processing. Missing job files do not prevent inspection of saved node output.
- JSON inspection expands and pages on demand. This supports the 887-segment Doupo demo without rendering or stringifying its entire output on every update.

## Limits

This release adds observation of existing records. Raw model requests/responses, prompts, token counts, and per-attempt snapshots have not been collected and are not presented as available. No workflow executor behavior or saved workflow graph is changed.

## Validation

`Start-ZwVoice.cmd test` includes execution projection and rendered-record checks, large-array pagination, legacy metadata, and a canvas-shell structure regression check, alongside existing connection checks, TypeScript, and the production build. Use the visible launcher owner for manual browser verification.

Manual verification used the existing published Doupo demo: all eight node records and 887 audio jobs loaded, searching for 萧炎 returned matching records, and selecting a line loaded its saved `bazong` task and audio URL. Locating its render node returned to the intact canvas. The project surface also switched from the completed v2.0 run to the failed v1.0 run and displayed that run's original analysis error.

Related backend validation selects `tests/test_workflows_api.py` and excludes the previously failing `test_split_quality_workflow_uses_selected_model_and_requires_reference`. That executor error-propagation failure is outside this read-only UI change and remains unresolved; a passing selected suite is not a claim that the full backend suite passes.
