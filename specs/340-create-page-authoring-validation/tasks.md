# Tasks: Create Page Authoring Validation

**Input**: Design documents from `/work/agent_jobs/mm:8b6e11c5-c4b6-4f5e-80a5-c5c38990639b/repo/specs/340-create-page-authoring-validation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/create-page-authoring-validation.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: This task list covers exactly one independently testable story: Create page authoring and validation for MM-641.

**Source Traceability**: MM-641 and the original Jira preset brief are preserved in `spec.md`; tasks reference FR-001 through FR-012, SCN-001 through SCN-005, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-005 plus original Jira coverage ID DESIGN-REQ-007.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`; final unit suite `./tools/test_unit.sh`
- Integration tests: `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q --tb=short` and `pytest tests/integration/api/test_task_contract_normalization.py -m integration_ci -q --tb=short`; final suite `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work.
- Each task includes exact file paths and requirement, scenario, success, or source IDs where applicable.

## Source Traceability Summary

| Status | IDs |
| --- | --- |
| missing | SC-001 |
| partial | FR-002, FR-003, FR-004, FR-006, FR-007, FR-009, SCN-001, SCN-002, SCN-004, SC-002, SC-004, DESIGN-REQ-002, DESIGN-REQ-003 |
| implemented_unverified | FR-001, FR-005, FR-010, FR-011, SCN-005, SC-005, DESIGN-REQ-001, DESIGN-REQ-005, DESIGN-REQ-007 |
| implemented_verified | FR-008, FR-012, SCN-003, SC-003, DESIGN-REQ-004 |

Rows with `missing` or `partial` require red-first tests and implementation. Rows with `implemented_unverified` require verification tests and conditional fallback implementation if verification fails. Rows with `implemented_verified` remain covered by existing evidence and final verification.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing feature artifacts and test harness before story work starts.

- [X] T001 Review `specs/340-create-page-authoring-validation/spec.md`, `specs/340-create-page-authoring-validation/plan.md`, and `specs/340-create-page-authoring-validation/contracts/create-page-authoring-validation.md` for MM-641 scope, one-story boundary, and preserved Jira preset brief (FR-012, SC-005)
- [X] T002 [P] Inspect existing Create page unit test helpers in `frontend/src/entrypoints/task-create.test.tsx` for repository, branch, publish mode, attachments, dependencies, presets, and Jira provenance setup reuse (FR-002, FR-003, FR-007, FR-011)
- [X] T003 [P] Inspect existing task payload boundary tests in `tests/integration/temporal/test_task_shaped_submission_normalization.py` and `tests/integration/api/test_task_contract_normalization.py` for canonical `task.git.branch` and `targetBranch` evidence (FR-008, SCN-003, SC-003, DESIGN-REQ-004)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare reusable test fixtures and identify exact current-failure assertions before production code changes.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T004 Add or update reusable test helpers in `frontend/src/entrypoints/task-create.test.tsx` for locating the Steps card, submit controls, latest `/api/executions` payload, and combined draft setup without changing production code (FR-002, FR-004, SCN-001, SC-004)
- [X] T005 [P] Add a comment-free helper or fixture in `frontend/src/entrypoints/task-create.test.tsx` for a combined valid draft containing branch, publish mode, objective attachment, step attachment, dependency, preset metadata, and Jira provenance setup (FR-007, FR-011, SCN-004, SC-004, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T006 [P] Confirm no new persistent storage, migrations, task tables, or external credentials are needed by reviewing `specs/340-create-page-authoring-validation/plan.md` and `specs/340-create-page-authoring-validation/data-model.md` before story implementation (FR-012, DESIGN-REQ-001)

**Checkpoint**: Foundation ready; red-first story test work can begin.

---

## Phase 3: Story - Create Page Authoring and Validation

**Summary**: As a Mission Control user, I want the Create page to collect and validate my full task draft while rendering Repository, Branch, and Publish Mode together inside the Steps card so that I can author tasks in task terms and submit a coherent task-shaped payload.

**Independent Test**: Author Create page drafts with repository, branch, publish mode, runtime, dependencies, presets/Jira state, and attachments; invalid drafts are blocked before submission, and valid drafts produce one task-shaped payload preserving authored intent.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-007

**Test Plan**:

- Unit: Create page DOM placement, accessible labels, invalid draft blocking, valid task payload shaping, attachments, dependencies, presets, Jira provenance, branch/publish semantics.
- Integration: Execution-visible task payload and task input snapshot boundaries for canonical `task.git.branch`, no `targetBranch`, attachment refs, provenance, dependencies, publish mode, and runtime.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [X] T007 [P] Add a failing unit test in `frontend/src/entrypoints/task-create.test.tsx` asserting Repository, Branch, and Publish Mode controls are inside `[data-canonical-create-section="Steps"]` and not owned only by the submit/floating bar (FR-004, SCN-001, SC-001, DESIGN-REQ-003)
- [X] T008 [P] Add failing unit tests in `frontend/src/entrypoints/task-create.test.tsx` proving invalid repository, branch-publish without branch, invalid publish mode, attachment-policy, and dependency drafts are blocked before `/api/executions` after the controls move (FR-003, FR-006, SCN-002, SC-002, DESIGN-REQ-002)
- [X] T009 [P] Add a failing unit test in `frontend/src/entrypoints/task-create.test.tsx` proving a combined valid draft preserves objective text, steps, runtime, repository, `task.git.branch`, `task.publish.mode`, dependencies, task and step attachments, authored presets, applied templates, and Jira provenance in the submitted payload (FR-002, FR-005, FR-007, FR-009, FR-010, FR-011, SCN-004, SCN-005, SC-004, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T010 [P] Add or update unit assertions in `frontend/src/entrypoints/task-create.test.tsx` preserving existing canonical branch behavior: submitted task uses `task.git.branch` and does not include `targetBranch` or `startingBranch` (FR-008, SCN-003, SC-003, DESIGN-REQ-004)
- [X] T011 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and confirm T007-T009 fail for the expected placement/combined-coverage reasons while T010 preserves existing canonical branch evidence before implementation (FR-004, FR-008, SC-001, SC-003)

### Integration Tests (write first) ⚠️

- [X] T012 [P] Add or extend an integration test in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving execution-visible task parameters and task input snapshots preserve `task.git.branch`, `task.publish.mode`, attachments, dependencies, authored presets, applied templates, and Jira provenance for a valid task-shaped submission (FR-007, FR-008, FR-011, SCN-003, SCN-004, SC-003, SC-004, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T013 [P] Add or extend an integration test in `tests/integration/api/test_task_contract_normalization.py` proving legacy `targetBranch` remains rejected or removed according to the current canonical task contract while `task.git.branch` remains accepted (FR-008, SCN-003, SC-003, DESIGN-REQ-004)
- [X] T014 Run `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q --tb=short` and `pytest tests/integration/api/test_task_contract_normalization.py -m integration_ci -q --tb=short`; confirm new or changed integration tests fail only where implementation evidence is missing, or pass as verification-only evidence for already implemented backend behavior (FR-007, FR-008, FR-011, SC-003, SC-004)

### Red-First Confirmation ⚠️

- [X] T015 Record red-first evidence in `specs/340-create-page-authoring-validation/tasks.md` by noting the focused frontend failure mode for Steps-card placement and any integration failure or verification-pass result before production code changes (FR-004, SC-001, DESIGN-REQ-003)
- [X] T016 Confirm no production files under `frontend/src/entrypoints/task-create.tsx`, `frontend/src/styles/mission-control.css`, or `api_service/api/routers/executions.py` have been changed before T011 and T014 complete (FR-003, FR-004, FR-007)

### Conditional Fallback Implementation for Implemented-Unverified Rows

- [X] T017 If T009 or T012 exposes missing attachment target preservation, update `frontend/src/entrypoints/task-create.tsx` and, only if backend normalization is implicated, `api_service/api/routers/executions.py` to preserve task/step `inputAttachments` through the relocated controls (FR-005, SCN-005, DESIGN-REQ-005)
- [X] T018 If T009 or T012 exposes missing preset, applied-template, Jira, or step source provenance, update `frontend/src/entrypoints/task-create.tsx` and, only if backend normalization is implicated, `api_service/api/routers/executions.py` to preserve that metadata (FR-011, SC-004, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T019 If T007 exposes task-first copy or accessibility regressions, update `frontend/src/entrypoints/task-create.tsx` labels/grouping so Repository, Branch, and Publish Mode remain task authoring controls inside the Steps card (FR-001, DESIGN-REQ-001)
- [X] T020 If T009 or T014 exposes branch/publish semantic regressions, update `frontend/src/entrypoints/task-create.tsx` and, only if necessary, `api_service/api/routers/executions.py` to preserve `task.git.branch`, publish mode, and no-`targetBranch` invariants (FR-008, FR-009, FR-010, SC-003, DESIGN-REQ-004)

### Implementation

- [X] T021 Move the Repository, Branch, and Publish Mode controls from the submit/floating control ownership into the Steps card in `frontend/src/entrypoints/task-create.tsx`, preserving existing state variables, accessible labels, branch lookup behavior, and submit payload fields (FR-004, FR-009, SCN-001, SC-001, DESIGN-REQ-003)
- [X] T022 Update submit action layout in `frontend/src/entrypoints/task-create.tsx` so task submission remains available after the controls move and the submit action no longer establishes the sole semantic container for Repository, Branch, and Publish Mode (FR-001, FR-004, SCN-001)
- [X] T023 Update Create page styling in `frontend/src/styles/mission-control.css` so the relocated repository/branch/publish controls fit inside the Steps card on desktop and mobile without overlapping step controls or submit actions (FR-004, SC-001)
- [X] T024 Update frontend validation flow in `frontend/src/entrypoints/task-create.tsx` only as needed so repository, branch, publish mode, runtime, dependency, and attachment-policy errors still block submission after relocation (FR-003, FR-006, SCN-002, SC-002, DESIGN-REQ-002)
- [X] T025 Update frontend submission shaping in `frontend/src/entrypoints/task-create.tsx` only as needed so valid drafts preserve objective text, steps, runtime, repository, `task.git.branch`, `task.publish.mode`, dependencies, attachments, authored presets, applied templates, and Jira provenance (FR-002, FR-005, FR-007, FR-008, FR-009, FR-010, FR-011, SCN-004, SCN-005, SC-003, SC-004, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T026 Update backend normalization in `api_service/api/routers/executions.py` only if T012-T014 prove the existing backend boundary fails MM-641 preservation or canonical branch requirements (FR-007, FR-008, FR-011, SC-003, SC-004, DESIGN-REQ-004, DESIGN-REQ-005)

### Story Validation

- [X] T027 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and fix failures in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, or `frontend/src/styles/mission-control.css` until the one-story frontend unit suite passes (FR-001 through FR-011, SCN-001 through SCN-005, SC-001 through SC-004, DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T028 Run `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q --tb=short` and `pytest tests/integration/api/test_task_contract_normalization.py -m integration_ci -q --tb=short`; fix failures in `api_service/api/routers/executions.py` or integration tests only within MM-641 scope (FR-007, FR-008, FR-011, SC-003, SC-004, DESIGN-REQ-004, DESIGN-REQ-005)
- [X] T029 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` to verify the focused frontend path through the repository unit runner (FR-001 through FR-011, SC-001 through SC-004)

**Checkpoint**: The single story is functional, covered by unit and integration evidence, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T030 [P] Review `docs/UI/CreatePage.md` and `docs/Tasks/TaskArchitecture.md` for drift against the implemented MM-641 behavior; update only if the canonical desired-state docs conflict with the completed runtime behavior (FR-012, SC-005, DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T031 [P] Review `specs/340-create-page-authoring-validation/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/create-page-authoring-validation.md`, `quickstart.md`, and `tasks.md` to ensure `MM-641`, the original Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-005 and original Jira coverage ID DESIGN-REQ-007 remain traceable (FR-012, SC-005)
- [X] T032 Run `./tools/test_unit.sh` for final unit verification; if the full suite is too large or blocked, record the exact blocker and preserve focused unit evidence from T027 and T029 (FR-001 through FR-012)
- [X] T033 Run `./tools/test_integration.sh` for hermetic integration verification; if Docker or compose is unavailable in the managed agent environment, record the exact blocker and preserve targeted integration evidence from T028 (FR-003, FR-007, FR-008, FR-011, SC-002, SC-003, SC-004)
- [X] T034 Run the quickstart validation steps from `specs/340-create-page-authoring-validation/quickstart.md` and record the command outcomes in final implementation notes (FR-001 through FR-012, SC-001 through SC-005)
- [ ] T035 Run `/moonspec-verify` after implementation and tests pass, validating against `specs/340-create-page-authoring-validation/spec.md`, the preserved `MM-641` Jira preset brief, plan, tasks, contracts, and test evidence (FR-012, SC-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies; can start immediately.
- **Foundational (Phase 2)**: depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: depends on Phase 2. Unit and integration tests must be authored and red-first evidence captured before production implementation.
- **Polish And Verification (Phase 4)**: depends on story implementation and focused tests passing.

### Within The Story

- T007-T010 unit tests precede T011 red-first unit confirmation.
- T012-T013 integration tests precede T014 integration confirmation.
- T015-T016 red-first guardrails precede T017-T026 production implementation.
- T017-T020 are conditional fallback tasks for implemented-unverified rows and run only if verification tests expose a gap.
- T021-T026 production tasks run after red-first confirmation.
- T027-T029 validate the story before Phase 4.
- T030-T035 run after the story is functional and focused tests pass.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T005 and T006 can run in parallel after T004 is understood.
- T007, T008, T009, and T010 can be authored in parallel if coordinated in `frontend/src/entrypoints/task-create.test.tsx` to avoid conflicting edits.
- T012 and T013 can run in parallel because they touch different integration test files.
- T017-T020 can run in parallel only when their triggering failures touch different files.
- T030 and T031 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Independent integration test authoring:
Task: "Add/extend temporal task-shaped submission integration test in tests/integration/temporal/test_task_shaped_submission_normalization.py"
Task: "Add/extend API task contract normalization integration test in tests/integration/api/test_task_contract_normalization.py"

# Independent polish checks:
Task: "Review docs/UI/CreatePage.md and docs/Tasks/TaskArchitecture.md for drift"
Task: "Review feature artifacts for MM-641 traceability"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational fixture tasks.
2. Write frontend unit tests for Steps-card placement, invalid draft blocking, combined valid submission, and canonical branch preservation.
3. Write or extend integration tests for execution-visible payload and task input snapshot boundaries.
4. Run focused unit and integration tests to capture red-first failures or verification-only passes before production changes.
5. Move Repository, Branch, and Publish Mode into the Steps card while preserving existing state, labels, branch lookup, validation, and payload semantics.
6. Apply conditional backend or metadata preservation fixes only if the red-first/verification tests expose a gap.
7. Rerun focused unit and integration tests, then repository unit/integration runners as feasible.
8. Preserve `MM-641`, the original Jira preset brief, and source design mappings, including original Jira coverage ID DESIGN-REQ-007, through final `/moonspec-verify`.

### Code-And-Test Work

- Code and tests required: FR-002, FR-003, FR-004, FR-006, FR-007, FR-009, SCN-001, SCN-002, SCN-004, SC-001, SC-002, SC-004, DESIGN-REQ-002, DESIGN-REQ-003.
- Verification tests plus conditional fallback implementation: FR-001, FR-005, FR-010, FR-011, SCN-005, SC-005, DESIGN-REQ-001, DESIGN-REQ-005, DESIGN-REQ-007.
- Already verified, preserve in final validation: FR-008, FR-012, SCN-003, SC-003, DESIGN-REQ-004.

---

## Notes

- This task list covers one story only: MM-641 Create page authoring and validation.
- Do not generate additional specs or run MoonSpec planning/specification from this task list.
- Do not change Publish Mode semantics or reintroduce `targetBranch`.
- Do not add persistent storage.
- Browser clients must continue using MoonMind APIs rather than direct Jira, GitHub, object storage, Temporal, or provider APIs.
- Tests must be written and red-first evidence captured before production implementation tasks.

## Implementation Evidence

- Red-first frontend evidence: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` failed before production edits with MM-641 placement assertions showing Repository, Branch, and Publish Mode were not inside `[data-canonical-create-section="Steps"]`.
- Backend integration evidence before production edits: `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q --tb=short` passed after assertion correction as verification-only boundary evidence; `pytest tests/integration/api/test_task_contract_normalization.py -m integration_ci -q --tb=short` passed after assertion correction as verification-only canonical branch evidence.
- Production implementation: `frontend/src/entrypoints/task-create.tsx` moves Repository, Branch, and Publish Mode into the Steps card, leaves the submit rail as the submit action container, and preserves existing state, validation, branch lookup, `task.git.branch`, and publish payload semantics. `frontend/src/styles/mission-control.css` adds responsive Steps-card control layout.
- Focused frontend validation: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx` passed with 38 active tests and 228 skipped tests. `npm run ui:test:task-create` also passed with the same result. `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` was blocked by the npm shell not resolving `vitest`; direct local binary and the repository unit wrapper succeeded.
- Repository unit wrapper: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` passed with 4874 Python tests, 1 xpassed test, 16 subtests, and the focused Vitest file passing.
- Targeted integration validation after implementation: `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q --tb=short` passed with 9 tests; `pytest tests/integration/api/test_task_contract_normalization.py -m integration_ci -q --tb=short` passed with 7 tests.
- Full integration runner: `./tools/test_integration.sh` was attempted and blocked by Docker administrative rules while building the pytest image: `403 Forbidden`. Targeted hermetic integration evidence is preserved above.
- Docs and traceability review: `docs/Tasks/TaskArchitecture.md` already states Repository, Branch, and Publish Mode render together in the Steps card and that `Publish Mode` remains submission data; no canonical docs update was needed. Feature artifacts still preserve `MM-641`, the original Jira preset brief, DESIGN-REQ-001 through DESIGN-REQ-005, and original Jira coverage ID DESIGN-REQ-007.
- Final `/moonspec-verify` remains unchecked because this managed step is limited to implementation work; run the MoonSpec verify step next.
