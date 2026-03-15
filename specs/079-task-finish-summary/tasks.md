# Tasks: Task Finish Summary System

**Input**: Design documents from `/specs/041-task-finish-summary/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`

**Tests**: Runtime validation is required for this feature; include worker, API, and dashboard coverage plus `./tools/test_unit.sh`.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- Use `- [ ]` checkbox syntax for every task
- `T###` is the sequential execution id
- `[P]` marks tasks that can run in parallel
- `[US#]` is required for user-story phase tasks only
- Every task includes concrete file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Lock runtime scope and traceability inputs before implementation starts.

- [X] T001 Confirm runtime-mode execution checklist and validation commands in `specs/041-task-finish-summary/quickstart.md` and `specs/041-task-finish-summary/checklists/requirements.md` (DOC-REQ-019).
- [X] T002 [P] Refresh `specs/041-task-finish-summary/contracts/requirements-traceability.md` with explicit implementation/validation owners and deterministic implementation-task + validation-task references for each `DOC-REQ-*` row (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018, DOC-REQ-019).
- [X] T003 [P] Confirm additive migration scaffold details in `api_service/migrations/versions/202602240001_task_finish_summary.py` before foundational coding begins (DOC-REQ-008, DOC-REQ-019).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared persistence and schema plumbing required by all user stories.

- [X] T004 Implement finish metadata columns and ORM mappings in `api_service/migrations/versions/202602240001_task_finish_summary.py` and `moonmind/workflows/agent_queue/models.py` (DOC-REQ-005, DOC-REQ-008, DOC-REQ-010).
- [X] T005 Implement shared finish summary request/response models in `moonmind/schemas/agent_queue_models.py` for complete/fail/cancel payloads and read models (DOC-REQ-004, DOC-REQ-006, DOC-REQ-009, DOC-REQ-014).
- [X] T006 Wire queue repositories and service terminal-transition metadata persistence in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-005, DOC-REQ-008, DOC-REQ-014).
- [X] T007 [P] Add compact list vs detail finish metadata mapping in `api_service/api/routers/agent_queue.py` and `moonmind/schemas/agent_queue_models.py` (DOC-REQ-001, DOC-REQ-010).
- [X] T008 [P] Add shared dashboard finish-metadata normalization helpers for list/detail rendering in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-015, DOC-REQ-016).

**Checkpoint**: Foundation complete; user stories can proceed without reworking core persistence/schema contracts.

---

## Phase 3: User Story 1 - Scan Job Outcomes Instantly (Priority: P1) (MVP)

**Goal**: Operators can triage terminal jobs from list/active views using outcome code, stage, and reason.

**Independent Test**: Queue list and active panels show documented badges plus stage/reason context for all terminal outcomes without requiring artifact downloads.

### Tests for User Story 1

- [X] T009 [P] [US1] Add list API response tests in `tests/unit/api/routers/test_agent_queue.py` to verify `finishOutcomeCode`, `finishOutcomeStage`, and `finishOutcomeReason` are present while `finishSummary` is excluded from list payloads (DOC-REQ-001, DOC-REQ-005, DOC-REQ-010, DOC-REQ-015).
- [X] T010 [P] [US1] Add list/active dashboard rendering tests in `tests/task_dashboard/test_queue_layouts.js` for outcome badge + stage/reason display across terminal states (DOC-REQ-001, DOC-REQ-015).

### Implementation for User Story 1

- [X] T011 [US1] Implement queue list response serialization updates in `api_service/api/routers/agent_queue.py` and `moonmind/workflows/agent_queue/service.py` for compact finish outcome fields (DOC-REQ-001, DOC-REQ-005, DOC-REQ-010, DOC-REQ-015).
- [X] T012 [US1] Implement list/active outcome badge rendering and stage/reason context in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-015).
- [X] T013 [US1] Ensure list-facing finish reason text is redacted/size-bounded before exposure in `moonmind/agents/codex_worker/worker.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-005, DOC-REQ-007).

**Checkpoint**: User Story 1 independently delivers fast terminal-run triage from queue list surfaces.

---

## Phase 4: User Story 2 - Understand Full Finish State in Detail View (Priority: P2)

**Goal**: Operators can inspect complete finish summaries in job detail and jump directly to proposals filtered by origin job.

**Independent Test**: Job detail shows finish outcome, publish summary, proposals summary, and deep links open proposals filtered with `originSource=queue&originId=<jobId>`.

### Tests for User Story 2

- [X] T014 [P] [US2] Add detail API and `/finish-summary` endpoint tests in `tests/unit/api/routers/test_agent_queue.py` for summary retrieval behavior (DOC-REQ-006, DOC-REQ-010, DOC-REQ-016).
- [X] T015 [P] [US2] Add proposals filter tests for `originSource` + `originId` in `tests/unit/api/routers/test_task_proposals.py` (DOC-REQ-017).
- [X] T016 [P] [US2] Add dashboard detail-panel and deep-link tests in `tests/task_dashboard/test_queue_layouts.js` (DOC-REQ-002, DOC-REQ-016, DOC-REQ-017).

### Implementation for User Story 2

- [X] T017 [US2] Implement detail `finishSummary` exposure and `/api/queue/jobs/{jobId}/finish-summary` route behavior in `api_service/api/routers/agent_queue.py`, `moonmind/workflows/agent_queue/service.py`, and `moonmind/schemas/agent_queue_models.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-010, DOC-REQ-016).
- [X] T018 [US2] Implement proposals origin-id filtering in `api_service/api/routers/task_proposals.py`, `moonmind/workflows/task_proposals/service.py`, and `moonmind/workflows/task_proposals/repositories.py` (DOC-REQ-014, DOC-REQ-017).
- [X] T019 [US2] Implement dashboard finish-summary detail panel plus proposals deep-link query wiring in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-002, DOC-REQ-016, DOC-REQ-017).

**Checkpoint**: User Story 2 independently delivers detail diagnostics and queue-origin proposals navigation.

---

## Phase 5: User Story 3 - Produce Deterministic Machine-Readable Run Summaries (Priority: P3)

**Goal**: Worker, persistence, and API layers produce one canonical redacted finish summary contract for every terminal run.

**Independent Test**: Representative PR publish, branch publish, no-change, publish-disabled, failure, and cancelled runs classify deterministically, persist finish metadata, and emit `reports/run_summary.json` best effort.

### Tests for User Story 3

- [X] T020 [P] [US3] Expand worker terminal classification and finalize-stage timing tests in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-011, DOC-REQ-012).
- [X] T021 [P] [US3] Add worker redaction, proposal summary, and artifact write failure-path tests in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-003, DOC-REQ-007, DOC-REQ-013).
- [X] T022 [P] [US3] Extend queue terminal mutation/persistence tests in `tests/unit/api/routers/test_agent_queue.py` and `tests/unit/api/routers/test_task_proposals.py` (DOC-REQ-008, DOC-REQ-009, DOC-REQ-014).

### Implementation for User Story 3

- [X] T023 [US3] Implement finalize-stage timing capture and canonical `finishSummary` payload builder in `moonmind/agents/codex_worker/worker.py` and `moonmind/schemas/agent_queue_models.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-006, DOC-REQ-011).
- [X] T024 [US3] Implement deterministic finish outcome classification precedence and publish-outcome mapping in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-004, DOC-REQ-012).
- [X] T025 [US3] Implement finish summary redaction and proposal outcome aggregation (requested/hook skills/generated/submitted/errors) in `moonmind/agents/codex_worker/worker.py` and `moonmind/workflows/task_proposals/service.py` (DOC-REQ-002, DOC-REQ-007, DOC-REQ-013).
- [X] T026 [US3] Propagate finish metadata through complete/fail/cancel acknowledgements in `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py`, and `api_service/api/routers/agent_queue.py` (DOC-REQ-009, DOC-REQ-014).
- [X] T027 [US3] Preserve existing proposal promotion semantics while adding queue-origin finish metadata linkage in `moonmind/workflows/task_proposals/repositories.py` and `moonmind/workflows/task_proposals/service.py` (DOC-REQ-002, DOC-REQ-014).
- [X] T028 [US3] Align shared runtime contracts for worker/API/dashboard finish summary compatibility in `moonmind/schemas/agent_queue_models.py`, `api_service/api/routers/agent_queue.py`, and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-018, DOC-REQ-019).

**Checkpoint**: User Story 3 independently delivers deterministic finish summaries with persistence, artifacts, and redaction.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final cross-surface verification and traceability closure.

- [X] T029 [P] Update final implementation/validation evidence links in `specs/041-task-finish-summary/contracts/requirements-traceability.md` and `specs/041-task-finish-summary/quickstart.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018, DOC-REQ-019).
- [X] T030 Run `./tools/test_unit.sh` and record pass evidence in `specs/041-task-finish-summary/quickstart.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018, DOC-REQ-019).
- [X] T031 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and record the output in `specs/041-task-finish-summary/quickstart.md` (DOC-REQ-019).
- [X] T032 [P] Execute manual queue list/detail/proposals smoke checks and log observed results in `specs/041-task-finish-summary/quickstart.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-015, DOC-REQ-016, DOC-REQ-017, DOC-REQ-018).
- [X] T033 [P] Run a deterministic DOC-REQ coverage audit (one implementation task + one validation task per `DOC-REQ-*`) and record results in `specs/041-task-finish-summary/contracts/requirements-traceability.md` and `specs/041-task-finish-summary/quickstart.md` (DOC-REQ-018, DOC-REQ-019).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 starts immediately.
- Phase 2 depends on Phase 1 completion and blocks all user-story work.
- Phase 3, Phase 4, and Phase 5 all depend on Phase 2 completion.
- Phase 6 depends on completion of all targeted user stories.
- Phase 6 deterministic coverage audit task (`T033`) depends on Phase 6 validation evidence tasks (`T029`-`T032`).

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2; no dependency on US2 or US3.
- **US2 (P2)**: Starts after Phase 2 and can proceed in parallel with US3 once shared contracts are stable.
- **US3 (P3)**: Starts after Phase 2 and can proceed in parallel with US2, but should land before final Phase 6 validation.

### Within Each User Story

- Tests should be authored before implementation and fail before code changes are finalized.
- Backend contract/schema updates should land before UI rendering work that depends on them.
- Worker classification logic should land before terminal transition integration and dashboard detail finalization.

## Parallel Opportunities

- Phase 1 tasks `T002` and `T003` are parallel.
- Phase 2 tasks `T007` and `T008` are parallel after `T004`-`T006` establish shared data plumbing.
- US1 tests `T009` and `T010` are parallel; implementation `T011` and `T012` can run in parallel once schemas are stable.
- US2 tests `T014`-`T016` are parallel; implementation `T018` and `T019` are parallel after `T017` contract exposure is in place.
- US3 tests `T020`-`T022` are parallel; implementation `T024`, `T025`, and `T027` are parallel after `T023` creates canonical finish-summary construction.
- Phase 6 task `T033` is parallelizable after `T030`-`T032` produce validation evidence.

## Parallel Example: User Story 1

```bash
Task: T009 [US1] tests/unit/api/routers/test_agent_queue.py
Task: T010 [US1] tests/task_dashboard/test_queue_layouts.js
Task: T012 [US1] api_service/static/task_dashboard/dashboard.js
```

## Parallel Example: User Story 2

```bash
Task: T015 [US2] tests/unit/api/routers/test_task_proposals.py
Task: T016 [US2] tests/task_dashboard/test_queue_layouts.js
Task: T018 [US2] api_service/api/routers/task_proposals.py + moonmind/workflows/task_proposals/*
```

## Parallel Example: User Story 3

```bash
Task: T020 [US3] tests/unit/agents/codex_worker/test_worker.py
Task: T022 [US3] tests/unit/api/routers/test_agent_queue.py + tests/unit/api/routers/test_task_proposals.py
Task: T025 [US3] moonmind/agents/codex_worker/worker.py + moonmind/workflows/task_proposals/service.py
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 (US1) and validate list triage behavior.
3. Stop and verify operational value before expanding scope.

### Incremental Delivery

1. Add US2 detail diagnostics and queue-origin proposal deep links.
2. Add US3 deterministic finalization/classification and canonical summary artifacts.
3. Complete Phase 6 full validation and traceability evidence.

### Team Parallelization

1. Backend schema/persistence owner: Phase 2 + US3 backend tasks.
2. API/contract owner: US1/US2 router tasks and API tests.
3. Dashboard owner: US1/US2 UI tasks and dashboard tests.
4. Integration owner: Phase 6 validation and evidence capture.

---

## Notes

- `[P]` tasks are designed to avoid file conflicts when executed concurrently.
- Every `DOC-REQ-*` id from the source document is carried into implementation and validation tasks for explicit traceability.
- Runtime implementation and validation are mandatory for acceptance of this feature.

## DOC-REQ Coverage Matrix (Deterministic Gate)

| DOC-REQ | Implementation Tasks | Validation Tasks | Traceability Row |
| --- | --- | --- | --- |
| DOC-REQ-001 | T011, T017, T023 | T009, T010, T014, T030, T032 | `contracts/requirements-traceability.md` row `DOC-REQ-001` |
| DOC-REQ-002 | T019, T025, T027 | T016, T030, T032 | `contracts/requirements-traceability.md` row `DOC-REQ-002` |
| DOC-REQ-003 | T023 | T020, T021, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-003` |
| DOC-REQ-004 | T005, T024 | T020, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-004` |
| DOC-REQ-005 | T004, T006, T011, T013 | T009, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-005` |
| DOC-REQ-006 | T005, T017, T023 | T014, T020, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-006` |
| DOC-REQ-007 | T013, T025 | T021, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-007` |
| DOC-REQ-008 | T004, T006, T026 | T022, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-008` |
| DOC-REQ-009 | T005, T017, T026 | T022, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-009` |
| DOC-REQ-010 | T004, T007, T011, T017 | T009, T014, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-010` |
| DOC-REQ-011 | T023 | T020, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-011` |
| DOC-REQ-012 | T024 | T020, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-012` |
| DOC-REQ-013 | T025 | T021, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-013` |
| DOC-REQ-014 | T006, T018, T026, T027 | T022, T030 | `contracts/requirements-traceability.md` row `DOC-REQ-014` |
| DOC-REQ-015 | T008, T011, T012 | T010, T030, T032 | `contracts/requirements-traceability.md` row `DOC-REQ-015` |
| DOC-REQ-016 | T017, T019 | T014, T016, T030, T032 | `contracts/requirements-traceability.md` row `DOC-REQ-016` |
| DOC-REQ-017 | T018, T019 | T015, T016, T030, T032 | `contracts/requirements-traceability.md` row `DOC-REQ-017` |
| DOC-REQ-018 | T028 | T020, T021, T022, T030, T032 | `contracts/requirements-traceability.md` row `DOC-REQ-018` |
| DOC-REQ-019 | T004, T005, T011, T017, T023, T028 | T030, T031, T033 | `contracts/requirements-traceability.md` row `DOC-REQ-019` |
