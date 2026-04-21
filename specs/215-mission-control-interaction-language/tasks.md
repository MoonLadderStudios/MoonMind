# Tasks: Mission Control Shared Interaction Language

**Input**: Design artifacts from `specs/215-mission-control-interaction-language/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/interaction-language.md`, `quickstart.md`  
**Tests**: Add focused CSS contract tests before production CSS changes. Confirm the new tests fail for missing interaction tokens or legacy lift, then implement until they pass.

## Phase 1: Setup

- [X] T001 Review `.specify/memory/constitution.md`, `README.md`, Jira Orchestrate preset behavior, and `docs/UI/MissionControlDesignSystem.md`.
- [X] T002 Create MM-427 MoonSpec artifacts under `specs/215-mission-control-interaction-language/` and preserve the trusted Jira preset brief under `docs/tmp/jira-orchestration-inputs/`.

## Phase 2: Tests First

- [X] T003 Add focused CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` proving interaction tokens exist. (FR-001, SC-001)
- [X] T004 Add focused CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` proving routine controls use tokenized scale behavior without `translateY`. (FR-002, FR-003, SC-002)
- [X] T005 Add focused CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` proving compact controls, chips, focus rings, disabled posture, and reduced-motion rules align with shared tokens. (FR-004, FR-005, FR-006, FR-007, FR-008, SC-003)
- [X] T006 Run the focused Mission Control test and record red-first evidence before implementation. Initial direct Vitest run failed on the new MM-427 assertions because interaction tokens were absent, routine controls still used `translateY`, and compact controls were layout-only. (FR-010)

## Phase 3: Implementation

- [X] T007 Add shared interaction, focus, disabled, and compact-control tokens to `frontend/src/styles/mission-control.css`. (FR-001)
- [X] T008 Update primary/default buttons, secondary buttons, `.button`, `.queue-action`, and `.queue-submit-primary` in `frontend/src/styles/mission-control.css` to use shared scale-only hover/press behavior. (FR-002, FR-003)
- [X] T009 Update icon/extension controls in `frontend/src/styles/mission-control.css` to use the same no-lift hover/press and focus-visible language. (FR-002, FR-006)
- [X] T010 Update inline toggles, page-size filters, and active filter chips in `frontend/src/styles/mission-control.css` to use the compact-control shell while preserving wrapping. (FR-004, FR-005)
- [X] T011 Add disabled and reduced-motion guards for shared controls in `frontend/src/styles/mission-control.css`. (FR-007, FR-008)

## Phase 4: Verification

- [X] T012 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx` or the direct Vitest equivalent if npm cannot resolve `vitest`. The npm script could not resolve `vitest`; the direct local binary passed both focused UI files. (FR-009, FR-010)
- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx` or document the exact blocker. (FR-009, FR-010)
- [X] T014 Write `specs/215-mission-control-interaction-language/verification.md` with coverage, commands, and verdict. (FR-011, SC-005)
- [X] T015 Commit the completed MM-427 work without pushing or creating a pull request.
