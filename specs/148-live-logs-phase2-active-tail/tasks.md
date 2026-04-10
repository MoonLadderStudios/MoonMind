# Tasks: Live Logs Phase 2 Active Tail

**Input**: Design documents from `/specs/148-live-logs-phase2-active-tail/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/active-tail.md, quickstart.md

**Tests**: Tests are required because the user explicitly requested test-driven development.

**Organization**: Tasks are grouped by user story so journal rendering, summary metadata, and stream-contract preservation can be validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story the task traces to (`US1`, `US2`, `US3`)

## Phase 1: Setup and Scope Guard

**Purpose**: Confirm local router contracts and runtime scope before implementation.

- [X] T001 Review existing merged-log, observability-summary, structured-history, and SSE helpers in `api_service/api/routers/task_runs.py` and current router tests in `tests/unit/api/routers/test_task_runs.py`.
- [X] T002 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.

---

## Phase 2: User Story 1 - Refresh Shows Active Live Content (Priority: P1) 🎯 MVP

**Goal**: Make `/logs/merged` render active journal content first, with spool fallback when the journal is unavailable.

**Independent Test**: Active managed-run records with journal or spool content return useful merged text without final artifacts.

### Tests for User Story 1

- [X] T003 [P] [US1] Add failing router coverage in `tests/unit/api/routers/test_task_runs.py` proving `/logs/merged` renders valid `observabilityEventsRef` journal rows before final artifacts.
- [X] T004 [P] [US1] Add failing router coverage in `tests/unit/api/routers/test_task_runs.py` proving `/logs/merged` falls back to `live_streams.spool` when the journal reference is missing, invalid, or empty.
- [X] T005 [P] [US1] Add failing router coverage in `tests/unit/api/routers/test_task_runs.py` proving malformed journal/spool rows are ignored while valid rows still render.

### Implementation for User Story 1

- [X] T006 [US1] Implement journal-to-merged rendering helpers in `api_service/api/routers/task_runs.py` using normalized `RunObservabilityEvent` rows and run-global sequence ordering.
- [X] T007 [US1] Update `/logs/merged` source preference in `api_service/api/routers/task_runs.py` to journal, spool, final merged artifact, legacy artifact, then split artifact fallback.

**Checkpoint**: Refreshing an active run can show recent content before SSE succeeds.

---

## Phase 3: User Story 2 - Summary Carries the Session Snapshot (Priority: P1)

**Goal**: Keep `/observability-summary` populated with session snapshot fields and `observabilityEventsRef` for active and terminal runs.

**Independent Test**: Records with session metadata expose that metadata through summary regardless of terminal state.

### Tests for User Story 2

- [X] T008 [P] [US2] Add or tighten router assertions in `tests/unit/api/routers/test_task_runs.py` for active summary `observabilityEventsRef` and record-derived `sessionSnapshot`.
- [X] T009 [P] [US2] Add or tighten router assertions in `tests/unit/api/routers/test_task_runs.py` for terminal summary `liveStreamStatus=ended` with retained `sessionSnapshot`.

### Implementation for User Story 2

- [X] T010 [US2] Adjust summary snapshot handling in `api_service/api/routers/task_runs.py` only if tests reveal missing or inconsistent record-derived fields.

**Checkpoint**: Summary remains the current UI's compact source for live status and session identity.

---

## Phase 4: User Story 3 - Current SSE Lifecycle Remains Truthful (Priority: P2)

**Goal**: Preserve stream endpoint status behavior while active merged history improves.

**Independent Test**: Active capable runs stream, active incapable runs return 400, and terminal runs return 410.

### Tests for User Story 3

- [X] T011 [P] [US3] Add or tighten router coverage in `tests/unit/api/routers/test_task_runs.py` proving `/logs/stream` availability status is unchanged for active capable, active incapable, and terminal runs.

### Implementation for User Story 3

- [X] T012 [US3] Keep `/logs/stream` logic in `api_service/api/routers/task_runs.py` behaviorally unchanged while integrating any helper refactors needed by merged rendering.

**Checkpoint**: Existing SSE consumers are not regressed.

---

## Phase 5: Validation and Closeout

**Purpose**: Verify focused runtime behavior and close the Spec Kit loop.

- [X] T013 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py`.
- [X] T014 Mark completed tasks as `[X]` in `specs/148-live-logs-phase2-active-tail/tasks.md`.
- [X] T015 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 blocks all work because it confirms task scope.
- Phase 2 is the MVP and should be completed before summary or stream hardening.
- Phase 3 can run after Phase 2 tests are in place because summary metadata is independent of rendering internals.
- Phase 4 runs after Phase 2 to verify helper refactors did not alter SSE behavior.
- Phase 5 runs after all implementation tasks complete.

### Within Each User Story

- Write or tighten tests first and confirm they fail for the targeted missing behavior.
- Implement the router change only after the relevant failing test exists.
- Re-run focused tests before marking tasks complete.

## Parallel Execution Examples

- T003, T004, and T005 can be written in parallel because they add distinct router test cases.
- T008, T009, and T011 can be tightened in parallel after Phase 2 tests exist.

## Implementation Strategy

### MVP First

1. Complete Phase 1.
2. Complete Phase 2 so active refreshes show journal/spool content before SSE.
3. Re-run focused router tests.
4. Complete summary and SSE preservation tests.
5. Run final validation and scope checks.

### Notes

- Do not rewrite the frontend in this phase.
- Do not introduce a new endpoint for this Phase 2 slice.
- Keep `/logs/merged` as `text/plain` so the existing UI continues to consume it unchanged.
