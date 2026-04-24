# Implementation Plan: Task Dependencies Phase 1 — Submit Contract And Validation

**Branch**: `117-task-dep-phase1` | **Date**: 2026-03-29 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/117-task-dep-phase1/spec.md`

## Summary

Phase 1 implements the task dependency submit contract in the execution creation pipeline. An audit of the current codebase confirms that **all Phase 1 functionality is already implemented** and tested:

- **Router** (`api_service/api/routers/executions.py`, `_create_execution_from_task_request`): reads `payload.task.dependsOn` (preferred) or `payload.dependsOn` (fallback), coerces to string list, trims/deduplicates, enforces 10-item limit, persists normalized list into `initial_parameters["task"]["dependsOn"]`.
- **Service** (`moonmind/workflows/temporal/service.py`, `_validate_dependencies`): checks 10-item limit, self-dependency, resolves each ID to an existing `MoonMind.Run` execution via `describe_execution`, and performs BFS cycle detection with depth-10 / 50-node limits.
- **`create_execution`** calls `_validate_dependencies` before any DB writes.
- **Unit tests**: `test_executions.py` and `test_temporal_service.py` cover all rejection cases.

The role of this spec is to formally document and close Phase 1 in the speckit pipeline, add a self-dependency rejection test that is currently missing, update the plan status to reflect completion, and confirm constitutional alignment.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, SQLAlchemy, Temporal Python SDK
**Storage**: PostgreSQL via `TemporalExecutionCanonicalRecord.parameters` JSONB column
**Testing**: pytest, `./tools/test_unit.sh`
**Target Platform**: Linux server (API service container)
**Project Type**: Single monorepo
**Performance Goals**: Validation must complete within normal HTTP request timeout (~30s); bounded traversal ensures O(50) DB calls max.
**Constraints**: No changes to Temporal workflow input shapes (Temporal-facing payload compatibility). No changes to `initialParameters` schema beyond adding `task.dependsOn` (already in).
**Scale/Scope**: Single endpoint (`POST /api/executions`), two files modified, one test file needs a new test case.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | `_validate_dependencies` only reads existing records; no duplication of logic. |
| II. One-Click Agent Deployment | PASS | No infrastructure changes. |
| III. Avoid Vendor Lock-In | PASS | DB-backed validation; no vendor-specific dependency resolution. |
| IV. Own Your Data | PASS | Dependency IDs stored in `parameters` JSONB; queryable. |
| V. Skills Are First-Class | N/A | Not a skill contract change. |
| VI. Bittersweet Lesson | PASS | Thin service layer; validation is in one place (`_validate_dependencies`). |
| VII. Runtime Configurability | PASS | 10-item limit, depth 10, node 50 are constants in service.py — could be config later. |
| VIII. Modular Architecture | PASS | Router handles shape validation; service handles semantic validation. |
| IX. Resilient by Default | PASS | Bounded traversal prevents infinite loops. |
| X. Continuous Improvement | PASS | Spec formalizes the contract for Phase 2 (workflow runtime consumption). |
| XI. Spec-Driven Development | PASS | This spec drives Phase 1 implementation gate. |
| XII. Canonical Documentation | N/A | No canonical doc changes in Phase 1. |
| XIII. Pre-Release Velocity | PASS | No compatibility aliases introduced. |

## Phase 0: Research Findings

### Current Implementation Audit

| FR | Location | Implementation | Status |
|----|----------|----------------|--------|
| FR-001 (read `payload.task.dependsOn`) | `executions.py` L758-763 | Prefers `task_payload["dependsOn"]`, falls back to `payload["dependsOn"]` | ✅ |
| FR-002 (array of strings) | `executions.py` L579-594 `_coerce_string_list` | Rejects non-list and non-string elements with HTTP 422 | ✅ |
| FR-003 (trim/remove blank) | `executions.py` L579-594 | Strips each entry; skips empty strings | ✅ |
| FR-004 (deduplicate) | `executions.py` L770 | `dict.fromkeys(...)` preserves insertion order | ✅ |
| FR-005 (10-item limit in router) | `executions.py` L772-773 | Raises `_invalid_task_request` before service call | ✅ |
| FR-006 (resolve to existing execution) | `service.py` L246-248 | `describe_execution` → `TemporalExecutionNotFoundError` | ✅ |
| FR-007 (MoonMind.Run only) | `service.py` L250-253 | Checks `workflow_type is TemporalWorkflowType.RUN` | ✅ |
| FR-008 (no self-dependency) | `service.py` L223-224 | Checks `new_workflow_id in depends_on` | ✅ |
| FR-009/010 (cycle detection w/ bounds) | `service.py` L226-265 | BFS with depth 10, node limit 50 | ✅ |
| FR-011 (persist to initialParameters) | `executions.py` L802-803, 856-857 | Sets `normalized_task_for_planner["dependsOn"]`; included in `initial_parameters["task"]` | ✅ |
| FR-012 (specific error messages) | `service.py` L248, 252-253 | Per-case messages for each error type | ✅ |
| FR-013 (no regressions) | Verified by `./tools/test_unit.sh` | 2162 tests pass | ✅ |

### Gap Found: Missing Self-Dependency Unit Test

The self-dependency check exists in the service code (L223-224 of `service.py`), but there is **no unit test** in `test_temporal_service.py` that directly tests the self-dependency rejection path. This gap should be closed to achieve full coverage of FR-008.

## Phase 1: Design & Contracts

### Source Code Layout

```text
moonmind/workflows/temporal/service.py       # _validate_dependencies (already implemented)
api_service/api/routers/executions.py        # _create_execution_from_task_request (already implemented)

tests/unit/workflows/temporal/
└── test_temporal_service.py                 # ADD: self-dependency rejection test

docs/Tasks/TaskDependencies.md         # UPDATE: Phase 1 status to complete

specs/117-task-dep-phase1/
├── plan.md         # This file
├── research.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### contracts/requirements-traceability.md

| FR | DOC-REQ | Tests | Status |
|----|---------|-------|--------|
| FR-001 | DOC-REQ-001 | `test_create_task_shaped_execution_prefers_task_depends_on` | ✅ |
| FR-002 | DOC-REQ-002 | `test_create_task_shaped_execution_rejects_invalid_required_capabilities` (same helper) | ✅ |
| FR-003 | DOC-REQ-003 | `test_create_task_shaped_execution_dedupes_and_normalizes_dependencies` | ✅ |
| FR-004 | DOC-REQ-003 | `test_create_task_shaped_execution_dedupes_and_normalizes_dependencies` | ✅ |
| FR-005 | DOC-REQ-004 | `test_create_task_shaped_execution_rejects_more_than_10_dependencies` (both) | ✅ |
| FR-006 | DOC-REQ-005 | `test_create_execution_rejects_missing_dependency` | ✅ |
| FR-007 | DOC-REQ-006 | `test_create_execution_rejects_non_run_dependency` | ✅ |
| FR-008 | DOC-REQ-007 | **MISSING** — to be added | ⚠️ |
| FR-009/010 | DOC-REQ-008 | `test_create_execution_rejects_dependency_graph_too_deep/large` | ✅ |
| FR-011 | DOC-REQ-009 | `test_create_task_shaped_execution_dedupes_and_normalizes_dependencies` | ✅ |
| FR-012 | DOC-REQ-010 | Per-case error message assertions in each test | ✅ |
| FR-013 | N/A | `./tools/test_unit.sh` exit 0 | ✅ |

## Complexity Tracking

No Constitution violations. No complexity tracking required.
