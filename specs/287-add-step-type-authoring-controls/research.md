# Research: Add Step Type Authoring Controls

## Classification

Decision: Treat MM-568 as a single-story runtime feature request.
Evidence: The Jira preset brief contains one story: a task author chooses Tool, Skill, or Preset from one Step Type control and sees the matching configuration. It references `docs/Steps/StepTypes.md` as runtime source requirements.
Rationale: The brief is independently testable through the Create page step editor and does not require story splitting.
Alternatives considered: Treat `docs/Steps/StepTypes.md` as a broad design and run breakdown. Rejected because Jira already selected a bounded Step Type authoring-controls slice.
Test implications: Focused Create page render/submission tests are required.

## Existing Artifact Resume

Decision: Start a new MM-568 feature directory at Specify and use existing Step Type specs only as implementation evidence.
Evidence: `rg -n "MM-568" specs` found no existing feature artifact preserving MM-568. Existing directories such as `specs/276-step-type-authoring-controls` and `specs/281-step-type-authoring` preserve different Jira keys.
Rationale: MoonSpec verification depends on preserving the original Jira issue key and brief in the active artifacts.
Alternatives considered: Reuse `specs/276-step-type-authoring-controls`. Rejected because it preserves MM-556, not MM-568.
Test implications: Add MM-568 traceability validation in final verification.

## FR-001 / SC-001 / DESIGN-REQ-001

Decision: Implemented and verified by current Create page code and active MM-568 tests.
Evidence: `frontend/src/entrypoints/task-create.tsx` defines `stepType` draft state and renders a `Step Type` control; `frontend/src/entrypoints/task-create-step-type.test.tsx` verifies one Step Type control with Tool, Skill, and Preset choices.
Rationale: The current test directly covers one accessible control and exact options.
Alternatives considered: Add another duplicate test. Rejected because current coverage is direct and sufficient.
Test implications: Rerun focused frontend unit tests.

## FR-002 / SC-002 / DESIGN-REQ-002

Decision: Implemented and verified.
Evidence: `STEP_TYPE_HELP_TEXT` contains helper copy for Tool, Skill, and Preset in `task-create.tsx`; `task-create-step-type.test.tsx` verifies the helper copy and absence of internal umbrella labels.
Rationale: The helper copy aligns with the source design: Tool is typed integration/system operation, Skill asks an agent, Preset inserts reusable configured steps.
Alternatives considered: Rewrite helper copy to match the docs verbatim. Rejected because the spec allows source-consistent concise copy and the existing copy is clear.
Test implications: Rerun focused frontend unit tests.

## FR-003 / SC-003

Decision: Implemented and verified.
Evidence: Create page renders conditional Skill, Tool, and Preset areas from `step.stepType`; active focused test `switches type-specific configuration while preserving instructions` verifies visible controls change.
Rationale: This directly covers type-specific configuration visibility.
Alternatives considered: Add browser E2E coverage. Rejected because the story is frontend-only and existing render/submission tests exercise the public UI contract.
Test implications: Rerun focused frontend unit tests.

## FR-004 / DESIGN-REQ-008

Decision: Implemented and verified for compatible instructions.
Evidence: The active switching test verifies instructions persist while changing Step Type.
Rationale: Instructions are the compatible cross-type field named in the Jira brief.
Alternatives considered: Require a modal confirmation for every incompatible value. Rejected because the source allows preserve, discard clearly, or confirmation, and current hidden-field submission safeguards satisfy the loss-prevention requirement for active submission.
Test implications: Rerun focused frontend unit tests.

## FR-005 / DESIGN-REQ-017

Decision: Implemented and verified.
Evidence: Step Type selector and options use Step Type, Tool, Skill, and Preset; active focused tests assert Capability, Activity, Invocation, Command, and Script are absent from the selector area.
Rationale: The primary discriminator does not expose the forbidden umbrella terminology.
Alternatives considered: Sweep the entire page for every forbidden word. Rejected because the source requirement applies to the primary Step Type discriminator, while narrow technical contexts may still use capability language.
Test implications: Rerun focused frontend unit tests.

## FR-006 / SC-004

Decision: Implemented and verified.
Evidence: Active focused test `preserves hidden Skill fields but blocks Tool submission without a selected Tool` verifies hidden Skill fields are preserved in draft but not submitted as active Tool configuration; Tool submission is blocked until a Tool is selected.
Rationale: This satisfies the requirement that incompatible hidden fields are not silently submitted.
Alternatives considered: Delete hidden Skill fields on every switch. Rejected because preserving draft data while preventing hidden submission is less destructive and still meets the source design.
Test implications: Rerun focused frontend unit tests.

## FR-007 / SC-005

Decision: Implemented and verified.
Evidence: Active focused test `keeps Preset selections scoped to each step` verifies independent per-step Preset state.
Rationale: Step-level state avoids cross-step leakage when multiple authored steps use Preset.
Alternatives considered: Treat per-step scoping as out of scope. Rejected because it is an edge case of one Step Type per authored step.
Test implications: Rerun focused frontend unit tests.

## Test Strategy

Decision: Use active focused Create page Vitest coverage as both unit and frontend integration boundary, then run the managed unit wrapper.
Evidence: The story is entirely within `frontend/src/entrypoints/task-create.tsx`; active MM-568 tests live in `frontend/src/entrypoints/task-create-step-type.test.tsx`; no backend route, storage, workflow, or external interface changes are planned.
Rationale: Testing Library render/submission tests exercise the UI contract and submission boundary without requiring Docker or provider credentials.
Alternatives considered: Compose-backed integration tests. Rejected because backend behavior does not change.
Test implications: Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx`.
