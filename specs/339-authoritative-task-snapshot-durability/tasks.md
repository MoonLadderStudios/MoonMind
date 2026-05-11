# Tasks: Authoritative Task Snapshot Durability

**Input**: Design documents from `/work/agent_jobs/mm:e2bc69b6-9268-42f5-8b6d-deec1caaeb08/repo/specs/339-authoritative-task-snapshot-durability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/task-input-snapshot-reconstruction.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: durable authoritative task input snapshots for edit, exact full rerun, edited full retry, and Resume.

**Source Traceability**: Original Jira issue `MM-639` and the canonical Jira preset brief are preserved in `spec.md`. This task list covers FR-001 through FR-009, acceptance scenarios 1 through 7, SC-001 through SC-006, and DESIGN-REQ-004/DESIGN-REQ-011/DESIGN-REQ-012/DESIGN-REQ-013. Requirement status summary from `plan.md`: 8 rows `partial`, 12 rows `implemented_unverified`, and 6 rows `implemented_verified`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/tasks/test_task_contract.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `./tools/test_integration.sh`; focused iteration may use `pytest tests/contract/test_temporal_execution_api.py -q`, `pytest tests/integration/temporal/test_full_retry_recovery_actions.py -q`, and `pytest tests/integration/api/test_task_contract_normalization.py -q`
- Final verification: `/moonspec-verify` (`/speckit.verify` user-facing equivalent)

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active one-story MoonSpec artifacts and local test surfaces before writing tests.

- [X] T001 Confirm `specs/339-authoritative-task-snapshot-durability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-input-snapshot-reconstruction.md`, and `quickstart.md` are present and preserve `MM-639`, FR-001 through FR-009, SC-001 through SC-006, and DESIGN-REQ-004/DESIGN-REQ-011/DESIGN-REQ-012/DESIGN-REQ-013.
- [X] T002 Inspect existing snapshot and recovery implementation paths in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/worker_runtime.py`, `moonmind/workflows/tasks/task_contract.py`, and `frontend/src/lib/temporalTaskEditing.ts` for the current code baseline before adding tests.
- [X] T003 Confirm local test commands from `specs/339-authoritative-task-snapshot-durability/quickstart.md` are available, including `./tools/test_unit.sh`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`, and `./tools/test_integration.sh`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared fixtures and traceability before story test and implementation work begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Create or update reusable task snapshot fixture helpers for authored fields, attachment targets, preset provenance, branch, publish, runtime, dependencies, and final order in `tests/unit/api/routers/test_executions.py` for FR-002, DESIGN-REQ-011, and SCN-001.
- [ ] T005 [P] Create or update reusable Resume checkpoint and recovery fixtures in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-004, FR-006, DESIGN-REQ-012, and DESIGN-REQ-013.
- [X] T006 [P] Create or update frontend snapshot artifact fixtures in `frontend/src/entrypoints/task-create.test.tsx` for FR-003, FR-007, SCN-002, SCN-003, and SC-003.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Durable Task Input Snapshot

**Summary**: As a platform owner, I want every submitted task to preserve an authoritative task input snapshot so that edit, exact full rerun, edited full retry, and Resume can reconstruct the authored task without relying on mutable catalogs or lossy projections.

**Independent Test**: Submit or simulate tasks with objective text, ordered steps, attachments, runtime/publish selections, repository/branch choices, preset metadata, provenance, and dependencies; then verify edit, exact full rerun, edited full retry, and Resume reconstruct or reuse original authored input according to distinct recovery intent.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SCN-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013

**Test Plan**:

- Unit: snapshot payload construction, descriptor/degraded state, action gating, edited retry snapshot creation, Resume immutability, frontend snapshot reconstruction, attachment binding failure modes.
- Integration: persisted snapshot artifact content, catalog independence, attachment-aware degraded behavior, exact full rerun without Resume progress, edited full retry new snapshot, Resume source snapshot identity.

### Unit Tests (write first) ⚠️

> NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass. For `implemented_unverified` rows, these are verification tests; if they pass immediately, skip the matching conditional fallback implementation task.

- [X] T007 Add failing unit tests for complete authoritative snapshot payload fields in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-002, SCN-001, SC-001, DESIGN-REQ-011, and MM-639 traceability.
- [X] T008 Add failing unit tests for attachment-aware missing or unreconstructible snapshot degraded descriptors and disabled recovery reasons in `tests/unit/api/routers/test_executions.py` covering FR-007, FR-008, SCN-007, SC-005, and DESIGN-REQ-004.
- [ ] T009 Add failing unit tests proving edited full retry creates a new snapshot while the source execution snapshot/evidence remains unchanged in `tests/unit/api/routers/test_executions.py` covering FR-005, SCN-005, SC-004, and DESIGN-REQ-012.
- [ ] T010 [P] Add failing unit tests proving exact full rerun and failed-step Resume preserve distinct recovery intent and unchanged source snapshot identity in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-004, FR-006, SCN-004, SCN-006, DESIGN-REQ-012, and DESIGN-REQ-013.
- [X] T011 [P] Add failing unit tests for Jira-Orchestrate child run snapshot completeness in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` covering FR-001, FR-002, SC-001, DESIGN-REQ-011, and source kind `create`.
- [X] T012 [P] Add failing frontend unit tests for snapshot-first reconstruction when live preset/template data changes, plus attachment binding rejection when compact refs cannot be matched, in `frontend/src/entrypoints/task-create.test.tsx` covering FR-003, FR-007, SCN-002, SCN-003, SC-002, SC-003, and DESIGN-REQ-004.
- [ ] T013 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/tasks/test_task_contract.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` to confirm T007-T012 fail for the expected red-first reasons.

### Integration Tests (write first) ⚠️

- [X] T014 Add failing contract test for persisted task input snapshot artifact content in `tests/contract/test_temporal_execution_api.py` covering FR-001, FR-002, FR-008, SCN-001, SC-001, DESIGN-REQ-011, and `contracts/task-input-snapshot-reconstruction.md`.
- [ ] T015 [P] Add failing integration test for exact full rerun, edited full retry, and Resume recovery intent separation in `tests/integration/temporal/test_full_retry_recovery_actions.py` covering FR-004, FR-005, FR-006, SCN-004, SCN-005, SCN-006, SC-004, DESIGN-REQ-012, and DESIGN-REQ-013.
- [ ] T016 [P] Add failing integration test for attachment-aware missing snapshot degraded behavior and no silent attachment loss in `tests/integration/api/test_task_input_snapshot_durability.py` covering FR-007, FR-008, SCN-002, SCN-007, SC-003, SC-005, and DESIGN-REQ-004.
- [ ] T017 Add failing contract test proving already submitted preset-derived tasks reconstruct without live preset catalog lookup in `tests/contract/test_temporal_execution_api.py` covering FR-003, SCN-003, SC-002, and DESIGN-REQ-011.
- [ ] T018 Run focused integration commands `pytest tests/contract/test_temporal_execution_api.py -q`, `pytest tests/integration/temporal/test_full_retry_recovery_actions.py -q`, and `pytest tests/integration/api/test_task_contract_normalization.py -q` to confirm T014-T017 fail for the expected red-first reasons.

### Red-First Confirmation ⚠️

- [ ] T019 Record red-first evidence for T007-T018 in `specs/339-authoritative-task-snapshot-durability/tasks.md` before changing production code, including which tests failed because behavior was missing versus already passed for `implemented_unverified` rows.

### Conditional Fallback Implementation

> Execute these tasks only for verification tests that fail. If an `implemented_unverified` verification test passes, mark the matching fallback task skipped with evidence in `specs/339-authoritative-task-snapshot-durability/tasks.md` and preserve the traceability in final verification.

- [X] T020 Update authoritative snapshot payload construction and metadata in `api_service/api/routers/executions.py` so snapshots explicitly preserve every FR-002 field, dependency declaration, final order, preset metadata, pinned bindings, include-tree summary, per-step provenance, detachment state, and attachment refs required by DESIGN-REQ-011.
- [X] T021 Update Jira-Orchestrate child-run snapshot persistence in `moonmind/workflows/temporal/worker_runtime.py` when T011 proves child snapshots lack required fields, ensuring worker-created child runs persist the same required authored snapshot fields as API-created runs for FR-001, FR-002, and SC-001.
- [X] T022 Update execution snapshot descriptor and action gating in `api_service/api/routers/executions.py` and any related Pydantic schema in `moonmind/schemas/temporal_models.py` so attachment-aware missing or unreconstructible snapshots produce explicit degraded state and disabled reasons for FR-007 and FR-008.
- [ ] T023 Conditional on T009/T015 failure: update edit/rerun update handling in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` so edited full retry creates a new snapshot and never mutates source snapshot, step evidence, artifacts, or checkpoints for FR-005 and SCN-005.
- [ ] T024 Conditional on T010/T015 failure: update failed-step Resume validation in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, and `moonmind/workflows/tasks/task_contract.py` so Resume reuses the original snapshot unchanged, rejects task input edits, and validates checkpoint snapshot identity for FR-006 and DESIGN-REQ-013.
- [X] T025 Update snapshot-first draft reconstruction and attachment binding validation in `frontend/src/lib/temporalTaskEditing.ts` and `frontend/src/entrypoints/task-create.tsx` so edit/rerun flows do not depend on live preset catalog state and cannot silently drop or retarget attachments for FR-003, FR-007, SC-002, and SC-003.

### Story Validation

- [ ] T026 Run focused unit validation `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/tasks/test_task_contract.py` and confirm FR-001 through FR-008 unit coverage passes.
- [ ] T027 Run focused frontend validation `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm snapshot-first reconstruction, live-catalog independence, and degraded attachment binding coverage passes for FR-003, FR-007, SCN-002, and SCN-003.
- [ ] T028 Run focused integration validation `pytest tests/contract/test_temporal_execution_api.py -q`, `pytest tests/integration/temporal/test_full_retry_recovery_actions.py -q`, and `pytest tests/integration/api/test_task_contract_normalization.py -q` and confirm SCN-001 through SCN-007 and SC-001 through SC-005 pass.
- [ ] T029 Confirm `specs/339-authoritative-task-snapshot-durability/spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `contracts/task-input-snapshot-reconstruction.md`, and `quickstart.md` preserve `MM-639`, the canonical Jira preset brief, and DESIGN-REQ-004/DESIGN-REQ-011/DESIGN-REQ-012/DESIGN-REQ-013 for FR-009 and SC-006.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [ ] T030 [P] Refactor any duplicated snapshot fixture or assertion helpers introduced during implementation in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, and `frontend/src/entrypoints/task-create.test.tsx` while preserving MM-639 traceability.
- [ ] T031 [P] Review security and secret hygiene for snapshot artifacts and test fixtures in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `tests/contract/test_temporal_execution_api.py`, ensuring no raw credentials, binary payloads, presigned URLs, or secret-like values are written to snapshots for Constitution security constraints.
- [ ] T032 Run quickstart validation from `specs/339-authoritative-task-snapshot-durability/quickstart.md` and record any blocked commands or failures in `specs/339-authoritative-task-snapshot-durability/tasks.md`.
- [ ] T033 Run full unit suite `./tools/test_unit.sh` after focused validation passes and address MM-639 regressions only.
- [ ] T034 Run required hermetic integration suite `./tools/test_integration.sh` after unit validation passes and address MM-639 regressions only.
- [ ] T035 Run final `/moonspec-verify` (`/speckit.verify`) against `specs/339-authoritative-task-snapshot-durability/spec.md` and preserve the final verification report with coverage for MM-639, FR-001 through FR-009, SCN-001 through SCN-007, SC-001 through SC-006, and DESIGN-REQ-004/DESIGN-REQ-011/DESIGN-REQ-012/DESIGN-REQ-013.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish & Verification (Phase 4)**: Depends on Story validation passing.

### Within The Story

- Unit tests T007-T012 must be written before any production implementation task T020-T025.
- Integration tests T014-T017 must be written before any production implementation task T020-T025.
- Red-first confirmation T019 must complete before production implementation tasks T020-T025.
- Conditional fallback implementation T020-T025 runs only for failed verification tests.
- Story validation T026-T029 runs after conditional implementation tasks are complete or explicitly skipped with passing verification evidence.
- Final verification T035 runs only after focused tests, full unit tests, and required integration tests pass or are blocked with exact reasons.

### Parallel Opportunities

- T005 and T006 can run in parallel after T004 because they touch different test fixture files.
- T010, T011, and T012 can run in parallel with T007-T009 after foundational setup because they touch different files.
- T015 and T016 can run in parallel with T014/T017 because they target different integration files.
- T020, T021, T022, T023, T024, and T025 may be split across workers only if the failed tests prove disjoint write scopes; otherwise apply in dependency order to avoid conflicting changes in `api_service/api/routers/executions.py`.
- T030 and T031 can run in parallel after story validation because they are polish checks with distinct concerns.

---

## Parallel Example: Story Phase

```bash
# Launch independent test authoring together after Phase 2:
Task: "T010 add service recovery intent tests in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "T011 add worker child snapshot tests in tests/unit/workflows/temporal/test_temporal_worker_runtime.py"
Task: "T012 add frontend reconstruction tests in frontend/src/entrypoints/task-create.test.tsx"

# Launch independent integration test authoring together:
Task: "T015 add recovery intent integration tests in tests/integration/temporal/test_full_retry_recovery_actions.py"
Task: "T016 add degraded attachment-aware snapshot tests in tests/integration/api/test_task_input_snapshot_durability.py"
```

---

## Implementation Strategy

### Requirement Status Handling

- **Code + tests required (`partial`)**: FR-002, FR-007, SCN-002, SCN-007, SC-003, SC-005, DESIGN-REQ-004, DESIGN-REQ-011. These receive failing tests plus required implementation work in T020, T022, and T025.
- **Verification first with conditional fallback (`implemented_unverified`)**: FR-001, FR-003, FR-005, FR-008, SCN-001, SCN-003, SCN-005, SC-001, SC-002, SC-004, DESIGN-REQ-012, DESIGN-REQ-013. These receive verification tests first; fallback implementation tasks run only if tests fail.
- **Already verified, preserve evidence (`implemented_verified`)**: FR-004, FR-006, FR-009, SCN-004, SCN-006, SC-006. These do not receive new implementation work by default, but T010/T015/T029/T035 preserve final evidence and guard against regressions.

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 setup/fixtures.
2. Write unit tests T007-T012 and integration tests T014-T017.
3. Run T013 and T018 to confirm red-first behavior or passing verification evidence for `implemented_unverified` rows.
4. Execute only the fallback implementation tasks T020-T025 whose corresponding tests fail.
5. Run focused validation T026-T029.
6. Run quickstart, full unit tests, required integration tests, and final `/moonspec-verify` in T032-T035.

---

## Notes

- This task list covers one story only: MM-639 authoritative task input snapshot durability.
- Do not generate broad refactors, new persistent tables, provider verification tests, Jira transitions, commits, or PRs from this task list unless a later step explicitly asks for them.
- Preserve unrelated dirty work, including existing `.gemini/skills` changes.
- Keep large/binary attachment content out of workflow history and snapshots; snapshots should contain refs and target metadata only.

## Implementation Evidence - 2026-05-11

- Red-first evidence: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py -k 'original_task_input_snapshot_payload_preserves_mm639_authored_fields or missing_attachment_aware_snapshot_descriptor_is_degraded_explicitly'` failed before production changes because `authoredTaskInput` was missing and attachment-aware missing snapshots were not exposed by the new test; `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_worker_runtime.py -k 'child_jira_orchestrate_run_persists_original_task_input_snapshot'` failed before production changes because child snapshots lacked `authoredTaskInput`.
- Passing focused evidence after implementation: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py -k 'original_task_input_snapshot_payload_preserves_mm639_authored_fields or missing_attachment_aware_snapshot_descriptor_is_degraded_explicitly'`, `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_worker_runtime.py -k 'child_jira_orchestrate_run_persists_original_task_input_snapshot'`, and `pytest tests/contract/test_temporal_execution_api.py -q -k 'task_shaped_create_returns_temporal_identity_and_redirect'`.
- Additional validation: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py`, `./tools/test_unit.sh tests/unit/api/routers/test_executions.py -k 'snapshot or rerun or resume'`, and `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py -k 'request_rerun_creates_fresh_execution_for_terminal_execution or failed_step_resume'` passed. The repository UI runner invoked by `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` reported the frontend suite passing; direct `npm run ui:test -- ...` from the repo root failed with `vitest: not found`, so the repo wrapper remains the verified UI path for this workspace.
- Full unit note: a full `./tools/test_unit.sh` run after the focused fixes completed 4862 tests but failed unrelated `tests/unit/services/temporal/runtime/test_supervisor.py::test_stalled_progress_does_not_override_clean_exit_without_termination`; no files in that runtime supervisor area were changed for MM-639.
- Scope note: This implement step completed the authoritative snapshot field/degraded descriptor/frontend binding slice. Broader remaining tasks such as T009/T010/T015/T016/T017/T018/T026-T035 are left unchecked unless covered by the passing evidence above, and final `/moonspec-verify` was not run in this managed implementation step.
