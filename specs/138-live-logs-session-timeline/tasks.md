# Tasks: Live Logs Session Timeline

**Input**: Design documents from `/specs/138-live-logs-session-timeline/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: This feature is explicitly TDD-driven. Write or update failing tests before the corresponding implementation tasks.

**Organization**: Tasks are grouped by user story so each slice can be implemented and validated independently.

## Phase 1: Setup (Shared Test Baseline)

**Purpose**: Establish failing tests for the Phase 0 and Phase 1 contract changes before runtime code is updated.

- [X] T001 [P] Add session-timeline rollout-setting coverage in `tests/unit/config/test_settings.py`
- [X] T002 [P] Add task-dashboard runtime-config coverage for `liveLogsSessionTimelineEnabled` and rollout scope in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T003 [P] Add canonical observability-event contract and durable-history tests in `tests/unit/services/temporal/runtime/test_log_streamer.py`
- [X] T004 [P] Add managed-run record persistence coverage for event-history refs and session snapshot fields in `tests/unit/services/temporal/runtime/test_store.py`
- [X] T005 [P] Add task-runs router coverage for structured observability-history reads and summary snapshot fallback in `tests/unit/api/routers/test_task_runs.py`
- [X] T006 [P] Add managed-run/session publication coverage for `observability.events.jsonl` persistence in `tests/unit/services/temporal/runtime/test_supervisor_live_output.py` and `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`

---

## Phase 2: Foundational (Blocking Contract Changes)

**Purpose**: Introduce the shared config and schema contracts needed by every user story.

**⚠️ CRITICAL**: User-story implementation should not begin until these contract changes are complete.

- [X] T007 Implement the session-timeline rollout setting in `moonmind/config/settings.py`
- [X] T008 Implement task-dashboard runtime-config exposure for the session-timeline rollout fields in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T009 Implement `RunObservabilityEvent`, expand event kinds, and extend `ManagedRunRecord` with session snapshot + `observabilityEventsRef` fields in `moonmind/schemas/agent_runtime_models.py`

**Checkpoint**: The repo has one shared rollout/config contract and one shared observability-event contract.

---

## Phase 3: User Story 1 - Re-baseline the rollout around the shipped live logs stack (Priority: P1)

**Goal**: Make the implementation tracker and runtime config accurately describe the shipped Live Logs baseline and the new session-timeline rollout boundary.

**Independent Test**: Inspect the tmp plan and runtime-config tests to confirm the shipped artifact/spool/SSE baseline is preserved and the session-timeline rollout fields are exposed independently from `logStreamingEnabled`.

### Implementation for User Story 1

- [X] T010 [US1] Replace `docs/ManagedAgents/LiveLogs.md` with the session-aware rollout tracker aligned to the shipped baseline
- [X] T011 [US1] Align feature-flag defaults and sample config documentation for the new rollout field in `api_service/config.template.toml` and `moonmind/config/settings.py`

**Checkpoint**: The migration tracker and runtime boot payload both describe the session-aware upgrade rather than a greenfield live-logs build.

---

## Phase 4: User Story 2 - Persist one canonical observability timeline contract (Priority: P1)

**Goal**: Publish, persist, and store one canonical event history for stdout, stderr, system, and session rows.

**Independent Test**: Generate mixed observability rows for a managed run and confirm the runtime writes `observability.events.jsonl` plus a corresponding ref on the managed-run record.

### Implementation for User Story 2

- [X] T012 [US2] Update spool transport to read and write the canonical observability-event contract in `moonmind/observability/transport.py`
- [X] T013 [US2] Update runtime event publication and durable event-history artifact creation in `moonmind/workflows/temporal/runtime/log_streamer.py`
- [X] T014 [US2] Persist `observabilityEventsRef` and latest session snapshot fields during managed-run finalization in `moonmind/workflows/temporal/runtime/supervisor.py`
- [X] T015 [US2] Persist `observabilityEventsRef` and latest session snapshot fields during managed-session publication in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [X] T016 [US2] Keep file-backed managed-run persistence compatible with the new record fields in `moonmind/workflows/temporal/runtime/store.py`

**Checkpoint**: Completed runs have durable structured event history and record-level session context without depending on live transport.

---

## Phase 5: User Story 3 - Preserve session context in observability summaries and records (Priority: P2)

**Goal**: Make summary/history readers prefer durable event history and record-backed session context while keeping existing live-log consumers readable.

**Independent Test**: Load observability summary and history for completed runs with and without event-history artifacts and confirm the router prefers structured history first, then degrades cleanly.

### Implementation for User Story 3

- [X] T017 [US3] Update task-run observability normalization, summary shaping, and structured-history retrieval in `api_service/api/routers/task_runs.py`
- [X] T018 [US3] Regenerate or update any affected API/runtime payload expectations in `frontend/src/generated/openapi.ts` if the backend contract changes require it

**Checkpoint**: Task-run observability APIs surface durable event-history refs and record-backed session snapshots while preserving current live-log readability.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify the feature end to end and close the execution loop.

- [X] T019 Run focused Phase 0 and Phase 1 verification in `./tools/test_unit.sh tests/unit/config/test_settings.py tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/services/temporal/runtime/test_log_streamer.py tests/unit/services/temporal/runtime/test_store.py tests/unit/services/temporal/runtime/test_supervisor_live_output.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/api/routers/test_task_runs.py`
- [X] T020 Run runtime scope validation in `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
- [X] T021 Run full regression verification in `./tools/test_unit.sh`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on the failing tests from Phase 1.
- **User Story 1 (Phase 3)**: Depends on Phase 2.
- **User Story 2 (Phase 4)**: Depends on Phase 2 and should land before User Story 3 because the router changes consume the new record/event fields.
- **User Story 3 (Phase 5)**: Depends on Phase 4.
- **Polish (Phase 6)**: Depends on all implementation phases completing.

### Parallel Opportunities

- `T001` through `T006` can run in parallel because they touch different test files.
- `T007` through `T009` can proceed in parallel only where file ownership does not overlap.
- `T014`, `T015`, and `T016` can be staged independently after `T009`, then integrated before `T017`.

## Implementation Strategy

### MVP First

1. Write all failing tests.
2. Land the foundational config and schema contracts.
3. Replace the tmp rollout tracker and flag wiring.
4. Persist structured event history and record-level session snapshots.
5. Update summary/history readers and verify the focused test slice.

### Notes

- Keep the spool/SSE transport boundary intact.
- Do not add a second long-lived internal event model for compatibility.
- Mark each task `[X]` in this file as it completes.
