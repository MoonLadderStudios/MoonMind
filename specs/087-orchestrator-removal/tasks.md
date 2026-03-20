---
description: "Task list — remove mm-orchestrator (087)"
---

# Tasks: Remove mm-orchestrator

**Input**: Design documents from `/specs/087-orchestrator-removal/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Phase 1: Setup

- [X] T001 Add `tests/unit/orchestrator_removal/test_doc_req_coverage.py` with guard tests for DOC-REQ-001–012 (compose, imports, routes)

## Phase 2: Foundational

- [X] T002 Inventory orchestrator FK order from `api_service/db/models.py` for Alembic drop order (DOC-REQ-011)

## Phase 3: User Story 1 — Compose without orchestrator (P1)

**Goal**: Default stack has no orchestrator service or test-only orchestrator service.

- [X] T003 [US1] DOC-REQ-001: Remove `orchestrator` service block from `docker-compose.yaml`
- [X] T004 [US1] DOC-REQ-002: Remove `orchestrator-tests` from `docker-compose.test.yaml`
- [X] T005 [US1] DOC-REQ-003: Remove orchestrator-only env vars from `docker-compose.yaml` / `docker-compose.test.yaml`
- [X] T006 [P] [US1] DOC-REQ-007: Remove `docker-compose.job.yaml` (or ensure no `orchestrator` service if reintroduced)
- [X] T007 [US1] DOC-REQ-004: Delete `api_service/api/routers/orchestrator.py` and unregister router in `api_service/api/main.py`
- [X] T101 [US1] Validate DOC-REQ-001: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_001_compose_main_no_orchestrator_service`
- [X] T102 [US1] Validate DOC-REQ-002: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_002_test_compose_no_orchestrator_tests`
- [X] T103 [US1] Validate DOC-REQ-003: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_003_no_orchestrator_env_in_compose`
- [X] T104 [US1] Validate DOC-REQ-004: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_004_api_has_no_orchestrator_router`
- [X] T107 [US1] Validate DOC-REQ-007 (job compose): `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_007_job_yaml_no_orchestrator_service`

## Phase 4: User Story 2 — Code and schema removal (P2)

**Goal**: No orchestrator workflows, models, or tables.

- [X] T008 [US2] DOC-REQ-005: Delete package `moonmind/workflows/orchestrator/` (all modules)
- [X] T009 [US2] DOC-REQ-006: Remove Orchestrator ORM classes and enums from `api_service/db/models.py`
- [X] T010 [US2] DOC-REQ-006: Remove orchestrator Pydantic models/enums from `moonmind/schemas/workflow_models.py` and fix exports
- [X] T011 [US2] DOC-REQ-006: Remove orchestrator branches from `moonmind/workflows/tasks/compatibility.py`
- [X] T012 [US2] DOC-REQ-011: Add Alembic revision under `api_service/migrations/versions/` to drop orchestrator tables safely
- [X] T013 [P] [US2] Grep-fix remaining imports (e.g. `celery_worker/`, `moonmind/workflows/tasks/`) referencing `workflows.orchestrator` or removed models
- [X] T105 [US2] Validate DOC-REQ-005: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_005_workflows_orchestrator_package_removed`
- [X] T106 [US2] Validate DOC-REQ-006: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_006_no_orchestrator_models_importable`
- [X] T108 [US2] Validate DOC-REQ-011: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_011_migration_drops_orchestrator_tables`

## Phase 5: User Story 3 — Tests, CI, dashboard, docs (P3)

**Goal**: No orchestrator in CI, dashboard, or architecture docs.

- [X] T014 [US3] DOC-REQ-008: Delete `tests/integration/orchestrator/`, `tests/unit/workflows/orchestrator/`, `tests/contract/test_orchestrator_api.py`, `.github/workflows/orchestrator-integration-tests.yml`
- [X] T015 [US3] DOC-REQ-009: Update `tools/test-integration.ps1`, `tests/task_dashboard/test_submit_runtime.js`, `tests/unit/api/routers/test_task_compatibility.py` for orchestrator removal
- [X] T016 [US3] DOC-REQ-009: Strip orchestrator runtime from `api_service/static/task_dashboard/dashboard.js` (routes, API calls, forms)
- [X] T017 [US3] DOC-REQ-010: Remove/archive `docs/Temporal/OrchestratorTaskRuntime.md`, `docs/Temporal/OrchestratorArchitecture.md`; update `docs/MoonMindArchitecture.md`, `docs/Temporal/TemporalArchitecture.md`
- [X] T018 [US3] DOC-REQ-010: Delete `specs/005-orchestrator-architecture/` and `specs/050-orchestrator-task-runtime/`
- [X] T019 [US3] DOC-REQ-007: Remove `services/orchestrator/` directory when unused (validate: `test_doc_req_services_orchestrator_dir_removed`)
- [X] T109 [US3] Validate DOC-REQ-008: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_008_no_orchestrator_ci_paths`
- [X] T110 [US3] Validate DOC-REQ-009: `./tools/test_unit.sh` + `tests/unit/api/routers/test_task_compatibility.py`
- [X] T111 [US3] Validate DOC-REQ-010: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_010_docs_specs_clean`
- [X] T112 [US3] Validate DOC-REQ-012: `./tools/test_unit.sh` + `tests/unit/orchestrator_removal/test_doc_req_coverage.py::test_doc_req_012_unit_suite_passes`

## Phase 6: Polish

- [X] T020 Update `AGENTS.md` or root docs that reference `orchestrator-tests` docker flow if present
- [X] T021 Grep repo for `orchestrator_run`, `moonmind.workflows.orchestrator`, `/orchestrator/tasks` and fix stragglers under `moonmind/`, `api_service/`

## Dependencies

- T002 before T012 (migration order)
- T008–T011 before T012 (models removed before migration reflects drops)
- T007–T011 before T016 (API behavior known before dashboard cut)
- US1 before US2 before US3 recommended

## Parallel execution examples

- T006 parallel with T003–T005 after plan review
- T013 parallel with test file updates after T008–T011

## Implementation strategy

Remove backend surfaces first, then migration, then UI and docs, then global grep cleanup.
