# Research: Add Step Type Authoring Controls

## Classification

Decision: MM-568 is a single-story runtime feature request.
Evidence: The trusted Jira brief describes one task-author journey: choose Tool, Skill, or Preset from one Step Type control and see the matching form.
Rationale: The brief references `docs/Steps/StepTypes.md` as source requirements, but the selected sections are scoped to one independently testable Create page behavior.
Alternatives considered: Treating `docs/Steps/StepTypes.md` as a broad declarative design was rejected because MM-568 selects a narrow authoring-control slice.
Test implications: One focused frontend UI test set is sufficient for the behavior boundary.

## Existing Step Type Selector

Decision: FR-001, FR-002, FR-003, FR-005, FR-006, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-017 were already implemented and covered.
Evidence: `frontend/src/entrypoints/task-create.tsx` defines `STEP_TYPE_OPTIONS`, `STEP_TYPE_HELP_TEXT`, and conditional Tool/Skill/Preset panels; `frontend/src/entrypoints/task-create.test.tsx` contains Step Type selector, helper copy, switching, and independent Preset scoping tests.
Rationale: The existing UI already exposes one Step Type selector with the correct options and drives panel visibility from selected Step Type.
Alternatives considered: Rebuilding the selector as a new component was rejected because the existing entrypoint already owns the behavior and tests.
Test implications: Preserve and rerun existing Step Type UI tests.

## Incompatible Data Handling

Decision: FR-004, SC-003, and DESIGN-REQ-008 required code and test updates.
Evidence: Previous `handleStepTypeChange` only changed `stepType` and cleared `presetPreview`, leaving incompatible hidden Skill/Tool/Preset values available if the user switched back.
Rationale: MM-568 requires incompatible meaningful data to be visibly discarded or guarded by confirmation. Visible discard is simpler, deterministic, and keeps submission state aligned with visible UI.
Alternatives considered: Confirmation dialogs were rejected because they add modal flow and are unnecessary when a clear notice plus preserving shared instructions satisfies the acceptance criterion.
Test implications: Update the Step Type test to assert incompatible Skill fields are cleared and a visible discard notice appears.

## Verification Strategy

Decision: Use the repo test wrapper for focused Create page Vitest coverage and attempt final unit verification.
Evidence: Repo instructions state frontend targets should run through `./tools/test_unit.sh --ui-args` when dependencies need preparation.
Rationale: The change is frontend-only; no API, database, Temporal, or compose boundary changes are involved.
Alternatives considered: Running raw `npm run ui:test` first failed because `vitest` was not installed in `node_modules`.
Test implications: Record both the raw command failure and wrapper-based verification in final evidence.
