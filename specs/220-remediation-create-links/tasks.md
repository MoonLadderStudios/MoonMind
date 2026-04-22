# Tasks: Remediation Create Links

**Input**: Design documents from `/specs/220-remediation-create-links/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests are required first. Integration coverage is represented by FastAPI router and Temporal service boundary tests for this persistence slice; no compose-backed integration service is introduced.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py` covers the API/service boundary for this persistence slice; `./tools/test_integration.sh` is not required because no compose-backed boundary is introduced.
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-431 traceability in `specs/220-remediation-create-links/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-create-links.md`, and `quickstart.md`.

## Phase 2: Foundational

- [X] T002 Add the durable remediation link model in `api_service/db/models.py` for FR-003, FR-005, DESIGN-REQ-003.
- [X] T003 Add the Alembic migration for `execution_remediation_links` in `api_service/migrations/versions/219_remediation_create_links.py` for FR-003.

## Phase 3: Persist Remediation Targets

**Summary**: Accept `task.remediation` create requests and persist target links.

**Independent Test**: Create a target execution, create a remediation execution pointing at it, then assert the normalized payload and persisted inbound/outbound link.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-024.

**Unit Test Plan**: Add red-first service tests for create-time validation, run ID pinning, persistence, lookup direction, dependency separation, authority/action policy validation, taskRunIds shape validation, nested remediation rejection, and compact link metadata.

**Integration Test Plan**: Add red-first FastAPI router/API-boundary tests proving task-shaped create preserves `task.remediation` and the remediation convenience route expands into the same canonical create contract. No compose-backed integration test is required for this persistence-only slice.

- [X] T004 Add failing service tests for valid remediation link creation, run ID pinning, inbound lookup, outbound lookup, and no dependency edges in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-002, FR-003, FR-005, FR-006, FR-007.
- [X] T005 Add failing service validation tests for missing target workflow ID, run ID identifier, missing target, non-run target, and mismatched target run ID in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-004.
- [X] T006 Add a failing task-shaped router test proving `payload.task.remediation` is preserved into `initial_parameters.task.remediation` in `tests/unit/api/routers/test_executions.py` for FR-001.
- [X] T007 Add failing API-boundary integration-style router test proving `POST /api/executions/{workflowId}/remediation` expands into canonical task-shaped create in `tests/unit/api/routers/test_executions.py` for FR-012 and DESIGN-REQ-005.
- [X] T008 Add failing service validation tests for unsupported `authorityMode`, unsupported `actionPolicyRef`, malformed `target.taskRunIds`, and nested remediation targets in `tests/unit/workflows/temporal/test_temporal_service.py` for FR-008, FR-009, FR-010, FR-011, and DESIGN-REQ-004.
- [X] T009 Run focused tests and confirm red-first failures before production changes in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/api/routers/test_executions.py` for SC-001, SC-002, SC-003, SC-004, SC-005, and SC-006.
- [X] T010 Add nullable remediation link action/outcome fields in `api_service/db/models.py` and `api_service/migrations/versions/223_remediation_link_status_fields.py` for FR-003 and DESIGN-REQ-024.
- [X] T011 Implement remediation target validation, run ID resolution, and link persistence in `moonmind/workflows/temporal/service.py` for FR-002, FR-003, FR-004, FR-005.
- [X] T012 Implement bounded remediation validation in `moonmind/workflows/temporal/service.py` for FR-008, FR-009, FR-010, and FR-011.
- [X] T013 Implement remediation inbound and outbound lookup methods in `moonmind/workflows/temporal/service.py` for FR-006.
- [X] T014 Preserve `payload.task.remediation` in task-shaped create normalization in `api_service/api/routers/executions.py` for FR-001.
- [X] T015 Implement remediation convenience route expansion in `api_service/api/routers/executions.py` for FR-012.
- [X] T016 Run story validation with focused unit and API-boundary tests from `specs/220-remediation-create-links/quickstart.md` for SC-001, SC-002, SC-003, SC-004, SC-005, and SC-006.

## Final Phase: Polish And Verification

- [X] T017 Run relevant unit and API-boundary verification from `specs/220-remediation-create-links/quickstart.md`.
- [X] T018 Run `/moonspec-verify` by auditing implementation against `specs/220-remediation-create-links/spec.md` and recording the result in `specs/220-remediation-create-links/verification.md`.
