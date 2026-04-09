# Tasks: Live Logs History Events

**Input**: Design documents from `/specs/141-live-logs-history-events/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: This feature is explicitly TDD-driven. Write or update failing tests before the corresponding implementation tasks.

**Organization**: Tasks are grouped by user story so each slice can be implemented and validated independently.

## Phase 1: Setup (Shared Test Baseline)

**Purpose**: Establish failing tests for the missing Phase 3 observability-history contract before router code is updated.

- [X] T001 Add failing history-query coverage for `since`, `limit`, stream filters, kind filters, and durable-source preference in `tests/unit/api/routers/test_task_runs.py`
- [X] T002 Add failing summary and SSE-compatibility coverage for session snapshot truthfulness and canonical event serialization in `tests/unit/api/routers/test_task_runs.py`

---

## Phase 2: Foundational (Blocking Router Helpers)

**Purpose**: Add the shared filtering and response-shaping helpers that every Phase 3 story depends on.

**⚠️ CRITICAL**: User-story implementation should not begin until these shared router helpers are complete.

- [X] T003 Implement reusable historical-event filtering and limiting helpers in `api_service/api/routers/task_runs.py`

**Checkpoint**: The router has one shared helper layer for filtered historical event retrieval.

---

## Phase 3: User Story 1 - Query durable observability history for the timeline (Priority: P1)

**Goal**: Make `/observability/events` a complete structured-history contract instead of a fixed unfiltered dump.

**Independent Test**: Request historical events with mixed streams and kinds and confirm the API honors `since`, `limit`, and the optional filters while preserving durable-source priority.

### Implementation for User Story 1

- [X] T004 [US1] Implement `since`, stream, and kind query handling for `/api/task-runs/{id}/observability/events` in `api_service/api/routers/task_runs.py`
- [X] T005 [US1] Preserve explicit event-journal -> spool -> artifact fallback ordering in `api_service/api/routers/task_runs.py`

**Checkpoint**: Historical timeline consumers can request bounded, filtered, canonical event history.

---

## Phase 4: User Story 2 - Keep summary and live streaming aligned with the same observability contract (Priority: P1)

**Goal**: Keep summary and SSE truthful and compatible with the historical event contract.

**Independent Test**: Load summary for active and completed runs and confirm `/logs/stream` still serializes canonical event rows without a schema mismatch.

### Implementation for User Story 2

- [X] T006 [US2] Implement session snapshot precedence and truthful live-stream status shaping in `api_service/api/routers/task_runs.py`
- [X] T007 [US2] Preserve canonical `RunObservabilityEvent` serialization for `/api/task-runs/{id}/logs/stream` in `api_service/api/routers/task_runs.py`

**Checkpoint**: Summary and live follow remain consistent with the structured-history contract.

---

## Phase 5: User Story 3 - Preserve compatibility and fallback ordering across observability surfaces (Priority: P2)

**Goal**: Keep current merged fallback and authorization behavior stable while the structured-history contract becomes the preferred source.

**Independent Test**: Exercise summary, structured history, and merged fallback together for runs with and without durable event journals and confirm access control remains consistent.

### Implementation for User Story 3

- [X] T008 [US3] Extend compatibility and fallback coverage for `/observability/events`, `/observability-summary`, and `/logs/merged` in `tests/unit/api/routers/test_task_runs.py`

**Checkpoint**: Older runs still degrade cleanly and authorized consumers retain the same observability access.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify the feature end to end and close the execution loop.

- [X] T009 Run focused Phase 3 verification in `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py`
- [X] T010 Run runtime scope validation in `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
- [X] T011 Run full regression verification in `./tools/test_unit.sh`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on the failing tests from Phase 1.
- **User Story 1 (Phase 3)**: Depends on Phase 2.
- **User Story 2 (Phase 4)**: Depends on Phase 2 and can land after the shared filtering helpers are in place.
- **User Story 3 (Phase 5)**: Depends on Phase 3 and Phase 4 because it validates the combined compatibility behavior.
- **Polish (Phase 6)**: Depends on all implementation phases completing.

### Parallel Opportunities

- `T001` and `T002` can be staged together as one TDD pass before implementation begins.
- `T004` and `T006` can be developed in sequence on the same router file but validated independently by story.

## Implementation Strategy

### MVP First

1. Add the failing router tests for historical query behavior, summary truthfulness, and SSE compatibility.
2. Land the shared filtering helper.
3. Complete User Story 1 so historical retrieval is a real structured contract.
4. Complete User Story 2 so summary and SSE remain aligned with that contract.
5. Validate compatibility behavior for User Story 3, then run the required verification gates.

### Notes

- Keep `/observability/events` as the canonical historical route.
- Keep `/logs/merged` stable as the human-readable fallback surface.
- Do not add a second observability event model or a Phase 3-only route family.
