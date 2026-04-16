# Research: Edit Task Shows All Steps

## Draft Reconstruction Source

Decision: Preserve explicit step records in `TemporalSubmissionDraft` in addition to the existing flattened `taskInstructions` string.
Rationale: The current helper already reads `task.steps`, but it merges instructions into a single string. Keeping ordered step records lets the form render all steps without changing backend contracts.
Alternatives considered: Backend reconstruction endpoint. Rejected because the issue is reproducible in frontend draft application and existing execution detail already carries task step data.

## Form Initialization

Decision: Apply reconstructed draft steps to `StepState[]` when present, and fall back to the existing single-step initialization when no step array is available.
Rationale: This preserves single-step behavior and fixes multi-step truncation without changing create-mode defaults.
Alternatives considered: Split the flattened instruction string on blank lines. Rejected because that would guess user intent and lose step metadata.

## Step Metadata Preservation

Decision: Map known step fields into existing `StepState`: `id`, `title`, `instructions`, skill/tool name, skill inputs/args, required capabilities, and template instruction identity.
Rationale: These fields already participate in submit serialization, so preserving them prevents unchanged steps from being dropped or merged.
Alternatives considered: Preserve raw step payloads separately. Rejected because submit already serializes from `StepState` and a second raw payload path would create conflicting sources of truth.

## Test Strategy

Decision: Add focused Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx` for helper reconstruction, edit-page rendering, and unchanged save payload preservation.
Rationale: This is the highest-risk UI path and is already covered by adjacent edit/rerun tests using mocked execution detail responses.
Alternatives considered: Compose-backed integration tests. Rejected for this slice because no backend/service boundary changes are planned.
