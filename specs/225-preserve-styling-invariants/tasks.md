# Tasks: Mission Control Styling Source and Build Invariants

**Input**: Design documents from `specs/225-preserve-styling-invariants/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `contracts/styling-invariants.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason when they expose a gap, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-430 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Original Jira reference and preset brief are preserved in `spec.md` and `spec.md` (Input). This task list maps all `FR-*`, acceptance scenarios, edge cases, `SC-*`, and `DESIGN-REQ-*` evidence to concrete test, implementation, and verification work.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/vite-config.test.ts`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/vite-config.test.ts`
- Final verification: `moonspec-verify` (`/speckit.verify` user-facing equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing frontend test and CSS inspection infrastructure is ready for MM-430.

- [X] T001 Confirm active feature artifacts exist for MM-430 in `specs/225-preserve-styling-invariants/spec.md`, `plan.md`, `research.md`, `contracts/styling-invariants.md`, and `quickstart.md`
- [X] T002 Confirm `frontend/src/entrypoints/mission-control.test.tsx` can read `frontend/src/styles/mission-control.css` through the existing PostCSS helper before adding MM-430 tests
- [X] T003 [P] Confirm `frontend/src/vite-config.test.ts` remains available for build/source boundary verification
- [X] T004 [P] Confirm `tailwind.config.cjs` is loadable from frontend tests for Tailwind source scanning coverage

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the traceability and test harness boundaries that block story work.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Map MM-430 requirement IDs and source IDs into this task list before editing tests in `specs/225-preserve-styling-invariants/tasks.md`
- [X] T006 Identify representative semantic shell selectors, additive modifiers, tokenized role selectors, theme token blocks, Tailwind scan paths, and generated dist boundaries in `frontend/src/styles/mission-control.css`, `api_service/templates/react_dashboard.html`, `api_service/templates/_navigation.html`, `tailwind.config.cjs`, and `frontend/vite.config.ts`
- [X] T007 Confirm no backend, Temporal, Jira, task submission payload, or generated dist files are required for this story by checking `specs/225-preserve-styling-invariants/plan.md`

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Preserve Styling Invariants

**Summary**: As a Mission Control maintainer, I want styling source and build invariants to be enforced so future UI work keeps stable semantic classes, token-first theming, complete source scanning, and generated-asset boundaries.

**Independent Test**: Inspect Mission Control source styling, component/template class usage, Tailwind content configuration, and generated asset boundaries. The story passes when semantic class compatibility is preserved, tokenized styling avoids opaque hardcoded role colors, documented source paths are included in Tailwind scanning, and source changes are made outside generated dist assets while existing Mission Control behavior remains intact.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, SC-008, DESIGN-REQ-001, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-026

**Test Plan**:

- Unit: CSS/config contract tests for semantic shell stability, additive modifiers, token-first role styling, light/dark token parity, Tailwind source scanning, and generated dist boundary protection.
- Integration: rendered Mission Control entrypoint and route regression tests for task list, create page, task detail/evidence, and shared Mission Control behavior to prove existing workflows still pass after invariant changes.

### Unit Tests (write first)

> NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason when they expose a gap, then implement only enough source code to make them pass.

- [X] T008 Add failing MM-430 semantic shell stability test for `dashboard-root`, `masthead`, `route-nav`, `panel`, `card`, `toolbar`, `status-*`, and compatible `queue-*` selectors covering FR-001, SC-001, DESIGN-REQ-024 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T009 Add failing MM-430 additive modifier test for shared variants such as `panel--controls`, `panel--data`, `panel--floating`, `panel--utility`, and data/table wide modifiers covering FR-002, SC-002, DESIGN-REQ-024 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T010 Add failing MM-430 token-first semantic role test covering representative shared surfaces while allowing non-role code/log exceptions for FR-003, SC-003, DESIGN-REQ-025 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T011 Add failing MM-430 light/dark token parity test for matching shared surfaces covering FR-004, SC-004, DESIGN-REQ-025 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T012 Add failing MM-430 Tailwind source scanning test covering `api_service/templates/react_dashboard.html`, `api_service/templates/_navigation.html`, and `frontend/src/**/*.{js,jsx,ts,tsx}` for FR-005, SC-005, DESIGN-REQ-025 in `frontend/src/vite-config.test.ts`
- [X] T013 Add failing MM-430 canonical source and generated dist boundary test covering `frontend/src/styles/mission-control.css`, source templates/components, and `api_service/static/task_dashboard/dist/` for FR-006, FR-007, SC-006, DESIGN-REQ-026 in `frontend/src/vite-config.test.ts`
- [X] T014 Add MM-430 traceability assertions or test names covering FR-008 and FR-010 in `frontend/src/entrypoints/mission-control.test.tsx` and `frontend/src/vite-config.test.ts`
- [X] T015 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/vite-config.test.ts` and capture red-first failures for T008-T014 or existing-pass verification for implemented_unverified rows

### Integration Tests (write first)

- [X] T016 Confirm Mission Control entry regression remains covered for FR-009 and SC-007 in `frontend/src/entrypoints/mission-control.test.tsx`
- [X] T017 Confirm task-list regression coverage for FR-009 and SC-007 in `frontend/src/entrypoints/tasks-list.test.tsx`
- [X] T018 Confirm create-page regression coverage for FR-009 and SC-007 in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T019 Confirm task-detail/evidence regression coverage for FR-009 and SC-007 in `frontend/src/entrypoints/task-detail.test.tsx`
- [X] T020 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/vite-config.test.ts` and capture expected red-first failures or existing-pass verification for T016-T019

### Conditional Implementation

- [X] T021 Conditionally update semantic shell selectors for FR-001 and DESIGN-REQ-024 in `frontend/src/styles/mission-control.css`, `api_service/templates/react_dashboard.html`, or `api_service/templates/_navigation.html` only if T008 exposes a gap
- [X] T022 Conditionally update additive shared modifier selectors for FR-002 and DESIGN-REQ-024 in `frontend/src/styles/mission-control.css` only if T009 exposes a gap
- [X] T023 Conditionally replace hardcoded opaque semantic role colors with `--mm-*` token usage for FR-003 and DESIGN-REQ-025 in `frontend/src/styles/mission-control.css` only if T010 exposes a role-color gap
- [X] T024 Conditionally update theme token declarations for FR-004 and DESIGN-REQ-025 in `frontend/src/styles/mission-control.css` only if T011 exposes a light/dark parity gap
- [X] T025 Conditionally update Tailwind content scanning for FR-005 and DESIGN-REQ-025 in `tailwind.config.cjs` only if T012 exposes a missing source path
- [X] T026 Conditionally update Vite/source-boundary tests or source comments for FR-006, FR-007, and DESIGN-REQ-026 without editing generated dist assets if T013 exposes a boundary gap
- [X] T027 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/vite-config.test.ts` and fix MM-430 unit/config failures
- [X] T028 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/vite-config.test.ts` and fix Mission Control regression failures
- [X] T029 Mark completed implementation tasks in `specs/225-preserve-styling-invariants/tasks.md` after tests pass, marking conditional tasks complete when skipped due passing verification

**Checkpoint**: The story is fully functional, covered by unit and integration-style UI tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T030 [P] Review MM-430 traceability in `specs/225-preserve-styling-invariants/spec.md`, `plan.md`, `tasks.md`, `contracts/styling-invariants.md`, and `spec.md` (Input)
- [X] T031 Run quickstart validation commands from `specs/225-preserve-styling-invariants/quickstart.md`
- [X] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/vite-config.test.ts` for final focused unit evidence
- [X] T033 Run `moonspec-verify` final verification and write the result to `specs/225-preserve-styling-invariants/verification.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on story tests and implementation passing

### Within The Story

- Unit and integration tests must be authored before conditional implementation tasks.
- Red-first or verification-first runs must complete before conditional fallback implementation.
- `mission-control.css` changes are centralized, so CSS implementation tasks should be sequenced rather than parallelized.
- Route regression tests run after CSS/config changes.
- Final `moonspec-verify` work runs only after tests pass and tasks are marked complete.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001-T002.
- T008-T011 and T014 touch the same test file and should be edited in one ordered batch.
- T012-T013 touch `frontend/src/vite-config.test.ts` and can be authored alongside the `mission-control.test.tsx` batch.
- T017, T018, and T019 are confirmation tasks for separate route test files and can be checked in parallel after unit/config tests are drafted.
- T030 can run in parallel with final validation command preparation.

## Parallel Example: Story Phase

```bash
# Separate files, safe to author together after Phase 2:
Task: "Add MM-430 CSS contract tests in frontend/src/entrypoints/mission-control.test.tsx"
Task: "Add MM-430 Tailwind/dist boundary tests in frontend/src/vite-config.test.ts"
Task: "Confirm task-list/create/detail regression coverage in existing route test files"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to lock the MM-430 traceability inventory.
2. Add focused MM-430 CSS/config tests first.
3. Confirm route-level regression tests for task list, create page, task detail/evidence, and Mission Control shared entry.
4. Run the focused tests and record red-first failures or existing-pass verification.
5. Implement only source CSS/config/template changes needed by failing MM-430 tests.
6. Do not hand-edit generated dist assets under `api_service/static/task_dashboard/dist/`.
7. Run targeted unit and integration-style UI tests.
8. Mark tasks complete and run final `moonspec-verify`.

## Notes

- This task list covers one story only: MM-430 styling source and build invariants.
- Backend, Temporal, Jira, task submission payload, and generated dist changes are out of scope unless a regression test exposes a direct break.
- `implemented_unverified` rows require verification tests first and conditional implementation only if verification fails.
- Preserve MM-430 and all source design IDs in final evidence.
