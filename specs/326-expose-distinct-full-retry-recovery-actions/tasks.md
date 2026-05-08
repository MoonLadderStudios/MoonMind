# Tasks: Expose Distinct Full Retry Recovery Actions

**Input**: Design documents from `specs/326-expose-distinct-full-retry-recovery-actions/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/recovery-actions.md](contracts/recovery-actions.md), [quickstart.md](quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: `Explicit Full-Task Recovery Choices`.

**Source Traceability**: MM-632, original Jira preset brief preserved in `spec.md`, FR-001 through FR-013, acceptance scenarios 1 through 7, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-007, and source coverage IDs DESIGN-REQ-012 / DESIGN-REQ-014 from the Jira brief.

**Requirement Status Summary From plan.md**: 0 missing, 13 partial, 19 implemented_unverified, 2 implemented_verified.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py`
- Frontend unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and does not depend on incomplete work
- Every task includes exact file paths and requirement, scenario, success criterion, or source IDs when applicable

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active MoonSpec artifacts and relevant code surfaces before authoring tests.

- [ ] T001 Confirm `specs/326-expose-distinct-full-retry-recovery-actions/spec.md`, `specs/326-expose-distinct-full-retry-recovery-actions/plan.md`, `specs/326-expose-distinct-full-retry-recovery-actions/research.md`, `specs/326-expose-distinct-full-retry-recovery-actions/data-model.md`, `specs/326-expose-distinct-full-retry-recovery-actions/contracts/recovery-actions.md`, and `specs/326-expose-distinct-full-retry-recovery-actions/quickstart.md` are present and still describe exactly one story for MM-632.
- [ ] T002 [P] Review current action capability serialization and rerun/resume routes in `api_service/api/routers/executions.py` for FR-001, FR-004, FR-008, FR-010, FR-012, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T003 [P] Review current Temporal rerun service behavior in `moonmind/workflows/temporal/service.py` for FR-004, FR-005, FR-006, FR-011, FR-012, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T004 [P] Review current Task Detail and Create page recovery flows in `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-create.tsx`, and `frontend/src/lib/temporalTaskEditing.ts` for FR-001, FR-002, FR-003, FR-004, FR-008, and SCN-001 through SCN-004.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add or adjust reusable test fixtures only. No production recovery behavior changes begin until this phase is complete.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T005 Add shared failed-execution fixture helpers for action capability, original snapshot, and resume checkpoint permutations in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-008, FR-010, SCN-001, SCN-006, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T006 [P] Add or refine Task Detail recovery action fixture builders in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-008, SCN-001, and SC-001.
- [ ] T007 [P] Add or refine Create page rerun/edit-for-rerun fixture builders in `frontend/src/entrypoints/task-create.test.tsx` covering FR-002, FR-003, FR-004, SCN-002, SCN-004, and DESIGN-REQ-003.
- [ ] T008 [P] Create hermetic integration test scaffolding for failed task recovery in `tests/integration/temporal/test_full_retry_recovery_actions.py` covering SCN-001 through SCN-007, DESIGN-REQ-001 through DESIGN-REQ-007, and `contracts/recovery-actions.md`.

**Checkpoint**: Fixture and integration scaffolding are ready; story tests can now be written.

---

## Phase 3: Story - Explicit Full-Task Recovery Choices

**Summary**: As a user recovering from a failed task, I want Edit task, Rerun, and Resume to appear as separate recovery choices only when each choice is actually available so that I can intentionally change the task, retry it exactly, or preserve completed progress without ambiguity.

**Independent Test**: Exercise failed task details across recovery capability combinations, then perform Edit task, exact Rerun, and Resume-unavailable flows; verify visible actions match capability state, Edit task allows edits from the authoritative snapshot, exact Rerun uses original input unchanged, full retry paths import no Resume progress, invalid Resume evidence fails visibly, and the failed source execution remains immutable.

**Traceability**: FR-001 through FR-013; SCN-001 through SCN-007; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-007; source coverage IDs DESIGN-REQ-012 and DESIGN-REQ-014.

**Unit Test Plan**:

- API route and model tests for action capability matrix, exact Rerun mutation rejection, Resume unavailable reasons, and source immutability.
- Temporal service tests for exact Rerun original-input preservation, no Resume progress import, and edited full retry snapshot provenance.
- Frontend tests for Task Detail action visibility and Create page edit-for-rerun versus exact Rerun behavior.

**Integration Test Plan**:

- Hermetic failed `MoonMind.Run` fixture with original task snapshot, optional Resume checkpoint, failed terminal state, and source step evidence.
- End-to-end route/service assertions for action availability, edited full retry, exact Rerun, invalid Resume evidence, and source immutability.

### Unit Tests (write first)

- [ ] T009 [P] Add failing API unit tests for the full recovery action capability matrix and disabled reasons in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-008, SCN-001, SC-001, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T010 [P] Add failing API unit tests requiring exact Rerun to reject or omit task/input mutation fields in `tests/unit/api/routers/test_executions.py` covering FR-004, FR-006, FR-012, SCN-004, SC-003, SC-004, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T011 [P] Add failing API unit tests for Resume unavailable reasons across missing, stale, unauthorized, and inconsistent checkpoint evidence in `tests/unit/api/routers/test_executions.py` covering FR-010, SCN-006, SC-005, and DESIGN-REQ-002.
- [ ] T012 [P] Add failing Temporal service unit tests proving exact Rerun preserves original task input unchanged and imports no `resumeSource`, `resumeCheckpointRef`, preserved steps, or completed progress in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-004, FR-005, FR-006, SCN-004, SCN-005, SC-003, SC-004, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T013 [P] Add failing Temporal service unit tests proving edited full retry creates a distinct authoritative snapshot and preserves failed source state in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-002, FR-003, FR-007, FR-011, SCN-002, SCN-003, SCN-007, SC-002, SC-006, DESIGN-REQ-003, and DESIGN-REQ-006.
- [ ] T014 [P] Add failing Task Detail UI tests for independent Edit task, Rerun, and Resume visibility plus disabled reason rendering in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-008, FR-010, SCN-001, SCN-006, SC-001, SC-005, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T015 [P] Add failing Create page UI tests proving edit-for-rerun permits authoring edits while exact Rerun cannot submit edited task/input mutation fields in `frontend/src/entrypoints/task-create.test.tsx` covering FR-002, FR-003, FR-004, FR-006, FR-012, SCN-002, SCN-004, SC-002, SC-003, SC-004, DESIGN-REQ-003, DESIGN-REQ-004, and DESIGN-REQ-007.

### Integration Tests (write first)

- [ ] T016 Add failing hermetic integration test for failed execution detail action capabilities in `tests/integration/temporal/test_full_retry_recovery_actions.py` covering FR-001, FR-008, SCN-001, SC-001, DESIGN-REQ-001, and `contracts/recovery-actions.md`.
- [ ] T017 Add failing hermetic integration test for edited full retry creating a new from-beginning execution with a new snapshot and no imported progress in `tests/integration/temporal/test_full_retry_recovery_actions.py` covering FR-002, FR-003, FR-006, FR-007, FR-011, SCN-002, SCN-003, SCN-005, SCN-007, SC-002, SC-004, SC-006, DESIGN-REQ-003, DESIGN-REQ-006, and DESIGN-REQ-007.
- [ ] T018 Add failing hermetic integration test for exact Rerun preserving original input unchanged and omitting Resume progress fields in `tests/integration/temporal/test_full_retry_recovery_actions.py` covering FR-004, FR-005, FR-006, FR-012, SCN-004, SCN-005, SC-003, SC-004, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T019 Add failing hermetic integration test for invalid Resume evidence returning operator-readable unavailable reasons without disabling full retry choices in `tests/integration/temporal/test_full_retry_recovery_actions.py` covering FR-001, FR-010, FR-012, SCN-006, SC-005, DESIGN-REQ-002, and DESIGN-REQ-005.

### Red-First Confirmation

- [ ] T020 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and confirm T009, T010, and T011 fail for the expected MM-632 reasons before production changes.
- [ ] T021 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py` and confirm T012 and T013 fail for the expected MM-632 reasons before production changes.
- [ ] T022 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T014 fails for the expected MM-632 reasons before production changes.
- [ ] T023 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T015 fails for the expected MM-632 reasons before production changes.
- [ ] T024 Run `./tools/test_integration.sh` or the narrow integration command for `tests/integration/temporal/test_full_retry_recovery_actions.py` and confirm T016 through T019 fail for the expected MM-632 reasons before production changes.

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [ ] T025 If T009, T014, or T016 expose gaps in distinct action capability rendering, update `api_service/api/routers/executions.py` and `frontend/src/entrypoints/task-detail.tsx` to preserve independent Edit task, Rerun, and Resume visibility for FR-001, FR-008, SCN-001, SC-001, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T026 If T013, T015, or T017 expose gaps in edited full retry snapshot reconstruction, update `frontend/src/entrypoints/task-create.tsx`, `frontend/src/lib/temporalTaskEditing.ts`, and `api_service/api/routers/executions.py` for FR-002, FR-003, FR-007, SCN-002, SCN-003, SC-002, DESIGN-REQ-003, and DESIGN-REQ-006.
- [ ] T027 If T013, T017, or T019 expose gaps in failed source immutability, update `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` for FR-011, SCN-007, SC-006, DESIGN-REQ-003, and DESIGN-REQ-007.
- [ ] T028 Preserve existing Resume edited-payload rejection behavior in `api_service/api/routers/executions.py` and `tests/unit/api/routers/test_executions.py` for already-verified FR-009 and DESIGN-REQ-005 while making adjacent exact Rerun changes.

### Implementation

- [ ] T029 Update exact Rerun request validation in `api_service/api/routers/executions.py` so exact Rerun rejects or omits task/input mutation fields, artifact overrides, and Resume checkpoint fields for FR-004, FR-006, FR-012, SCN-004, SCN-005, SC-003, SC-004, DESIGN-REQ-004, DESIGN-REQ-007, and `contracts/recovery-actions.md`.
- [ ] T030 Update manual rerun behavior in `moonmind/workflows/temporal/service.py` so exact Rerun reuses original task input unchanged and does not carry `resumeSource`, `resumeCheckpointRef`, preserved steps, completed prior progress, or edited parameter patches for FR-004, FR-005, FR-006, FR-012, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T031 Update Temporal request or response models in `moonmind/schemas/temporal_models.py` only if T029 or T030 requires a stricter exact Rerun contract shape, preserving in-flight compatibility for existing worker-bound payloads for FR-004, FR-012, and DESIGN-REQ-004.
- [ ] T032 Update exact Rerun UI submission behavior in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/lib/temporalTaskEditing.ts` so exact Rerun cannot submit edited task/input mutation fields and Edit task remains the explicit mutation path for FR-002, FR-003, FR-004, FR-006, FR-012, SCN-002, SCN-004, DESIGN-REQ-003, DESIGN-REQ-004, and DESIGN-REQ-007.
- [ ] T033 Update Task Detail disabled-reason and action rendering in `frontend/src/entrypoints/task-detail.tsx` if T014 shows missing independent visibility or unreadable unavailable reasons for FR-001, FR-008, FR-010, SCN-001, SCN-006, SC-001, SC-005, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T034 Update Resume checkpoint validation and unavailable reason mapping in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` for stale, unauthorized, or inconsistent checkpoint evidence for FR-010, FR-012, SCN-006, SC-005, DESIGN-REQ-002, and DESIGN-REQ-005.
- [ ] T035 Update source immutability safeguards in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` so Edit task, exact Rerun, and Resume attempts preserve failed source state, snapshot, step ledger refs, artifact refs, and checkpoint refs for FR-011, SCN-007, SC-006, DESIGN-REQ-003, and DESIGN-REQ-007.

### Story Validation

- [ ] T036 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py` and fix failures in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, or `moonmind/schemas/temporal_models.py` until the MM-632 Python unit tests pass.
- [ ] T037 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix failures in `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-create.tsx`, or `frontend/src/lib/temporalTaskEditing.ts` until the MM-632 UI tests pass.
- [ ] T038 Run `./tools/test_integration.sh` and fix failures in `tests/integration/temporal/test_full_retry_recovery_actions.py`, `api_service/api/routers/executions.py`, or `moonmind/workflows/temporal/service.py` until the MM-632 hermetic integration tests pass or document a concrete local Docker/socket blocker in `specs/326-expose-distinct-full-retry-recovery-actions/quickstart.md`.
- [ ] T039 Validate story coverage by tracing FR-001 through FR-013, SCN-001 through SCN-007, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-007, and source coverage IDs DESIGN-REQ-012 / DESIGN-REQ-014 across `specs/326-expose-distinct-full-retry-recovery-actions/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/recovery-actions.md`, `quickstart.md`, and `tasks.md`.

**Checkpoint**: The single story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T040 [P] Update `specs/326-expose-distinct-full-retry-recovery-actions/quickstart.md` if final commands, blockers, or validation steps changed during implementation for FR-013 and SC-007.
- [ ] T041 [P] Review `specs/326-expose-distinct-full-retry-recovery-actions/contracts/recovery-actions.md` against final behavior and update only if the public recovery action contract changed intentionally for FR-001 through FR-013.
- [ ] T042 Run full required unit verification with `./tools/test_unit.sh` after focused tests pass and record any unresolved blocker in `specs/326-expose-distinct-full-retry-recovery-actions/quickstart.md`.
- [ ] T043 Run hermetic integration verification with `./tools/test_integration.sh` after focused integration tests pass, or record a concrete Docker/socket blocker in `specs/326-expose-distinct-full-retry-recovery-actions/quickstart.md`.
- [ ] T044 Run `/speckit.verify` after implementation and tests pass, validating the completed implementation against MM-632, the original Jira preset brief, `spec.md`, `plan.md`, `tasks.md`, required unit tests, required integration tests, and source coverage IDs DESIGN-REQ-012 / DESIGN-REQ-014.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies; can start immediately.
- **Phase 2 Foundational**: Depends on Phase 1; blocks story test and implementation work.
- **Phase 3 Story**: Depends on Phase 2. Unit and integration tests must be written and red-first confirmation tasks must complete before implementation.
- **Phase 4 Polish & Verification**: Depends on story implementation and focused test validation.

### Within The Story

- T009 through T015 unit tests and T016 through T019 integration tests must be written before implementation tasks.
- T020 through T024 red-first confirmations must complete before T025 through T035 production changes.
- T025 through T028 are conditional fallback implementation tasks for implemented_unverified rows and should be skipped when verification tests already pass.
- T029 through T035 complete partial requirements and contract gaps.
- T036 through T039 validate the story before Phase 4.
- T044 is the final `/speckit.verify` task and must run after implementation and tests pass.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel.
- T006, T007, and T008 can run in parallel after T005 if fixture conventions are clear.
- T009 through T015 can be authored in parallel because they touch different test files or independent sections.
- T016 through T019 are in one integration file and should be coordinated sequentially or by one owner.
- T025 through T028 can run in parallel with implementation tasks only after red-first confirmation, but each must coordinate around shared files.
- T040 and T041 can run in parallel after story validation.

---

## Parallel Example: Story Test Authoring

```bash
# Independent test-authoring slices:
Task: "T009/T010/T011 API unit tests in tests/unit/api/routers/test_executions.py"
Task: "T012/T013 service unit tests in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "T014 Task Detail UI tests in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T015 Create page UI tests in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

1. Complete setup and fixture tasks T001 through T008.
2. Write unit tests T009 through T015 and integration tests T016 through T019.
3. Run red-first confirmations T020 through T024 before production changes.
4. Preserve already-verified Resume edit rejection behavior while tightening exact Rerun semantics.
5. Complete conditional fallback tasks only when verification tests expose gaps in implemented_unverified rows.
6. Complete partial requirement implementation tasks T029 through T035.
7. Run focused unit, UI, and integration validation T036 through T039.
8. Complete polish and final verification T040 through T044.

## Coverage Summary

- **Code-and-test work**: Partial rows FR-004, FR-006, FR-010, FR-012, SCN-004, SCN-005, SCN-006, SC-003, SC-004, SC-005, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-007.
- **Verification-first with conditional fallback**: Implemented-unverified rows FR-001, FR-002, FR-003, FR-005, FR-007, FR-008, FR-011, FR-013, SCN-001, SCN-002, SCN-003, SCN-007, SC-001, SC-002, SC-006, SC-007, DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-006.
- **Already verified / preserve only**: FR-009 and DESIGN-REQ-005.
- **Final traceability**: T039 and T044 preserve MM-632, the original Jira preset brief, and source coverage IDs DESIGN-REQ-012 / DESIGN-REQ-014.

## Notes

- This task list covers exactly one story.
- Unit and integration tests are mandatory and appear before implementation tasks.
- Red-first confirmation tasks T020 through T024 are required before production code changes.
- Do not create `plan.md`, `spec.md`, PRs, Jira transitions, or implementation commits from this task-generation step.
