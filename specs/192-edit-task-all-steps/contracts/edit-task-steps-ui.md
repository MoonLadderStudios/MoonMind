# UI Contract: Edit Task Step Reconstruction

## Boundary

The shared `/tasks/new` page in edit or rerun mode consumes a `TemporalSubmissionDraft` from `buildTemporalSubmissionDraftFromExecution`.

## Inputs

- Execution detail for a supported `MoonMind.Run`.
- Optional input artifact or task input snapshot.
- Task data may include:
  - `task.instructions`
  - `task.steps[]`
  - `step.id`
  - `step.title`
  - `step.instructions`
  - `step.skill` or `step.tool`
  - `step.skill.args` / `step.skill.inputs` / `step.tool.inputs`
  - `step.skill.requiredCapabilities` / `step.tool.requiredCapabilities`

## Required Behavior

- If explicit valid steps exist, the draft exposes an ordered `steps` array.
- The edit form renders every draft step as a distinct form section.
- If no explicit valid steps exist, the form keeps the current single primary step behavior.
- Saving an unchanged loaded draft serializes later steps back into `task.steps`.

## Error Behavior

- Missing all task instructions, step content, and primary skill still fails reconstruction with the existing error.
- Malformed empty step entries are skipped instead of causing valid later steps to be dropped.

## Traceability

This contract implements MM-340 and maps to FR-001 through FR-006.
