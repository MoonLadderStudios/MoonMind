# Tasks: Live Logs Phase 1 Activation

**Input**: Design documents from `/specs/147-live-logs-phase1-activation/`
**Prerequisites**: plan.md, spec.md

**Tests**: Tests are required for this slice because the user explicitly requested test-driven development.

**Organization**: Tasks are grouped by user story so the active-record path and truthful failure handling can be verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task traces to (`US1`, `US2`, `US3`)

## Phase 1: Foundational Contract Alignment

**Purpose**: Lock the Phase 1 slice onto the actual local code seams before implementation.

- [X] T001 Review the current Codex adapter, managed-session controller/supervisor, and task-run router contracts in `moonmind/workflows/adapters/codex_session_adapter.py`, `moonmind/workflows/temporal/runtime/managed_session_controller.py`, `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`, and `api_service/api/routers/task_runs.py`.
- [X] T002 Validate task scope in runtime mode with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.

---

## Phase 2: User Story 1 - Mission Control attaches before the Codex turn finishes (Priority: P1) 🎯 MVP

**Goal**: Persist an active live-capable task-run record before `send_turn` completes so the existing Live Logs summary/history/SSE path can attach to an in-flight Codex managed-session run.

**Independent Test**: Pause `send_turn` inside the adapter test and confirm the managed-run store already contains a `running` record with session metadata and `liveStreamCapable: true`.

### Tests for User Story 1

- [X] T003 [P] [US1] Add failing adapter coverage in `tests/unit/workflows/adapters/test_codex_session_adapter.py` proving `CodexSessionAdapter.start()` writes a `running` managed-run record before `_send_turn(...)` completes and advertises `liveStreamCapable=True`.
- [X] T004 [P] [US1] Add or tighten router assertions in `tests/unit/api/routers/test_task_runs.py` covering active capable summary/stream truthfulness for Codex managed-session runs.

### Implementation for User Story 1

- [X] T005 [US1] Update early persistence flow in `moonmind/workflows/adapters/codex_session_adapter.py` so a `running` `ManagedRunRecord` is saved immediately after session resolution and locator creation.
- [X] T006 [US1] Update task-run record persistence in `moonmind/workflows/adapters/codex_session_adapter.py` so active Codex managed-session runs preserve workspace/session snapshot fields and advertise `liveStreamCapable=True`.

**Checkpoint**: Mission Control can discover an active Codex managed-session run before the turn finishes.

---

## Phase 3: User Story 2 - Active Codex turns publish observable session events (Priority: P1)

**Goal**: Ensure controller/supervisor session-plane events remain visible through the task-run observability stream during the active turn and that final artifact publication preserves the durable refs on the task-run record.

**Independent Test**: Emit `session_started`, `turn_started`, and `turn_completed` through the managed-session controller/supervisor path and confirm task-run observability still exposes them while the run is active and after publication completes.

### Tests for User Story 2

- [X] T007 [P] [US2] Add or tighten boundary coverage in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` proving the existing session event publication path still emits normalized timeline rows for `send_turn`.
- [X] T008 [P] [US2] Add adapter assertions in `tests/unit/workflows/adapters/test_codex_session_adapter.py` proving the final task-run record retains `observabilityEventsRef` and artifact refs after publication.

### Implementation for User Story 2

- [X] T009 [US2] Adjust `moonmind/workflows/adapters/codex_session_adapter.py` and any required runtime helper seams so completion updates preserve the active-record session metadata while keeping controller/supervisor-published event rows usable through the task-run observability path.

**Checkpoint**: The active run shows session lifecycle activity before completion and preserves durable refs afterward.

---

## Phase 4: User Story 3 - Failure paths stay truthful for in-flight observability (Priority: P2)

**Goal**: Guarantee the early-persisted run record does not remain stale when a managed turn fails.

**Independent Test**: Force a failed `send_turn` response and confirm the task-run record transitions from the early `running` state to a terminal failure state with the same workspace/session metadata.

### Tests for User Story 3

- [X] T010 [P] [US3] Add failing adapter coverage in `tests/unit/workflows/adapters/test_codex_session_adapter.py` proving a failed turn rewrites the early live-capable record to a terminal failure state.

### Implementation for User Story 3

- [X] T011 [US3] Update failure handling in `moonmind/workflows/adapters/codex_session_adapter.py` so turn failures finalize the previously persisted task-run record truthfully instead of leaving it `running`.

**Checkpoint**: Early-persisted records stay truthful across send-turn failures.

---

## Phase 5: Validation and Closeout

**Purpose**: Run the required validation and finish the spec workflow cleanly.

- [X] T012 Run `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`.
- [X] T013 Mark completed tasks as `[X]` in `specs/147-live-logs-phase1-activation/tasks.md`.
- [X] T014 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 establishes the exact implementation scope and validation guardrail.
- Phase 2 is the MVP and blocks everything else because the active record must exist before the other observability surfaces matter.
- Phase 3 depends on Phase 2 because the task-run record must exist before active event publication is useful to Mission Control.
- Phase 4 depends on Phase 2 because it updates the new early-persistence window.
- Phase 5 runs after all implementation work is complete.

### Within Each User Story

- Write the tests first and confirm they fail for the targeted gap.
- Implement the runtime changes only after the relevant failing tests exist.
- Re-run the focused tests before moving to the next story.

## Implementation Strategy

### MVP First

1. Complete Phase 1.
2. Complete Phase 2 and verify the early live-capable record path.
3. Re-run the targeted adapter/router tests.
4. Continue with session-event preservation and failure cleanup.

### Notes

- Keep the change inside existing adapter/controller/router boundaries.
- Do not add compatibility wrappers for old task-run persistence behavior.
- Preserve non-fatal observability publication semantics.
