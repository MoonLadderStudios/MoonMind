# Research: Task Dependencies Phase 1 — Submit Contract And Validation

## Context

This research phase audited the existing implementation of the Phase 1 submit contract and validation against the DOC-REQ requirements from `docs/Tasks/TaskDependencies.md`.

## Findings

### Decision: Verify-only for implemented code

- **What was chosen**: The router and service implementations are confirmed complete; no new production code is needed.
- **Rationale**: All FR-001 through FR-012 are implemented and tested. Adding unnecessary code could introduce regressions.
- **Alternatives considered**: Refactoring validation into a standalone module — rejected as premature; the current placement (router for shape, service for semantic) is clean.

### Decision: Add self-dependency unit test

- **What was chosen**: Add one unit test to `test_temporal_service.py` for the self-dependency rejection path (FR-008).
- **Rationale**: The code is correct but the test gap leaves FR-008 unverified by automated tests.
- **Alternatives considered**: Skip test — rejected because the speckit contract requires all FRs to have automated test coverage.

### Decision: Update plan tracking doc

- **What was chosen**: Update `docs/tmp/011-TaskDependenciesPlan.md` to mark Phase 1 as complete.
- **Rationale**: Drives implementation sequencing for Phases 2–5.

## Audit Results

### Router (`api_service/api/routers/executions.py`)

| Check | Lines | Result |
|-------|-------|--------|
| Prefer `task_payload.dependsOn` over `payload.dependsOn` | L758-763 | ✅ |
| `_coerce_string_list` validates array of strings | L579-594 | ✅ |
| Trim and remove blank entries | L579-594 | ✅ |
| Deduplicate preserving insertion order | L770 | ✅ |
| 10-item limit before service call | L772-773 | ✅ |
| Persist to `initial_parameters["task"]["dependsOn"]` | L802-803, 856-857 | ✅ |

### Service (`moonmind/workflows/temporal/service.py`)

| Check | Lines | Result |
|-------|-------|--------|
| 10-item limit check | L220-221 | ✅ |
| Self-dependency check | L223-224 | ✅ |
| BFS with depth 10 limit | L242-243 | ✅ |
| 50-node traversal limit | L239-240 | ✅ |
| Resolve each ID to existing execution | L246-248 | ✅ |
| Check `workflow_type` is `MoonMind.Run` | L250-253 | ✅ |
| Read transitive deps for cycle detection | L256-265 | ✅ |

### Tests

| Test | File | FR |
|------|------|-----|
| `test_create_execution_rejects_more_than_10_dependencies` | test_temporal_service.py | FR-005 |
| `test_create_execution_rejects_missing_dependency` | test_temporal_service.py | FR-006 |
| `test_create_execution_rejects_non_run_dependency` | test_temporal_service.py | FR-007 |
| **MISSING: self-dependency test** | test_temporal_service.py | **FR-008** |
| `test_create_execution_rejects_dependency_graph_too_deep` | test_temporal_service.py | FR-009/010 |
| `test_create_execution_rejects_dependency_graph_too_large` | test_temporal_service.py | FR-009/010 |
| `test_create_task_shaped_execution_rejects_more_than_10_dependencies` | test_executions.py | FR-005 |
| `test_create_task_shaped_execution_dedupes_and_normalizes_dependencies` | test_executions.py | FR-003, FR-004, FR-011 |
| `test_create_task_shaped_execution_prefers_task_depends_on` | test_executions.py | FR-001 |
