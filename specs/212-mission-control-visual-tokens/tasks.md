# Tasks: Mission Control Visual Tokens and Atmosphere

**Input**: Design artifacts from `specs/212-mission-control-visual-tokens/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/visual-token-contract.md`, `quickstart.md`
**Tests**: Add focused UI/CSS contract tests before production CSS changes. Confirm they fail for the intended missing-token reason, then implement CSS until they pass.

## Phase 1: Setup

- [X] T001 Review `.specify/memory/constitution.md`, `README.md`, Jira Orchestrate preset behavior, and relevant Mission Control design docs.
- [X] T002 Create MM-424 MoonSpec artifacts under `specs/212-mission-control-visual-tokens/` and preserve the trusted Jira preset brief under `docs/tmp/jira-orchestration-inputs/`.

## Phase 2: Tests First

- [X] T003 Add CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` for shared atmosphere/glass/elevation tokens in `:root` and `.dark`. (FR-001, FR-002, SC-001)
- [X] T004 Add CSS contract tests proving `body`, `.dark body`, `.masthead::before`, and `.panel` consume the shared tokens. (FR-003, FR-004, SC-002, SC-003)
- [X] T005 Run the focused Mission Control test and confirm the new contract tests fail before production CSS changes. (FR-007)

## Phase 3: Implementation

- [X] T006 Add light and dark atmosphere/glass/input/elevation token definitions to `frontend/src/styles/mission-control.css`. (FR-001, FR-002)
- [X] T007 Update body, dark body, masthead, panel, and floating bar styling to consume the new tokens while preserving existing layout and readable semantic text tokens. (FR-003, FR-004, FR-005)

## Phase 4: Verification

- [X] T008 Attempt `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`, then run the direct Vitest equivalent after the npm script cannot resolve `vitest` in this container. (FR-006, FR-007)
- [X] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx` or document the exact blocker. (FR-007)
- [X] T010 Write `specs/212-mission-control-visual-tokens/verification.md` with coverage, commands, and verdict. (FR-008, SC-005)
- [X] T011 Commit the completed MM-424 work without pushing or creating a pull request.
