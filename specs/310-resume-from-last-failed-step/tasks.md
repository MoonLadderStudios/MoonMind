# Tasks: Resume from Last Failed Step

**Input**: Design documents from `/work/agent_jobs/mm:222b2e78-d472-440c-8bff-8e20c3cfd8f8/repo/specs/310-resume-from-last-failed-step/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/resume-from-failed-step-api.md](contracts/resume-from-failed-step-api.md), [quickstart.md](quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: failed-step Resume for `MoonMind.Run`.

**Source Traceability**: The original `MM-602` Jira preset brief is preserved in [spec.md](spec.md) `**Input**`. Tasks cover FR-001 through FR-012, acceptance scenarios 1 through 7, SC-001 through SC-008, DESIGN-REQ-001 through DESIGN-REQ-013, edge cases, and the non-goals in the Jira brief.

**Requirement Status Summary**: From [plan.md](plan.md): 21 rows are `missing`, 10 are `partial`, 1 is `implemented_unverified`, and 1 is `implemented_verified`. Code-and-test work is required for all missing and partial resume behavior. SC-008 is verification/conditional traceability work. FR-012 is already verified by `spec.md` and remains covered by final verification only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Focused frontend tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete work.
- Every task includes exact file paths and relevant requirement, scenario, success criterion, or source IDs.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and test entrypoints before red-first work.

- [X] T001 Verify active feature pointer `.specify/feature.json` resolves to `specs/310-resume-from-last-failed-step` and that `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/resume-from-failed-step-api.md` exist for MM-602 traceability.
- [X] T002 [P] Review existing backend execution action, rerun, snapshot, and step-ledger surfaces in `api_service/api/routers/executions.py`, `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/service.py`, and `moonmind/workflows/temporal/step_ledger.py` before adding tests for FR-001 through FR-008.
- [X] T003 [P] Review existing task-detail action and intervention UI behavior in `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/entrypoints/task-detail.test.tsx` before adding tests for FR-009 and SC-001.
- [ ] T004 [P] Confirm focused test commands from `quickstart.md` are runnable in this workspace: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`, `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`, and `./tools/test_integration.sh`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the shared test fixtures and contract scaffolding that all story tests depend on.

**CRITICAL**: No production implementation work starts until Phase 2 and all red-first tests in Phase 3 are complete.

- [ ] T005 Create reusable failed `MoonMind.Run` execution fixtures with source workflow ID, source run ID, original task input snapshot ref, plan ref/digest, and failed step ledger rows in `tests/unit/api/routers/test_executions.py` for FR-001 through FR-007 and DESIGN-REQ-008.
- [ ] T006 [P] Create reusable resume checkpoint fixture payloads with valid, missing, mismatched, unauthorized, and incomplete variants in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-005, FR-006, FR-007, DESIGN-REQ-009, and DESIGN-REQ-012.
- [ ] T007 [P] Create reusable resumed step ledger fixture data with preserved prior steps and source provenance in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` for FR-008, SC-003, DESIGN-REQ-003, and DESIGN-REQ-013.
- [ ] T008 [P] Create task-detail UI fixture data for failed-step Resume capability, disabled reasons, related runs, and preserved-step display in `frontend/src/entrypoints/task-detail.test.tsx` for FR-009, SC-001, and SC-006.
- [ ] T009 [P] Create API contract test fixture payloads for `POST /api/executions/{workflow_id}/resume-from-failed-step`, execution detail action additions, and preserved-step response additions in `tests/contract/test_temporal_execution_api.py` for FR-002, FR-004, FR-005, and SC-005.

**Checkpoint**: Shared fixtures exist and story test authoring can begin.

---

## Phase 3: Story - Resume Failed Task Progress

**Summary**: As a MoonMind operator recovering a failed task, I want to resume from the last failed step while preserving completed prior work so that I do not have to rerun successful setup or implementation steps.

**Independent Test**: Open a failed task with checkpointed completed progress, choose **Resume**, confirm a linked follow-up execution starts at the failed step with prior steps shown as preserved, and verify failed or incomplete checkpoints prevent any new step execution.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012; SCN-001 through SCN-007; SC-001 through SC-008; DESIGN-REQ-001 through DESIGN-REQ-013.

**Test Plan**:

- Unit: action capability serialization, resume request validation, checkpoint model validation, source identity pinning, edited payload rejection, service fail-fast paths, preserved-step helpers, and UI rendering states.
- Integration: resume API contract, source/resumed execution linkage, checkpoint validation before execution, preserved-step materialization, related-run cross-linking, and failed-step Resume UI journey.

### Unit Tests (write first)

> Write these tests first. Run them and confirm they fail for the expected missing capability, missing route, missing model, or missing UI behavior before implementation.

- [X] T010 [P] Add failing unit tests for `canResumeFromFailedStep` serialization, eligibility, disabled reasons, and lifecycle `canResume` separation in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-009, SC-001, DESIGN-REQ-005, and DESIGN-REQ-007.
- [X] T011 [P] Add failing unit tests for resume command request validation rejecting edited task payload fields in `tests/unit/api/routers/test_executions.py` covering FR-004, SC-005, DESIGN-REQ-009, and the edited-input edge case.
- [X] T012 [P] Add failing unit tests for resume source identity pinning with missing, blank, stale, and mismatched source run IDs in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-003, FR-006, DESIGN-REQ-001, and DESIGN-REQ-008.
- [X] T013 [P] Add failing unit tests for resume checkpoint model validation in `tests/unit/workflows/temporal/test_temporal_service.py` covering required source, failed step, preserved steps, prepared refs, output refs, workspace state, FR-005, FR-006, DESIGN-REQ-011, and DESIGN-REQ-012.
- [X] T014 [P] Add failing unit tests for explicit resume validation failures before new step execution in `tests/unit/workflows/temporal/test_temporal_service.py` covering missing checkpoint, unauthorized checkpoint, plan mismatch, missing output refs, workspace restore unavailable, FR-006, FR-007, and SC-004.
- [X] T015 [P] Add failing unit tests for preserved-step materialization helpers in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-008, SC-003, DESIGN-REQ-003, DESIGN-REQ-004, and DESIGN-REQ-013.
- [X] T016 [P] Add failing frontend unit tests for failed-step Resume action rendering, accessible name, disabled reason display, confirmation copy, success feedback, and lifecycle Resume distinction in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-009, SC-001, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T017 [P] Add failing frontend unit tests for source/resumed related-run display and `Resumed from failed step` label in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-009, SC-006, DESIGN-REQ-010, and SCN-007.

### Integration and Contract Tests (write first)

- [X] T018 [P] Add failing contract tests for `POST /api/executions/{workflow_id}/resume-from-failed-step` success and error responses in `tests/contract/test_temporal_execution_api.py` covering FR-002, FR-004, FR-005, SC-002, SC-005, and `contracts/resume-from-failed-step-api.md`.
- [X] T019 [P] Add failing API integration tests for execution detail additions `actions.canResumeFromFailedStep`, `resume`, and `relatedRuns` in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-009, FR-010, SC-001, and SC-006.
- [X] T020 [P] Add failing API integration tests for `/api/executions/{workflow_id}/steps` preserved-step provenance output in `tests/unit/api/routers/test_executions.py` covering FR-008, FR-010, SC-003, and DESIGN-REQ-013.
- [X] T021 [P] Add failing Temporal service boundary tests for linked follow-up execution creation, source immutability, source run pinning, original snapshot reuse, and no edited payload in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-002, FR-003, FR-004, SC-002, and DESIGN-REQ-008.
- [X] T022 [P] Add failing workflow boundary tests for resumed `MoonMind.Run` initialization from validated checkpoint and start-at-failed-step behavior in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-008, SC-003, DESIGN-REQ-002, and DESIGN-REQ-004.
- [X] T023 [P] Add failing hermetic integration coverage for one successful failed-step Resume path and one invalid checkpoint path in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering SCN-002, SCN-003, SCN-005, FR-011, and SC-007.

### Red-First Confirmation

- [ ] T024 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and confirm T010, T011, T019, and T020 fail for missing failed-step Resume capability, route, response, and preserved-step behavior before implementation.
- [ ] T025 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py` and confirm T012, T013, T014, and T021 fail for missing resume source/checkpoint validation and linked execution behavior before implementation.
- [ ] T026 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and confirm T015 and T022 fail for missing preserved-step and start-at-failed-step workflow behavior before implementation.
- [ ] T027 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T016 and T017 fail for missing failed-step Resume UI and related-run behavior before implementation.
- [ ] T028 Run the targeted contract/integration tests for T018 and T023 and confirm they fail for the expected missing API route or workflow behavior before implementation in `tests/contract/test_temporal_execution_api.py` and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`.

### Conditional Verification-Only and Fallback Work

- [X] T029 Verify existing traceability evidence for implemented_verified FR-012 and implemented_unverified SC-008 in `specs/310-resume-from-last-failed-step/spec.md`, `specs/310-resume-from-last-failed-step/plan.md`, and `specs/310-resume-from-last-failed-step/tasks.md` before production implementation.
- [ ] T030 If T029 finds MM-602 traceability gaps, update `specs/310-resume-from-last-failed-step/plan.md` and `specs/310-resume-from-last-failed-step/tasks.md` to restore MM-602, original preset brief references, and DESIGN-REQ-001 through DESIGN-REQ-013 coverage for SC-008.

### Models and Schemas

- [X] T031 Add `canResumeFromFailedStep` and resume disabled reason serialization to `ExecutionActionCapabilityModel` in `moonmind/schemas/temporal_models.py` covering FR-001, FR-009, SC-001, and DESIGN-REQ-005.
- [X] T032 Add resume source, resume checkpoint, preserved-step provenance, and resume response/request Pydantic models in `moonmind/schemas/temporal_models.py` covering FR-003, FR-004, FR-005, FR-008, DESIGN-REQ-009, DESIGN-REQ-011, and DESIGN-REQ-012.
- [X] T033 Extend `StepLedgerRowModel` or related step ledger schema in `moonmind/schemas/temporal_models.py` with preserved-from-source provenance while preserving existing step statuses and compatibility-sensitive query validation for FR-008, FR-010, and DESIGN-REQ-013.

### Services and Domain Logic

- [X] T034 Implement failed-step Resume eligibility and disabled reason calculation in `api_service/api/routers/executions.py` using source state, workflow type, original task input snapshot, failed step identity, and checkpoint availability for FR-001, FR-006, SCN-001, SCN-006, DESIGN-REQ-005, and DESIGN-REQ-008.
- [X] T035 Implement resume checkpoint loading and validation helpers in `moonmind/workflows/temporal/service.py` covering source identity, task snapshot, plan evidence, failed step identity, preserved outputs, prepared refs, and workspace state for FR-005, FR-006, FR-007, SC-004, and DESIGN-REQ-009.
- [X] T036 Implement edited task payload rejection for failed-step Resume requests in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` covering FR-004, SC-005, and the no-edit non-goal.
- [X] T037 Implement linked follow-up execution creation with `resumeSource` metadata, source workflow/run pinning, idempotency, source immutability, and original task input snapshot reuse in `moonmind/workflows/temporal/service.py` covering FR-002, FR-003, FR-004, SC-002, DESIGN-REQ-001, and DESIGN-REQ-008.
- [X] T038 Implement preserved-step materialization helpers in `moonmind/workflows/temporal/step_ledger.py` for source provenance, reused artifact refs, no attempt increment as new work, and operator-visible preserved summaries covering FR-008, FR-010, SC-003, DESIGN-REQ-003, and DESIGN-REQ-013.

### Endpoints, Workflow, and UI

- [X] T039 Add `POST /api/executions/{workflow_id}/resume-from-failed-step` to `api_service/api/routers/executions.py` with success and error response behavior from `contracts/resume-from-failed-step-api.md` covering FR-002, FR-004, FR-006, FR-007, SC-002, SC-004, and SC-005.
- [X] T040 Extend execution detail serialization in `api_service/api/routers/executions.py` to return failed-step Resume availability, checkpoint summary, disabled reasons, and source/resumed related runs for FR-001, FR-009, FR-010, SC-001, SC-006, DESIGN-REQ-006, and DESIGN-REQ-010.
- [X] T041 Extend `/api/executions/{workflow_id}/steps` serialization in `api_service/api/routers/executions.py` to include preserved-step provenance from validated ledger data for FR-008, FR-010, SC-003, and DESIGN-REQ-013.
- [X] T042 Implement resumed `MoonMind.Run` initialization in `moonmind/workflows/temporal/workflows/run.py` so validated resume source metadata materializes preserved prior steps, restores prepared/workspace state, and starts new execution at the failed step for FR-008, SCN-003, DESIGN-REQ-002, and DESIGN-REQ-004.
- [X] T043 Ensure invalid checkpoint, authorization, plan mismatch, missing output refs, and restore failures stop before failed-step execution in `moonmind/workflows/temporal/workflows/run.py` and `moonmind/workflows/temporal/service.py` covering FR-006, FR-007, SCN-005, and SC-004.
- [X] T044 Update task-detail action parsing, failed-step Resume button, accessible name, disabled reason rendering, confirmation/success handling, and lifecycle Resume separation in `frontend/src/entrypoints/task-detail.tsx` covering FR-001, FR-009, SC-001, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-007.
- [X] T045 Update task-detail related-run and preserved-step rendering in `frontend/src/entrypoints/task-detail.tsx` for source/resumed cross-links and `Resumed from failed step` labels covering FR-008, FR-009, SC-003, SC-006, SCN-007, and DESIGN-REQ-010.

### Integration Wiring and Story Validation

- [X] T046 Wire resume checkpoint artifact refs through existing Temporal artifact services in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` without embedding large checkpoint bodies in workflow history for FR-005, FR-010, DESIGN-REQ-011, and DESIGN-REQ-012.
- [ ] T047 Wire source/resumed relationship lookup through existing execution records or add the smallest necessary durable relation support in `api_service/api/routers/executions.py` and `api_service/db/models.py` only if existing records cannot satisfy authorized source/resumed detail queries for FR-002, FR-009, SC-006, and DESIGN-REQ-010.
- [X] T048 Run focused unit tests `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and fix failures in the touched backend files until FR-001 through FR-008 and FR-010 pass.
- [X] T049 Run focused frontend tests `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and fix failures in `frontend/src/entrypoints/task-detail.tsx` until FR-009, SC-001, SC-003, and SC-006 pass.
- [X] T050 Run focused contract and integration tests for `tests/contract/test_temporal_execution_api.py` and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, then fix failures in backend/workflow files until the successful Resume path and invalid checkpoint path pass.
- [ ] T051 Validate the single story end to end using `specs/310-resume-from-last-failed-step/quickstart.md`, confirming eligible Resume, linked follow-up execution, source immutability, preserved prior steps, invalid checkpoint failure, edited payload rejection, and related-run navigation for SCN-001 through SCN-007.

**Checkpoint**: Failed-step Resume is implemented, covered by unit and integration tests, and independently testable for MM-602.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T052 [P] Review `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/workflows/run.py`, and `moonmind/workflows/temporal/step_ledger.py` for secret hygiene, bounded checkpoint payloads, idempotency, fail-fast errors, and no silent full-rerun fallback covering DESIGN-REQ-003, DESIGN-REQ-009, DESIGN-REQ-011, and Constitution IX.
- [ ] T053 [P] Review `frontend/src/entrypoints/task-detail.tsx` for accessible failed-step Resume labels, clear disabled reasons, non-overlapping action states, and distinct lifecycle Resume behavior covering FR-009 and DESIGN-REQ-007.
- [ ] T054 [P] Update implementation notes in `specs/310-resume-from-last-failed-step/plan.md` only if reality changes during implementation, preserving MM-602 and avoiding migration details in canonical `docs/` for FR-012 and Constitution XII.
- [ ] T055 Run the full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and record the result for final verification in `specs/310-resume-from-last-failed-step/quickstart.md` evidence notes or the verification report.
- [ ] T056 Run the required hermetic integration suite with `./tools/test_integration.sh` or document a concrete environment blocker, preserving the targeted failed-step Resume integration evidence for FR-011 and SC-007.
- [ ] T057 Run `/speckit.verify` after implementation and tests pass, and ensure the verification report preserves MM-602, the original Jira preset brief, FR-001 through FR-012, SC-001 through SC-008, DESIGN-REQ-001 through DESIGN-REQ-013, tests run, and remaining risks.

---

## Dependencies and Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup; blocks all story test and implementation work.
- **Story (Phase 3)**: Depends on Foundational fixtures; unit and integration tests must be written and confirmed failing before implementation.
- **Polish and Verification (Phase 4)**: Depends on story implementation and focused tests passing.

### Within the Story

- T010 through T017 unit test authoring precedes all production implementation tasks.
- T018 through T023 integration/contract test authoring precedes all production implementation tasks.
- T024 through T028 red-first confirmation tasks must complete before T031.
- T029/T030 traceability verification can happen after red-first confirmation and before production code; T030 is conditional on traceability gaps.
- T031 through T033 schema/model work precedes service, endpoint, workflow, and UI implementation.
- T034 through T038 service/domain logic precedes route/workflow/UI wiring where those consumers depend on new models and validation helpers.
- T039 through T045 expose public/API/UI/workflow behavior after models and domain logic exist.
- T046 and T047 wire artifact and related-run integration after core service/route behavior exists.
- T048 through T051 validate the completed story before polish.
- T055 through T057 are final verification tasks after implementation is complete.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel after T001.
- T006, T007, T008, and T009 can run in parallel after T005 if fixture conventions are agreed.
- T010 through T017 can be authored in parallel because they target different test concerns and files.
- T018 through T023 can be authored in parallel with unit tests where files do not overlap.
- T031 and T033 both touch `moonmind/schemas/temporal_models.py` and should be serialized; T034/T035/T036/T037 share service/router files and should be coordinated.
- T044 and T045 both touch `frontend/src/entrypoints/task-detail.tsx` and should be serialized.
- T052, T053, and T054 can run in parallel after story validation.

## Parallel Example: Story Test Authoring

```bash
# Parallel test authoring examples after Phase 2:
Task: "T010 Add failed-step Resume backend action tests in tests/unit/api/routers/test_executions.py"
Task: "T013 Add resume checkpoint validation tests in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "T016 Add failed-step Resume UI tests in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T018 Add resume API contract tests in tests/contract/test_temporal_execution_api.py"
```

## Implementation Strategy

### TDD Story Delivery

1. Complete Phase 1 and Phase 2 setup/fixtures.
2. Write all unit, integration, contract, workflow, and frontend tests for FR-001 through FR-011 and SC-001 through SC-007.
3. Confirm the new tests fail for missing failed-step Resume behavior before production code changes.
4. Preserve FR-012 traceability and conditionally repair SC-008 traceability only if verification finds a gap.
5. Implement schemas, service validation, API routes, workflow initialization, step ledger provenance, artifact refs, related-run lookup, and task-detail UI.
6. Run focused tests, then full unit and integration suites.
7. Run `/speckit.verify` and compare final behavior against the preserved MM-602 Jira preset brief.

### Requirement Status Handling

- `missing` and `partial` rows receive red-first tests plus implementation tasks.
- `implemented_unverified` SC-008 receives verification-first work and conditional fallback traceability updates.
- `implemented_verified` FR-012 receives no production implementation task; it remains covered by final traceability verification.

## Notes

- This task list covers exactly one story: Resume Failed Task Progress.
- Do not implement editable Resume, broad run-history product surfaces, or generic RequestRerun changes beyond keeping failed-step Resume distinct.
- Do not embed large checkpoint bodies in workflow history; use compact refs and artifact-backed evidence.
- Do not use lifecycle `canResume` or Temporal `Resume` update as failed-step Resume.
- Keep source execution immutable and pin both source `workflowId` and source `runId`.
- Commit after each completed task or logical group during implementation.
