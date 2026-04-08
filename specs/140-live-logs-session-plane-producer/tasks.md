# Tasks: Live Logs Session Plane Producer

**Input**: Design documents from `/specs/140-live-logs-session-plane-producer/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: This feature is explicitly TDD-driven. Write or update failing tests before the corresponding implementation tasks.

**Organization**: Tasks are grouped by user story so each slice can be implemented and validated independently.

## Phase 1: Setup (Shared Test Baseline)

**Purpose**: Establish failing tests for the missing Phase 2 observability-producer behavior before runtime code is updated.

- [X] T001 [P] Add controller coverage for resume, steer, terminate, and non-fatal event publication behavior in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- [X] T002 [P] Add supervisor coverage for summary/checkpoint publication rows and non-fatal event publication behavior in `tests/unit/services/temporal/runtime/test_managed_session_supervisor.py`
- [X] T003 [P] Add adapter coverage for `start_session` versus `resume_session` control signaling in `tests/unit/workflows/adapters/test_codex_session_adapter.py`

---

## Phase 2: Foundational (Blocking Producer Changes)

**Purpose**: Wire the missing event-production and control-signaling boundaries used by every Phase 2 story.

**⚠️ CRITICAL**: User-story implementation should not begin until these producer changes are complete.

- [X] T004 Implement best-effort session-event emission and missing resume/steer/terminate rows in `moonmind/workflows/temporal/runtime/managed_session_controller.py`
- [X] T005 Implement summary/checkpoint publication rows and best-effort supervisor emission in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`
- [X] T006 Implement `start_session` / `resume_session` workflow control signaling in `moonmind/workflows/adapters/codex_session_adapter.py`

**Checkpoint**: The controller, supervisor, and adapter all emit or signal the missing Phase 2 session-plane observability facts.

---

## Phase 3: User Story 1 - Session lifecycle controls publish timeline-visible events (Priority: P1)

**Goal**: Make resume, steer, interrupt, clear, and termination visible in the run-global timeline.

**Independent Test**: Exercise the controller and adapter tests to confirm the normalized rows and control signals are emitted without breaking successful control actions.

### Implementation for User Story 1

- [X] T007 [US1] Emit `session_resumed` on runtime-handle reuse and signal `resume_session` from the adapter/controller boundary
- [X] T008 [US1] Emit normalized steering and termination rows while preserving existing clear/reset and interrupt behavior
- [X] T009 [US1] Make observability publication failures non-fatal to successful control actions

**Checkpoint**: Lifecycle control actions are visible in Live Logs as session-aware facts rather than hidden controller state changes.

---

## Phase 4: User Story 2 - Session artifact publication becomes visible in the observability timeline (Priority: P1)

**Goal**: Make summary/checkpoint publication rows part of the same durable timeline as other session events.

**Independent Test**: Publish session artifacts and confirm `summary_published` and `checkpoint_published` appear in durable observability history.

### Implementation for User Story 2

- [X] T010 [US2] Emit `summary_published` and `checkpoint_published` during managed-session publication in `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`

**Checkpoint**: Continuity publication is visible inline in the session-aware observability stream.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Verify the feature end to end and close the execution loop.

- [X] T011 Run focused Phase 2 verification in `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_managed_session_supervisor.py tests/unit/workflows/adapters/test_codex_session_adapter.py`
- [X] T012 Run runtime scope validation in `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on the failing tests from Phase 1.
- **User Story 1 (Phase 3)**: Depends on Phase 2.
- **User Story 2 (Phase 4)**: Depends on Phase 2.
- **Polish (Phase 5)**: Depends on all implementation phases completing.

### Parallel Opportunities

- `T001`, `T002`, and `T003` can run in parallel because they touch different test files.
- `T004`, `T005`, and `T006` can proceed independently after the tests are in place.

## Implementation Strategy

### MVP First

1. Add failing tests for the missing Phase 2 producer behavior.
2. Wire the controller and supervisor emission boundaries.
3. Wire adapter control signaling for session start/resume.
4. Run the focused verification and scope checks.

### Notes

- Keep the Phase 1 `RunObservabilityEvent` contract unchanged.
- Keep reset-boundary semantics explicit and session-plane publishing best-effort.
- Do not introduce provider-native event rows or a second session-event model.
