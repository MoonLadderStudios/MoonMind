# Tasks: Persist Authoritative Task Snapshots

**Input**: `specs/323-persist-task-snapshots/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/execution-reconstruction.md`, `quickstart.md`
**Prerequisites**: Existing execution route tests and Temporal service tests are available.
**Unit test command**: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
**Integration test command**: `./tools/test_integration.sh` only if implementation expands beyond API action capability serialization.

## Source Traceability

The original MM-629 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-009, acceptance scenarios 1-5, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-011. Plan status summary: FR-002, FR-006, and FR-008 were partial before implementation because parameter-derived fallback enabled edit/rerun when the authoritative snapshot was missing; T005-T013 now record the test-first remediation and verification work for that bounded story.

## Story

As a user retrying or reviewing a task, I want MoonMind to reconstruct the original authored task from a durable snapshot so edit, rerun, and resume flows cannot lose task intent.

**Independent Test**: Submit or serialize representative terminal task executions with and without `task_input_snapshot_ref`; verify edit/rerun/resume actions are enabled only when the authoritative snapshot and required resume checkpoint exist, and missing snapshots are reported as degraded or unavailable rather than reconstructed from parameters.

## Phase 1: Setup

- [X] T001 Confirm MM-629 is a single-story runtime feature and no existing `specs/*` directory already owns MM-629 in `specs/323-persist-task-snapshots/spec.md` (FR-009, SC-006)
- [X] T002 Create MoonSpec specify, plan, research, data model, contract, and quickstart artifacts under `specs/323-persist-task-snapshots/` (FR-009, SC-006)

## Phase 2: Foundational

- [X] T003 Identify execution action capability and task snapshot descriptor boundaries in `api_service/api/routers/executions.py` and `moonmind/schemas/temporal_models.py` (FR-002, FR-006, FR-008)
- [X] T004 Identify existing snapshot persistence and resume validation evidence in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `moonmind/workflows/temporal/service.py` (FR-001, FR-003, FR-004, FR-005, FR-007)

## Phase 3: Authoritative Task Snapshot Reconstruction

### Unit Test Plan

Update `tests/unit/api/routers/test_executions.py` so terminal `MoonMind.Run` executions without `task_input_snapshot_ref` disable `canEditForRerun` and `canRerun` even when task parameters contain instructions, steps, tool data, or skill data. Preserve tests proving snapshots enable edit/rerun and resume remains checkpoint-gated.

### Integration Test Plan

No new hermetic integration test is required for the scoped code change because the behavior is a deterministic API serialization policy already covered by unit route/model tests. Run full unit verification before finalizing.

- [X] T005 Add/update failing unit coverage in `tests/unit/api/routers/test_executions.py` for terminal task parameters without authoritative snapshots disabling edit/rerun (FR-002, FR-006, FR-008, DESIGN-REQ-001, DESIGN-REQ-010)
- [X] T006 Add/update unit coverage in `tests/unit/api/routers/test_executions.py` confirming missing snapshots report `original_task_input_snapshot_missing` for edit/rerun despite reconstructable parameters (FR-006, FR-008)
- [X] T007 Run targeted red-first test command `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and confirm the updated test fails before production edits (FR-002, FR-006, FR-008)
- [X] T008 Remove parameter-derived edit/rerun fallback from `_build_action_capabilities()` in `api_service/api/routers/executions.py` (FR-002, FR-006, FR-008)
- [X] T009 Remove now-unused reconstructable-parameter helper code in `api_service/api/routers/executions.py` if no callers remain (FR-006, DESIGN-REQ-001)
- [X] T010 Run targeted unit verification `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and require PASS (FR-001 through FR-008)
- [X] T011 Review `taskInputSnapshot` descriptor output against `contracts/execution-reconstruction.md` for authoritative/degraded/unavailable states (FR-008)

## Final Phase: Polish And Verification

- [X] T012 Run full unit suite `./tools/test_unit.sh` before final verification (SC-005)
- [X] T013 Run `/moonspec-verify` equivalent and write `specs/323-persist-task-snapshots/verification.md` with verdict, evidence, and MM-629 traceability (FR-009, SC-006)

## Dependencies And Execution Order

1. T001-T004 establish artifacts and code boundaries.
2. T005-T007 must complete before production code edits.
3. T008-T009 implement the scoped policy change.
4. T010-T013 validate and verify the story.

## Implementation Strategy

Close only the unsafe fallback gap identified in the plan. Do not change snapshot payload schemas, artifact storage, resume checkpoint models, frontend UI, or Temporal workflow payload shapes unless targeted tests expose a direct requirement gap.
