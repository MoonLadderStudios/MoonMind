# Implementation Plan: Task Dependencies Phase 2 - MoonMind.Run Dependency Gate

**Branch**: `123-task-dep-phase2` | **Date**: 2026-04-01 | **Spec**: [spec.md](spec.md) 
**Input**: Feature specification from `/specs/123-task-dep-phase2/spec.md`

## Summary

Phase 2 implements the missing runtime dependency gate inside `MoonMindRunWorkflow.run()`. The workflow will inspect `initialParameters.task.dependsOn` after payload initialization, enter `waiting_on_dependencies` when dependencies exist, wait on prerequisite workflow handles inside a cancellation scope, and only proceed to planning when all prerequisites complete successfully. The implementation is test-driven and centered on workflow-boundary coverage for the patched path, replay-safe legacy path, and dependency-failure/cancel outcomes.

## Technical Context

**Language/Version**: Python 3.11 
**Primary Dependencies**: Temporal Python SDK, pytest 
**Storage**: Temporal workflow state, Search Attributes, Memo, existing artifact outputs 
**Testing**: pytest via `./tools/test_unit.sh` 
**Target Platform**: Linux worker container running Temporal workflows 
**Project Type**: Single Python monorepo 
**Performance Goals**: Dependency gating should add no polling loop and should block only on prerequisite workflow completion or cancellation. 
**Constraints**: Preserve replay safety for in-flight executions, add workflow-boundary tests, do not introduce new internal compatibility aliases, and keep metadata within the existing Visibility schema. 
**Scale/Scope**: One workflow file, one or two unit-test files, and feature-spec artifacts for this phase.

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | Uses Temporal-native external workflow handles rather than bespoke polling or parallel orchestration code. |
| II. One-Click Agent Deployment | PASS | No infrastructure or operator bootstrap changes. |
| III. Avoid Vendor Lock-In | PASS | Uses Temporal workflow semantics already central to the platform. |
| IV. Own Your Data | PASS | Dependency metadata stays in operator-controlled workflow memo/search attributes and run artifacts. |
| V. Skills Are First-Class | N/A | Not a skill system change. |
| VI. Bittersweet Lesson | PASS | Thin orchestration addition behind stable workflow contracts; no new runtime scaffolding layers. |
| VII. Runtime Configurability | PASS | Dependencies come from request payload data already persisted in `initialParameters`. |
| VIII. Modular Architecture | PASS | Keeps dependency wait logic localized to `MoonMindRunWorkflow` lifecycle boundaries. |
| IX. Resilient by Default | PASS | Adds durable dependency waiting, failure propagation, and replay-safe patching with boundary tests. |
| X. Continuous Improvement | PASS | Improves runtime observability through waiting metadata without changing operator flow shape. |
| XI. Spec-Driven Development | PASS | This feature spec/plan/tasks drive the Phase 2 runtime implementation. |
| XII. Canonical Documentation | PASS | Sequencing stays in `local-only handoffs`; canonical docs already describe the target behavior. |
| XIII. Pre-Release Velocity | PASS | No compatibility aliases; only a replay-safe patch guard for in-flight workflow histories. |

## Phase 0: Research Findings

See [research.md](research.md) for the detailed decision log. Key decisions:

1. Use `workflow.get_external_workflow_handle(dep_id).result()` and `asyncio.gather(...)` rather than polling activities.
2. Guard the new dependency gate with `workflow.patched("dependency-gate-v1")` so unpatched histories preserve legacy `initializing -> planning` behavior.
3. Reuse the existing visibility model: `mm_state=waiting_on_dependencies`, `waitingReason=dependency_wait`, dependency IDs stored in workflow memo, and no new search attributes in Phase 2.
4. Cover the feature at the workflow boundary with unit tests that exercise the patched path, the legacy unpatched path, and degraded dependency outcomes.

## Phase 1: Design & Contracts

### Source Code Layout

```text
moonmind/workflows/temporal/workflows/run.py
tests/unit/workflows/temporal/workflows/test_run_scheduling.py
tests/unit/workflows/temporal/workflows/test_run_signals_updates.py

specs/123-task-dep-phase2/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── requirements-traceability.md
└── tasks.md
```

**Structure Decision**: Keep the implementation in the existing `MoonMindRunWorkflow` module and add workflow-boundary tests alongside the current run workflow tests. No new package or abstraction layer is justified for this phase.

### Design Notes

#### Workflow insertion point

- Add dependency parsing immediately after `_initialize_from_payload()` and before the first transition to `STATE_PLANNING`.
- Preserve current behavior for runs with no dependencies or for unpatched replay histories.

#### Waiting behavior

- Introduce a small helper to normalize dependency IDs from `parameters.get("task", {}).get("dependsOn")`.
- Introduce a helper that:
 - stores dependency IDs on workflow state,
 - sets `_waiting_reason = "dependency_wait"`,
 - transitions to `STATE_WAITING_ON_DEPENDENCIES`,
 - awaits dependency handles inside `workflow.CancellationScope()`,
 - converts dependency exceptions into a dependency-specific `ValueError`.

#### Metadata behavior

- Keep queryable state in the existing registered schema:
 - `mm_state = waiting_on_dependencies`
 - `waiting_reason = dependency_wait`
- Store compact dependency IDs in memo only, because the current visibility contract explicitly avoids new ad hoc search attributes in v1.

#### Replay and cancellation safety

- Patched path: run dependency gate.
- Unpatched path: skip dependency gate entirely to preserve existing histories.
- Cancellation during wait should interrupt the scope and let normal cancel/finalize logic run without signaling prerequisite workflows.

### Planned Artifacts

- [data-model.md](data-model.md): workflow-local dependency gate fields and state transitions
- [contracts/requirements-traceability.md](contracts/requirements-traceability.md): `DOC-REQ-*` to FR/test mapping
- [quickstart.md](quickstart.md): deterministic validation path for patched/unpatched/failure cases

## Complexity Tracking

No Constitution violations. No complexity tracking required.
