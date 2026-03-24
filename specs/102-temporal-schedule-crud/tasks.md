# Tasks: Temporal Schedule CRUD

**Feature**: `102-temporal-schedule-crud`
**Branch**: `102-temporal-schedule-crud`
**Spec**: [spec.md](spec.md)
**Plan**: [plan.md](plan.md)

## Phase 1: Setup

- [x] T001 Create exception module at `moonmind/workflows/temporal/schedule_errors.py` with `ScheduleAdapterError`, `ScheduleNotFoundError`, `ScheduleAlreadyExistsError`, `ScheduleOperationError` (DOC-REQ-007)

## Phase 2: Foundational — Policy Mapping

- [x] T002 Create policy mapping module at `moonmind/workflows/temporal/schedule_mapping.py` with `map_overlap_policy()` mapping `skip`→`SKIP`, `allow`→`ALLOW_ALL`, `buffer_one`→`BUFFER_ONE`, `cancel_previous`→`CANCEL_OTHER` (DOC-REQ-003)
- [x] T003 [P] Add `map_catchup_window()` to `moonmind/workflows/temporal/schedule_mapping.py` mapping `none`→`timedelta(0)`, `last`→`timedelta(minutes=15)`, `all`→`timedelta(days=365)` (DOC-REQ-004)
- [x] T004 [P] Add `build_schedule_spec()` to `moonmind/workflows/temporal/schedule_mapping.py` that constructs `ScheduleSpec` from cron, timezone, and `jitter_seconds` → `timedelta` (DOC-REQ-006)
- [x] T005 [P] Add `build_schedule_policy()` to `moonmind/workflows/temporal/schedule_mapping.py` that constructs `SchedulePolicy` using `map_overlap_policy()` and `map_catchup_window()` (DOC-REQ-003, DOC-REQ-004)
- [x] T006 [P] Add `build_schedule_state()` to `moonmind/workflows/temporal/schedule_mapping.py` that constructs `ScheduleState` from `enabled` and `note` parameters
- [x] T007 [P] Add `make_schedule_id()` and `make_workflow_id_template()` to `moonmind/workflows/temporal/schedule_mapping.py` following convention `mm-schedule:{uuid}` and `mm:{uuid}:{epoch}` (DOC-REQ-005)
- [x] T008 Create unit tests at `tests/unit/workflows/temporal/test_schedule_mapping.py` covering all mapping functions, each overlap mode, each catchup mode, jitter, and ID generation (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)

## Phase 3: User Story 1 — Create Schedule (P1)

- [x] T009 [US1] Add `create_schedule()` method to `TemporalClientAdapter` in `moonmind/workflows/temporal/client.py` using `schedule_mapping` to build `Schedule` object and wrapping SDK exceptions in `ScheduleAlreadyExistsError`/`ScheduleOperationError` (DOC-REQ-001, DOC-REQ-007)
- [x] T010 [US1] Add unit tests for `create_schedule()` in `tests/unit/workflows/temporal/test_client_schedules.py` verifying correct `Schedule` construction with mocked Temporal client (DOC-REQ-001)

## Phase 4: User Story 2 — Schedule Lifecycle Management (P1)

- [x] T011 [US2] Add `describe_schedule()` method to `TemporalClientAdapter` in `moonmind/workflows/temporal/client.py` returning `ScheduleDescription`, wrapping `ScheduleNotFoundError` (DOC-REQ-002, DOC-REQ-007)
- [x] T012 [P] [US2] Add `update_schedule()` method to `TemporalClientAdapter` in `moonmind/workflows/temporal/client.py` using `handle.update()` callback pattern (DOC-REQ-002)
- [x] T013 [P] [US2] Add `pause_schedule()` and `unpause_schedule()` methods to `TemporalClientAdapter` in `moonmind/workflows/temporal/client.py` (DOC-REQ-002)
- [x] T014 [P] [US2] Add `trigger_schedule()` method to `TemporalClientAdapter` in `moonmind/workflows/temporal/client.py` (DOC-REQ-002)
- [x] T015 [P] [US2] Add `delete_schedule()` method to `TemporalClientAdapter` in `moonmind/workflows/temporal/client.py` (DOC-REQ-002)
- [x] T016 [US2] Add unit tests for `describe_schedule()`, `update_schedule()`, `pause_schedule()`, `unpause_schedule()`, `trigger_schedule()`, `delete_schedule()` in `tests/unit/workflows/temporal/test_client_schedules.py` with mocked `ScheduleHandle` (DOC-REQ-002)

## Phase 5: User Story 3 — Error Handling (P2)

- [x] T017 [US3] Add unit tests for SDK error wrapping in `tests/unit/workflows/temporal/test_client_schedules.py`: mock Temporal client to raise `RPCError` and verify `ScheduleOperationError`; mock not-found to verify `ScheduleNotFoundError` (DOC-REQ-007)

## Phase 6: Polish & Cross-Cutting

- [x] T018 Run `./tools/test_unit.sh` and verify all new and existing tests pass
- [x] T019 Verify no raw `temporalio` exceptions leak past adapter boundary by reviewing all catch blocks in `client.py`

## Dependencies

```text
T001 ──→ T002-T007 ──→ T008
                   ──→ T009 ──→ T010
                   ──→ T011-T015 ──→ T016
                                ──→ T017
T018 depends on all prior tasks
T019 depends on all prior tasks
```

## Parallel Execution Opportunities

- T002-T007 can all be written in one file (`schedule_mapping.py`) but are logically independent
- T011-T015 can be implemented in parallel (independent methods on same class)
- T008, T010, T016, T017 are test tasks that can run after their implementation dependencies

## Implementation Strategy

**MVP**: T001 → T002-T007 → T008 → T009-T010 (schedule creation with policy mapping)
**Full scope**: All 19 tasks ✅ COMPLETE

## Task Summary

| Phase | Tasks | Parallelizable | Status |
|---|---|---|---|
| Setup | 1 | — | ✅ |
| Foundational | 7 | T003-T007 | ✅ |
| US1: Create | 2 | — | ✅ |
| US2: Lifecycle | 6 | T012-T015 | ✅ |
| US3: Errors | 1 | — | ✅ |
| Polish | 2 | — | ✅ |
| **Total** | **19** | | **✅ ALL COMPLETE** |

## DOC-REQ Coverage

| DOC-REQ | Implementation Tasks | Validation Tasks | Status |
|---|---|---|---|
| DOC-REQ-001 | T009 | T010 | ✅ |
| DOC-REQ-002 | T011, T012, T013, T014, T015 | T016 | ✅ |
| DOC-REQ-003 | T002, T005 | T008 | ✅ |
| DOC-REQ-004 | T003, T005 | T008 | ✅ |
| DOC-REQ-005 | T007 | T008 | ✅ |
| DOC-REQ-006 | T004 | T008 | ✅ |
| DOC-REQ-007 | T001, T009 | T017 | ✅ |
