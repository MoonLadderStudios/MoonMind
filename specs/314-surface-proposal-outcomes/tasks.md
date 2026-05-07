# Tasks: Surface Proposal Outcomes

**Input**: Design documents from `/work/agent_jobs/mm:0c314657-113e-4d5e-951f-7149150d8b9e/repo/specs/314-surface-proposal-outcomes/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/proposal-outcome-visibility-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: Original Jira preset brief `MM-600` is preserved in `spec.md`. Tasks cover one story: Proposal Outcome Visibility. Traceability spans FR-001 through FR-014, acceptance scenarios 1-6, edge cases, SC-001 through SC-008, and DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030.

**Requirement Status Summary**: 24 partial rows require code-and-test work, 6 implemented_unverified rows require verification-first tests with conditional fallback implementation, and 2 implemented_verified rows require preservation through final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Focused Python unit tests: `python -m pytest tests/unit/workflows/temporal/workflows/test_run_proposals.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/agents/codex_worker/test_worker.py -q`
- Focused frontend unit tests: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/proposals.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Focused integration tests: `python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm the active one-story feature artifacts and testing entry points before writing tests.

- [X] T001 Confirm active feature artifacts exist and preserve `MM-600` in `specs/314-surface-proposal-outcomes/spec.md`, `specs/314-surface-proposal-outcomes/plan.md`, `specs/314-surface-proposal-outcomes/research.md`, `specs/314-surface-proposal-outcomes/data-model.md`, `specs/314-surface-proposal-outcomes/contracts/proposal-outcome-visibility-contract.md`, and `specs/314-surface-proposal-outcomes/quickstart.md`.
- [X] T002 Confirm the one-story scope and no existing `tasks.md` implementation state drift in `specs/314-surface-proposal-outcomes/spec.md` and `specs/314-surface-proposal-outcomes/plan.md`.
- [X] T003 [P] Confirm Python-focused proposal tests can be collected in `tests/unit/workflows/temporal/workflows/test_run_proposals.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/unit/agents/codex_worker/test_worker.py`.
- [X] T004 [P] Confirm frontend-focused proposal visibility tests can be collected in `frontend/src/entrypoints/task-detail.test.tsx`, `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and planned `frontend/src/entrypoints/proposals.test.tsx`.

---

## Phase 2: Foundational

**Purpose**: Establish shared fixtures and contract anchors needed before story test authoring.

**Blocking Rule**: No production implementation work begins until Phase 2 is complete.

- [ ] T005 Add shared proposal outcome fixture helpers for delivered, duplicate, malformed, failed-delivery, and promoted outcomes in `tests/unit/workflows/task_proposals/test_service.py` covering FR-002, FR-003, FR-005, FR-010, FR-011, FR-013, SC-001 through SC-006, DESIGN-REQ-009, DESIGN-REQ-028, and DESIGN-REQ-029.
- [ ] T006 [P] Add shared UI fixture payloads for proposal outcomes in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-004, FR-008, FR-009, FR-013, SC-002, SC-006, DESIGN-REQ-028.
- [ ] T007 [P] Add integration fixture helpers for a proposal-capable run outcome in `tests/integration/temporal/test_proposal_review_delivery.py` covering acceptance scenarios 1-6 and DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030.

**Checkpoint**: Shared fixtures and contract anchors are ready; story tests can be written.

---

## Phase 3: Story - Proposal Outcome Visibility

**Summary**: As a MoonMind operator, I want proposal generation, delivery, deduplication, failure, and promotion outcomes visible in run summaries and Mission Control so I can understand proposal-stage results without using a separate proposal queue.

**Independent Test**: Run or simulate one proposal-capable task with generated, delivered, duplicate, malformed, delivery-failed, and promoted proposal outcomes, then verify finish summary, exported run summary, execution detail, API-visible state, and Mission Control surfaces expose expected redacted outcome data while GitHub/Jira remain the normal review path.

**Traceability**: FR-001 through FR-014; acceptance scenarios 1-6; SC-001 through SC-008; DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030.

**Unit Test Plan**: Summary builders, proposal service serialization, API payloads, state mapping, redaction, malformed-candidate handling, provider-failure metadata, compact task summary, promotion links, and UI rendering.

**Integration Test Plan**: Proposal-capable run boundary covering delivered, duplicate, malformed, failed-delivery, and promoted outcomes across summary, exported summary, API/detail payload, and Mission Control-facing data.

### Unit Tests

- [X] T008 [P] Add failing unit tests for Temporal proposal summary requested/generated/submitted/delivered counts, redacted validation errors, delivery failures, external links, and dedup updates in `tests/unit/workflows/temporal/workflows/test_run_proposals.py` covering FR-001, FR-002, FR-003, FR-004, FR-005, SC-001, SC-002, SC-004, DESIGN-REQ-009, DESIGN-REQ-029.
- [X] T009 [P] Add failing unit tests for Codex worker `reports/run_summary.json` proposal outcome fields and redaction in `tests/unit/agents/codex_worker/test_worker.py` covering FR-001, FR-002, FR-003, FR-004, FR-005, FR-011, SC-001, SC-004, DESIGN-REQ-009, DESIGN-REQ-029.
- [ ] T010 Add verification-first unit tests for proposal-stage state exposure in `tests/unit/workflows/temporal/workflows/test_run_proposals.py` covering FR-006, SCN-004, SC-005, DESIGN-REQ-028.
- [ ] T011 [P] Add failing unit tests for proposal service delivery outcome aggregation, malformed candidate visible errors, failed delivery metadata, dedup new-or-updated status, and zero-promotion guarantee in `tests/unit/workflows/task_proposals/test_service.py` covering FR-003, FR-005, FR-010, FR-011, SC-003, SC-004, DESIGN-REQ-029.
- [X] T012 [P] Add failing API serialization tests for proposal outcome payloads, compact task summaries, review delivery details, and promotion result links in `tests/unit/api/routers/test_task_proposals.py` covering FR-004, FR-008, FR-009, FR-013, SC-002, SC-006, DESIGN-REQ-028.
- [X] T013 [P] Add preservation unit tests for dashboard compatibility mapping in `tests/unit/api/routers/test_task_dashboard_view_model.py` covering FR-007, SC-005.
- [ ] T014 [P] Add failing frontend unit tests for execution detail proposal outcome rendering in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-004, FR-008, FR-009, FR-013, SC-002, SC-006, DESIGN-REQ-028.
- [ ] T015 [P] Add failing frontend unit tests for Mission Control proposal outcome/status rendering in `frontend/src/entrypoints/mission-control.test.tsx` covering FR-008, FR-009, FR-013, SC-006, DESIGN-REQ-028.
- [ ] T016 [P] Add failing frontend unit tests proving normal navigation/copy does not make a standalone proposal queue the primary review path in `frontend/src/entrypoints/tasks-list.test.tsx` and `frontend/src/entrypoints/proposals.test.tsx` covering FR-012, SCN-006, SC-007, DESIGN-REQ-030.

### Integration Tests

- [ ] T017 Add failing integration test for a proposal-capable run with delivered and duplicate proposal outcomes in `tests/integration/temporal/test_proposal_review_delivery.py` covering acceptance scenarios 1-2, FR-001, FR-002, FR-004, FR-005, SC-001, SC-002, DESIGN-REQ-009.
- [ ] T018 Add failing integration test for malformed candidate and provider delivery failure visibility in `tests/integration/temporal/test_proposal_review_delivery.py` covering acceptance scenario 3, FR-003, FR-010, FR-011, SC-003, SC-004, DESIGN-REQ-029.
- [ ] T019 Add failing integration test for proposal-stage state, detail payload, compact task summary, and promotion result links in `tests/integration/temporal/test_proposal_review_delivery.py` covering acceptance scenarios 4-5, FR-006, FR-008, FR-009, FR-013, SC-005, SC-006, DESIGN-REQ-028.
- [ ] T020 Add failing integration or UI-boundary test proving external GitHub/Jira tracker review remains primary and MoonMind proposal UI is admin/recovery-only if retained in `tests/integration/temporal/test_proposal_review_delivery.py` covering acceptance scenario 6, FR-012, SC-007, DESIGN-REQ-030.

### Red-First Confirmation

- [X] T021 Run focused Python unit tests for `tests/unit/workflows/temporal/workflows/test_run_proposals.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/unit/agents/codex_worker/test_worker.py`; confirm new tests from T008-T013 fail for the expected missing proposal outcome behavior.
- [ ] T022 Run focused frontend unit tests for `frontend/src/entrypoints/task-detail.test.tsx`, `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/proposals.test.tsx`; confirm new tests from T014-T016 fail for the expected missing proposal outcome UI behavior.
- [ ] T023 Run focused integration tests for `tests/integration/temporal/test_proposal_review_delivery.py`; confirm new tests from T017-T020 fail for the expected missing end-to-end proposal outcome behavior.

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [ ] T024 If T010 shows proposal-stage state exposure is not already complete, update `moonmind/workflows/temporal/workflows/run.py` and `api_service/api/routers/executions.py` for FR-006, SCN-004, SC-005, DESIGN-REQ-028.
- [ ] T025 If T011 shows malformed candidate skip/no-promotion behavior is incomplete, update `moonmind/workflows/task_proposals/service.py` and `moonmind/agents/codex_worker/worker.py` for FR-010, SC-003, DESIGN-REQ-029.
- [ ] T026 If final traceability checks fail, update `specs/314-surface-proposal-outcomes/spec.md`, `specs/314-surface-proposal-outcomes/plan.md`, `specs/314-surface-proposal-outcomes/tasks.md`, and later verification artifacts to preserve MM-600, FR-014, SC-008.

### Implementation

- [X] T027 Extend proposal outcome summary models and payload shapes in `moonmind/workflows/temporal/workflows/run.py`, `moonmind/agents/codex_worker/worker.py`, and `moonmind/schemas/task_proposal_models.py` for FR-001, FR-002, FR-003, FR-004, FR-005, FR-011, DESIGN-REQ-009, DESIGN-REQ-029.
- [X] T028 Implement delivered count, external links, dedup updates, redacted validation errors, and provider delivery failures in `moonmind/workflows/task_proposals/service.py` and `moonmind/workflows/temporal/activity_runtime.py` for FR-002, FR-003, FR-004, FR-005, FR-011, SC-001 through SC-004.
- [X] T029 Implement compact proposal task summary fields, including priority and max attempts where present, in `api_service/api/routers/task_proposals.py` and `moonmind/schemas/task_proposal_models.py` for FR-008, FR-009, SC-006, DESIGN-REQ-028.
- [X] T030 Implement run-scoped proposal outcome exposure for execution detail and Mission Control consumers in `api_service/api/routers/executions.py` and any required schema helpers in `api_service/api/schemas.py` for FR-004, FR-008, FR-009, FR-013, SC-002, SC-006, DESIGN-REQ-028.
- [X] T031 Implement promotion result link derivation from provider decision metadata in `api_service/api/routers/task_proposals.py`, `api_service/api/routers/executions.py`, and `moonmind/workflows/task_proposals/service.py` for FR-013, SCN-005, SC-006.
- [X] T032 Implement execution detail proposal outcome rendering in `frontend/src/entrypoints/task-detail.tsx` and any required styling in `frontend/src/styles/mission-control.css` for FR-004, FR-008, FR-009, FR-013, SC-002, SC-006.
- [ ] T033 Implement Mission Control proposal outcome rendering or task-detail handoff in `frontend/src/entrypoints/mission-control.tsx`, `frontend/src/entrypoints/mission-control-app.tsx`, and `frontend/src/styles/mission-control.css` for FR-008, FR-009, FR-013, SC-006.
- [X] T034 Reframe, restrict, or remove normal proposal queue affordances in `frontend/src/entrypoints/proposals.tsx`, `frontend/src/entrypoints/mission-control-app.tsx`, and `frontend/src/entrypoints/tasks-list.tsx` so GitHub/Jira remain the primary review path for FR-012, SCN-006, SC-007, DESIGN-REQ-030.
- [ ] T035 Wire integration boundary behavior for proposal outcome summaries, detail payloads, and UI-facing fields in `tests/integration/temporal/test_proposal_review_delivery.py`, `moonmind/workflows/temporal/workflows/run.py`, and `api_service/api/routers/executions.py` for acceptance scenarios 1-6 and DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030.

### Story Validation

- [X] T036 Run focused Python unit tests for `tests/unit/workflows/temporal/workflows/test_run_proposals.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, and `tests/unit/agents/codex_worker/test_worker.py`; fix failures until FR-001 through FR-014 and DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030 pass in the focused backend scope.
- [X] T037 Run focused frontend unit tests for `frontend/src/entrypoints/task-detail.test.tsx`, `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/proposals.test.tsx`; fix failures until Mission Control and execution detail satisfy FR-008, FR-009, FR-012, FR-013.
- [X] T038 Run focused integration tests for `tests/integration/temporal/test_proposal_review_delivery.py`; fix failures until acceptance scenarios 1-6 and SC-001 through SC-007 pass.
- [ ] T039 Run quickstart validation from `specs/314-surface-proposal-outcomes/quickstart.md` and record exact blockers if Docker-backed integration execution is unavailable in the managed environment.

**Checkpoint**: The single story is implemented, covered by unit and integration tests, and independently validated against MM-600.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [ ] T040 [P] Review proposal outcome payloads for compactness and secret redaction in `moonmind/workflows/temporal/workflows/run.py`, `moonmind/agents/codex_worker/worker.py`, `moonmind/workflows/task_proposals/service.py`, and `api_service/api/routers/executions.py` covering FR-003, FR-011, SC-004.
- [ ] T041 [P] Review UI accessibility and text fit for proposal outcome rendering in `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/mission-control.tsx`, and `frontend/src/styles/mission-control.css` covering FR-008, FR-009, FR-013.
- [ ] T042 [P] Preserve MM-600 traceability in `specs/314-surface-proposal-outcomes/verification.md` when final verification is produced, covering FR-014, SC-008.
- [X] T043 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` using the command documented in `specs/314-surface-proposal-outcomes/quickstart.md` for final unit verification and record failures or environment blockers.
- [ ] T044 Run `./tools/test_integration.sh` using the command documented in `specs/314-surface-proposal-outcomes/quickstart.md` for final `integration_ci` verification when Docker is available and record failures or environment blockers.
- [ ] T045 Run `/moonspec-verify` for `specs/314-surface-proposal-outcomes/` after implementation and tests pass, preserving MM-600 and DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029, DESIGN-REQ-030 in verification evidence.

---

## Dependencies And Execution Order

### Phase Dependencies

- Phase 1 Setup has no dependencies.
- Phase 2 Foundational depends on Phase 1 and blocks story tests.
- Phase 3 Story depends on Phase 2.
- Phase 4 Polish and Verification depends on story implementation and focused validation passing.

### Within The Story

- T008-T016 unit/frontend tests must be written before T027-T034 implementation.
- T017-T020 integration tests must be written before T035 integration wiring.
- T021-T023 red-first confirmation must complete before production implementation tasks T027-T035.
- T024-T026 are conditional fallback tasks for implemented_unverified rows and should run only when verification tests expose a gap.
- T027-T031 backend contracts and services should complete before T032-T034 UI rendering.
- T036-T039 validate the story after implementation.
- T045 `/moonspec-verify` runs only after implementation and test evidence is available.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001-T002.
- T006 and T007 can run in parallel after T005.
- T008-T016 can be authored in parallel because they touch different test files.
- T017-T020 are ordered because they all modify `tests/integration/temporal/test_proposal_review_delivery.py`.
- T024-T026 are conditional and independent by file set.
- T040-T042 can run in parallel during polish.

## Parallel Example

```bash
# After Phase 2, launch independent test authoring:
Task: "T008 add Temporal summary tests in tests/unit/workflows/temporal/workflows/test_run_proposals.py"
Task: "T012 add API serialization tests in tests/unit/api/routers/test_task_proposals.py"
Task: "T014 add task detail UI tests in frontend/src/entrypoints/task-detail.test.tsx"

# After backend outcome fields exist, launch UI/admin-path work separately:
Task: "T032 implement execution detail rendering in frontend/src/entrypoints/task-detail.tsx"
Task: "T034 reframe proposal queue affordances in frontend/src/entrypoints/proposals.tsx"
```

## Implementation Strategy

1. Preserve the single-story MM-600 traceability from `spec.md`.
2. Complete setup and shared fixtures.
3. Write unit, frontend, and integration tests first.
4. Run red-first commands and confirm failures identify missing proposal outcome behavior.
5. Execute conditional fallback tasks only where verification-first tests fail.
6. Implement backend summary/service/API contracts.
7. Implement frontend Mission Control and execution-detail surfaces.
8. Validate focused Python, frontend, and integration test suites.
9. Run full unit and integration commands when available.
10. Run final `/moonspec-verify`.

## Notes

- This task list covers exactly one story: Proposal Outcome Visibility.
- Do not create a standalone proposal-review workflow as normal UX; GitHub/Jira remain the normal review path.
- Do not embed full task snapshots or provider issue bodies in workflow summaries or UI outcome cards.
- Keep all redacted errors compact and secret-safe.
- Do not create commits, pull requests, Jira transitions, or implementation outside this task list during task generation.
