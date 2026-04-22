# Tasks: Remediation Create Links

**Input**: Design documents from `/specs/220-remediation-create-links/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests are required first. Integration coverage is represented by router/service boundary unit tests for this persistence slice.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`
- Integration tests: Not required for this persistence slice; no compose-backed boundary is introduced.
- Final verification: `/speckit.verify`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-431 traceability in `specs/220-remediation-create-links/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-create-links.md`, and `quickstart.md`.

## Phase 2: Foundational

- [X] T002 Add the durable remediation link model in `api_service/db/models.py` for FR-003, FR-005, DESIGN-REQ-003.
- [X] T003 Add the Alembic migration for `execution_remediation_links` in `api_service/migrations/versions/219_remediation_create_links.py` for FR-003.

## Phase 3: Persist Remediation Targets

**Summary**: Accept `task.remediation` create requests and persist target links.

**Independent Test**: Create a target execution, create a remediation execution pointing at it, then assert the normalized payload and persisted inbound/outbound link.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003.

- [X] T004 Add failing service tests for valid remediation link creation, run ID pinning, inbound lookup, outbound lookup, and no dependency edges in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-002, FR-003, FR-005, FR-006, FR-007.
- [X] T005 Add failing service validation tests for missing target workflow ID, run ID identifier, missing target, non-run target, and mismatched target run ID in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-004.
- [X] T006 Add a failing task-shaped router test proving `payload.task.remediation` is preserved into `initial_parameters.task.remediation` in `tests/unit/api/routers/test_executions.py` for FR-001.
- [X] T007 Implement remediation target validation, run ID resolution, and link persistence in `moonmind/workflows/temporal/service.py` for FR-002, FR-003, FR-004, FR-005.
- [X] T008 Implement remediation inbound and outbound lookup methods in `moonmind/workflows/temporal/service.py` for FR-006.
- [X] T009 Preserve `payload.task.remediation` in task-shaped create normalization in `api_service/api/routers/executions.py` for FR-001.
- [X] T010 Run focused unit tests and update implementation until they pass for SC-001, SC-002, SC-003, SC-004.
- [X] T013 Add failing service validation tests for unsupported `authorityMode`, unsupported `actionPolicyRef`, malformed `target.taskRunIds`, and nested remediation targets in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-008, FR-009, FR-010, and FR-011.
- [X] T014 Add failing router test for `POST /api/executions/{workflowId}/remediation` expanding into canonical task-shaped create in `tests/unit/api/routers/test_executions.py` for FR-012.
- [X] T015 Add nullable remediation link action/outcome fields in `api_service/db/models.py` and `api_service/migrations/versions/223_remediation_link_status_fields.py` for FR-003 and DESIGN-REQ-024.
- [X] T016 Implement bounded remediation validation in `moonmind/workflows/temporal/service.py` for FR-008, FR-009, FR-010, and FR-011.
- [X] T017 Implement remediation convenience route expansion in `api_service/api/routers/executions.py` for FR-012.
- [X] T018 Run focused unit tests and update implementation until they pass for SC-005 and SC-006.

## Final Phase: Polish And Verification

- [X] T011 Run relevant unit verification from `specs/220-remediation-create-links/quickstart.md`.
- [X] T012 Run `/speckit.verify` by auditing implementation against `specs/220-remediation-create-links/spec.md` and recording the result.
