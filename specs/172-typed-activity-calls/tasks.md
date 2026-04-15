# Tasks: Typed Temporal Activity Calls

**Input**: `/specs/172-typed-activity-calls/spec.md`, `/specs/172-typed-activity-calls/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/temporal-activity-boundary.md`, `quickstart.md`
**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available; targeted Temporal test-worker unit coverage is required for this story.

## Source Traceability Summary

- DESIGN-REQ-004: T001, T004, T008, T013
- DESIGN-REQ-006: T010, T011, T014
- DESIGN-REQ-009: T002, T003, T005, T006, T009, T012
- DESIGN-REQ-010: T006, T010, T011, T014
- DESIGN-REQ-011: T002, T007, T008, T012

## Story Summary

Enforce shared typed conversion and typed activity calls for representative managed/external agent runtime Temporal activity boundaries.

## Independent Test

Run the targeted tests in `quickstart.md` and confirm typed request models are serialized through Temporal and workflow call sites receive canonical MoonMind models.

## Phase 1: Setup

- [X] T001 Create Moon Spec feature artifacts in `specs/172-typed-activity-calls/` for MM-328 traceability.

## Phase 2: Foundational

- [X] T002 Add strict typed activity request models in `moonmind/schemas/temporal_activity_models.py` for external run identifiers and managed runtime status/fetch/cancel payloads. [FR-003, FR-008]
- [X] T003 Add or update typed execution overloads in `moonmind/workflows/temporal/typed_execution.py` for migrated runtime activity names. [FR-005]
- [X] T004 Add shared Temporal data converter contract in `moonmind/workflows/temporal/data_converter.py` and use it from `moonmind/workflows/temporal/client.py`. [FR-001, FR-002]

## Phase 3: Story

- [X] T005 [P] Add failing converter contract tests in `tests/unit/workflows/temporal/test_temporal_client.py`. [SC-001]
- [X] T006 [P] Add failing typed boundary tests in `tests/unit/workflows/temporal/test_typed_activity_boundaries.py` for strict validation and Temporal round-trip. [SC-002, SC-004]
- [X] T007 [P] Add failing activity runtime validation tests in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`. [FR-008]
- [X] T008 [P] Add failing AgentRun call-site payload tests in `tests/unit/workflows/temporal/workflows/test_agent_run_jules_execution.py`. [SC-003]
- [X] T009 Run targeted tests and confirm the new tests fail before production edits.
- [X] T010 Update `moonmind/workflows/temporal/workflows/agent_run.py` to construct typed request models and route migrated calls through `execute_typed_activity`. [FR-004, FR-005, FR-006, FR-007]
- [X] T011 Update `moonmind/workflows/temporal/activity_runtime.py` to validate retained legacy dicts into typed models at public activity edges. [FR-006, FR-008]
- [X] T012 Update any affected existing tests/mocks to use typed request models where they inspect migrated call-site payloads. [FR-004, FR-005]
- [X] T013 Run targeted tests from `quickstart.md` and fix regressions.
- [X] T014 Run final unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record exact blocker.
- [X] T015 Run `/speckit.verify` equivalent read-only verification and record verdict.

## Dependencies and Execution Order

1. T001-T004 establish artifacts and shared contracts.
2. T005-T009 create red-first evidence.
3. T010-T012 implement and update tests.
4. T013-T015 verify.

## Parallel Examples

- T005, T006, T007, and T008 can be authored in parallel because they touch separate test files.

## Implementation Strategy

Keep stable Temporal activity names. Add typed models at the schema boundary, validate compatibility payloads at activity entry, and make workflow call sites construct canonical request models before invoking the typed execution facade.
