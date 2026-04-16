# Data Model: Edit Task Shows All Steps

## TemporalSubmissionDraft

Represents the create/edit/rerun form state reconstructed from execution detail and optional input artifacts.

Fields added or clarified:

- `steps`: ordered list of reconstructed editable task steps. Optional for compatibility with current single-step paths.
- `taskInstructions`: existing primary instruction string used for legacy single-step fallback and objective-level instructions.

Validation rules:

- `steps` preserves source order.
- Empty or non-object step entries are ignored.
- At least one of `taskInstructions`, `primarySkill`, or a reconstructed step with meaningful instructions or skill metadata must exist.

## EditableTaskStep

Represents one task step in the shared task form.

Fields:

- `id`: optional durable/template step id.
- `title`: optional display title.
- `instructions`: step instructions.
- `skillId`: explicit skill or tool name/id.
- `skillArgs`: serialized JSON input for the skill.
- `skillRequiredCapabilities`: comma-separated capabilities already used by submit serialization.
- `templateStepId`: optional original template step id when the step is template-bound.
- `templateInstructions`: optional template instruction baseline.

Validation rules:

- Step entries with all fields empty may be omitted during reconstruction.
- Object-shaped skill inputs must serialize to JSON for display in the skill-args field.
- Valid later steps must remain valid even if earlier entries are empty.

## TaskEditUpdatePayload

Existing submit/update payload generated from `StepState[]`.

State transition:

1. Execution detail/input artifact -> `TemporalSubmissionDraft`.
2. `TemporalSubmissionDraft.steps` -> `StepState[]`.
3. `StepState[]` -> task create/edit payload.

Invariant:

- Unchanged reconstructed later steps remain present in the submitted payload unless removed by the operator.
