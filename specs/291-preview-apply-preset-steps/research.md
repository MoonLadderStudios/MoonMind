# Research: Preview and Apply Preset Steps

## FR-001 / DESIGN-REQ-011 - Step Editor Preset Selection

Decision: Implemented by existing Create page behavior and verified by active MM-578 tests.
Evidence: `frontend/src/entrypoints/task-create.tsx` exposes Step Type `Preset`; `frontend/src/entrypoints/task-create.test.tsx` covers one Step Type control, Preset selection, scoped selections, and MM-578 step-editor preset preview/apply.
Rationale: MM-578 requires preset use in the step editor rather than a separate management flow; active tests exercise the authoring boundary.
Alternatives considered: Add a new Presets section flow. Rejected because it conflicts with DESIGN-REQ-011 and DESIGN-REQ-019.
Test implications: Focused frontend unit/integration rerun is sufficient unless it fails.

## FR-002 / FR-008 / DESIGN-REQ-013 - Validation And Failure Handling

Decision: Implemented and verified by active preview failure and stale-detail tests.
Evidence: `task-create.test.tsx` covers MM-578 stale preset detail handling, failed expansion leaving the draft unchanged, generated-step validation failure messaging, and unresolved Preset submission blocking.
Rationale: The story requires draft-preserving failure modes and visible feedback before mutation.
Alternatives considered: Add backend validation tests. Rejected for this story because the public authoring boundary uses mocked task-template responses and no backend contract change is planned.
Test implications: Focused Create page Vitest and managed unit runner.

## FR-003 / FR-004 / FR-005 / DESIGN-REQ-012 - Preview Then Apply

Decision: Implemented by existing Create page preview/apply behavior and verified by active MM-578 tests.
Evidence: `task-create.tsx` holds per-step preview state; `task-create.test.tsx` covers MM-578 generated title/type/warning preview, apply replacement, editable generated steps, and executable tool binding submission.
Rationale: Preview must precede mutation, and apply must replace the temporary Preset step with executable steps.
Alternatives considered: Expand immediately on preset selection. Rejected because it removes the required preview decision point.
Test implications: Focused frontend unit/integration validation.

## FR-006 / DESIGN-REQ-004 - Executable Submission Boundary

Decision: Implemented and verified by active submission-blocking and generated Tool binding tests.
Evidence: `task-create.test.tsx` covers MM-578 unresolved Preset blocking and preset-generated Tool step submission after apply.
Rationale: Preset steps are authoring placeholders by default; executable submission must carry Tool/Skill steps.
Alternatives considered: Allow unresolved Preset submission. Rejected because MM-578 does not introduce linked-preset execution semantics.
Test implications: Focused frontend unit/integration validation.

## FR-007 / DESIGN-REQ-019 - Management vs Use Separation

Decision: Implemented and verified by active tests that use step-editor preset preview/apply without Task Presets management.
Evidence: `task-create.test.tsx` includes separate MM-578 coverage for step-editor preset preview/apply and Preset Management actions.
Rationale: The source brief explicitly separates management from use.
Alternatives considered: Route application through Preset Management. Rejected because it contradicts the source design.
Test implications: Focused frontend unit/integration validation.
