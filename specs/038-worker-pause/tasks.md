# Tasks: Worker Pause System (Temporal Era)

**Feature**: `038-worker-pause` | **Branch**: `038-worker-pause`
**Spec**: [spec.md](file:///Users/nsticco/MoonMind/specs/038-worker-pause/spec.md)
**Plan**: [plan.md](file:///Users/nsticco/MoonMind/specs/038-worker-pause/plan.md)

## Phase 1: Setup & Foundation

- [x] T001 Create Temporal client helper module at `moonmind/workflows/temporal/temporal_client.py` with async functions `get_drain_metrics()` and `send_batch_signal()` (DOC-REQ-002, DOC-REQ-003)
- [x] T002 [P] Create unit tests for Temporal client helper at `tests/unit/workflows/temporal/test_temporal_client.py` (DOC-REQ-002, DOC-REQ-003)

## Phase 2: User Story 1 — Pause for Infrastructure Upgrades (P1)

- [x] T003 [US1] Add API guard in `api_service/main.py` to check `system_worker_pause_state.paused` before `temporal_client.start_workflow()` and return HTTP 503 "system paused" response when paused (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005, FR-001, FR-005)
- [x] T004 [P] [US1] Add unit test for API guard in `tests/unit/api/test_main_pause_guard.py` verifying workflow start is blocked when paused and allowed when not paused (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005, FR-001, FR-005)
- [x] T005 [US1] Update `moonmind/workflows/agent_queue/service.py` to replace legacy queue-table drain metrics with Temporal Visibility query via `temporal_client.get_drain_metrics()` (DOC-REQ-002, FR-003)
- [x] T006 [US1] Update `api_service/api/routers/system_worker_pause.py` response serialization to use Temporal-sourced drain metrics in `GET /api/system/worker-pause` (DOC-REQ-007, FR-003)
- [x] T007 [P] [US1] Update existing unit tests in `tests/unit/api/routers/test_system_worker_pause.py` for Temporal Visibility metrics integration (DOC-REQ-007, FR-003)

## Phase 3: User Story 2 — Monitor Drain Progress (P2)

- [x] T008 [US2] Verify `GET /api/system/worker-pause` returns `isDrained=true` when Temporal Visibility reports 0 running workflows (DOC-REQ-002, FR-003)
- [x] T009 [P] [US2] Add unit test verifying audit trail includes all pause/resume actions with actor, reason, mode, timestamps (DOC-REQ-010, FR-002)

## Phase 4: User Story 3 — Quiesce Mode via Temporal Batch Signals (P3)

- [x] T010 [US3] Add `send_pause_signal()` and `send_resume_signal()` methods to `moonmind/workflows/agent_queue/service.py` using `temporal_client.send_batch_signal()` with heartbeat checkpoint support (DOC-REQ-003, DOC-REQ-008, DOC-REQ-009, FR-007, FR-008)
- [x] T011 [US3] Update `POST /api/system/worker-pause` in `api_service/api/routers/system_worker_pause.py` to call `send_pause_signal()` on `action=pause, mode=quiesce` and `send_resume_signal()` on `action=resume` from quiesce (DOC-REQ-003, FR-007, FR-010)
- [x] T012 [P] [US3] Add unit tests for Batch Signal dispatch in `tests/unit/api/routers/test_system_worker_pause.py` verifying signal is sent on quiesce pause/resume (DOC-REQ-003, FR-007, FR-010)

## Phase 5: Polish & Cross-Cutting

- [x] T013 Update `api_service/api/schemas.py` if needed to align `WorkerPauseMetricsModel` field names with Temporal-sourced data (DOC-REQ-006, DOC-REQ-007, FR-009)
- [x] T013b [P] Verify Dashboard reads Temporal-sourced drain metrics from the updated API response schema in `api_service/api/schemas.py` (DOC-REQ-006, FR-009)
- [x] T014 [P] Run full test suite via `./tools/test_unit.sh` to verify no regressions
- [x] T015 Update `specs/038-worker-pause/quickstart.md` with Temporal-era validation steps (already done)
- [x] T016 Update `docs/Temporal/WorkerPauseSystem.md` if any implementation deviates from spec (already done)

## Dependencies

```text
T001 → T002 (helper tests depend on helper)
T001 → T005 (service uses helper)
T001 → T010 (service uses helper for signals)
T003 → T004 (guard test depends on guard)
T005 → T006 (router update depends on service update)
T005 → T007 (tests depend on updated service)
T010 → T011 (router depends on service signal methods)
T011 → T012 (tests depend on router changes)
```

## Parallel Execution Opportunities

- **T001 + T003**: Client helper and API guard can be built in parallel (different files)
- **T002 + T004**: Their respective tests can be written in parallel
- **T007 + T009**: Independent test additions
- **T012 + T013 + T014**: Final validation tasks are independent

## Implementation Strategy

**MVP (User Story 1 only)**: T001 → T003 → T005 → T006 → T007 delivers the core drain-mode pause with Temporal metrics and API guard. This alone provides production value.

**Full scope**: Add User Story 2 (T008-T009) and User Story 3 (T010-T012) for complete Quiesce mode support with Batch Signals.
