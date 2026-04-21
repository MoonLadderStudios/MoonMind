# Tasks: Mission Control Visual Tokens and Atmosphere

**Input**: Design artifacts from `specs/212-mission-control-visual-tokens/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/visual-token-contract.md`, `quickstart.md`
**Tests**: Add focused UI/CSS contract tests before production CSS changes. Confirm they fail for the intended missing-token reason, then implement CSS until they pass.
**Story**: One story only, "Visual Tokens and Atmosphere" from trusted Jira issue MM-424.
**Independent Test**: Inspect `frontend/src/styles/mission-control.css` and render the shared Mission Control app shell. The story passes when the token contract exists in light and dark themes, body atmosphere uses tokenized violet/cyan/warm layers, shared chrome consumes glass/elevation tokens, and route behavior remains unchanged.
**Traceability IDs**: FR-001 through FR-008, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-027, MM-424.

## Unit Test Plan

Focused unit-style CSS contract assertions live in `frontend/src/entrypoints/mission-control.test.tsx`. They validate token definitions in `:root` and `.dark`, body atmosphere usage, and shared chrome token consumption.

## Integration Test Plan

Integration-style app-shell rendering coverage stays in `frontend/src/entrypoints/mission-control.test.tsx`. It verifies shared Mission Control route loading, dashboard alerts, shell width behavior, and unknown-page behavior while the token contract is active. No compose-backed integration suite is required for this CSS-only design-system story.

## Phase 1: Setup

- [X] T001 Review `.specify/memory/constitution.md`, `README.md`, Jira Orchestrate preset behavior, and relevant Mission Control design docs.
- [X] T002 Create MM-424 MoonSpec artifacts under `specs/212-mission-control-visual-tokens/` and preserve the trusted Jira preset brief under `docs/tmp/jira-orchestration-inputs/`.

## Phase 2: Red-First Tests

- [X] T003 Add red-first unit CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` for shared atmosphere/glass/elevation tokens in `:root` and `.dark`. (FR-001, FR-002, SC-001, DESIGN-REQ-001, DESIGN-REQ-009)
- [X] T004 Add red-first integration-style app-shell/CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` proving `body`, `.dark body`, `.masthead::before`, and `.panel` consume the shared tokens while existing shell behavior remains covered. (FR-003, FR-004, FR-006, SC-002, SC-003, SC-004, DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011)
- [X] T005 Run the focused Mission Control test and confirm the new contract tests fail before production CSS changes. (FR-007)

## Phase 3: Implementation

- [X] T006 Add light and dark atmosphere/glass/input/elevation token definitions to `frontend/src/styles/mission-control.css`. (FR-001, FR-002, SC-001)
- [X] T007 Update body, dark body, masthead, panel, and floating bar styling in `frontend/src/styles/mission-control.css` to consume the new tokens while preserving existing layout and readable semantic text tokens. (FR-003, FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-027)

## Phase 4: Story Validation And Final Verify

- [X] T008 Run story validation with `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`, then run the direct Vitest equivalent after the npm script cannot resolve `vitest` in this container. (FR-006, FR-007, SC-004)
- [X] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx` or document the exact blocker. (FR-007)
- [X] T010 Run traceability validation for MM-424, trusted Jira preset brief preservation, and DESIGN-REQ coverage across `specs/212-mission-control-visual-tokens/` and `docs/tmp/jira-orchestration-inputs/MM-424-moonspec-orchestration-input.md`. (FR-008, SC-005)
- [X] T011 Run final `/moonspec-verify` work and write `specs/212-mission-control-visual-tokens/verification.md` with coverage, commands, and verdict. (FR-001 through FR-008, SC-001 through SC-005)
- [X] T012 Commit the completed MM-424 work without pushing or creating a pull request.

## Dependencies And Order

- T001-T002 must complete before any test or implementation task.
- T003-T005 must complete before T006-T007.
- T006-T007 must complete before T008-T011.
- T012 runs last after final verification evidence is recorded.
