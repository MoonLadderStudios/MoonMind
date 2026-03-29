# Task Dependencies System

Status: Active  
Implementation State: Partially implemented (API validation complete, workflow gate pending)  
Owners: MoonMind Engineering  
Last Updated: 2026-03-29  
Related: `docs/Api/ExecutionsApiContract.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskCancellation.md`, `docs/Tasks/TaskProposalSystem.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/UI/MissionControlArchitecture.md`  
Implementation Tracking: `docs/tmp/011-TaskDependenciesPlan.md`

---

## 1. Purpose

This document defines the desired-state contract for **Task Dependencies** in MoonMind.

Task dependencies allow one `MoonMind.Run` execution to wait on one or more other `MoonMind.Run` executions before entering its own active work. This supports multi-stage orchestration across separate runs while keeping each execution independently durable, observable, and cancelable.

---

## 2. Contract Summary

- A task-shaped create request may declare prerequisite execution IDs in `payload.task.dependsOn`.
- `dependsOn` values are `workflowId` values for existing `MoonMind.Run` executions.
- For Temporal-backed task surfaces, `taskId == workflowId`.
- `runId` is diagnostic only and is never a valid dependency target.
- A dependent run remains in `waiting_on_dependencies` until all listed prerequisites complete successfully.
- If any prerequisite fails, is canceled, is terminated, or cannot be resolved at runtime, the dependent run fails with a dependency-specific failure.
- Dependencies are **non-transitive** at the contract level: if C depends on B and B depends on A, C waits on B only.

### 2.1 v1 scope

Version 1 of this feature is intentionally narrow:

- create-time dependency declaration only,
- `MoonMind.Run` to `MoonMind.Run` dependencies only,
- maximum of **10** dependency IDs,
- no dependency editing after create,
- no cross-workflow-type dependencies,
- no template-instantiated dependency graphs.

---

## 3. Current Implementation Snapshot

### 3.1 Already implemented

- The lifecycle vocabulary includes `waiting_on_dependencies` (`MoonMindWorkflowState` enum, workflow constant, DB migration).
- Execution API and UI status mapping recognize `waiting_on_dependencies` and map it to dashboard status `waiting`.
- Action gating treats `waiting_on_dependencies` as a first-class pre-execution state.
- Task-shaped submit normalizes and persists `payload.task.dependsOn` into `initialParameters.task.dependsOn`.
- Create-time dependency validation is complete: array coercion, deduplication, 10-item limit, self-dependency rejection, missing-target rejection, non-Run-type rejection, and transitive cycle detection with bounded traversal (depth 10, node limit 50).

### 3.2 Still missing

- `MoonMind.Run` does not yet enforce a real dependency gate before `planning`.
- Dependency outcome details are not yet captured in the finish summary.
- Mission Control does not yet expose dependency authoring or inspection UX.

The canonical contract in this document describes the target behavior. Open implementation sequencing lives in `docs/tmp/011-TaskDependenciesPlan.md`.

---

## 4. API Contract

Task dependencies belong on the Temporal-backed create flow through `/api/executions`.

The user-facing request remains task-shaped:

```json
{
  "type": "task",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "task": {
      "instructions": "Run integration tests",
      "dependsOn": [
        "mm:01ABC...",
        "mm:01DEF..."
      ]
    }
  }
}
```

The router normalizes this into `initialParameters.task.dependsOn` for `MoonMind.Run`.

### 4.1 Validation rules

At create time, the API must validate:

1. `dependsOn` is an array of strings.
2. Blank entries are removed and repeated IDs are deduplicated.
3. At most 10 IDs remain after normalization.
4. Each ID resolves to an existing execution.
5. Each target execution is `MoonMind.Run`.
6. The new execution does not depend on itself.
7. Adding the dependency edges does not create a cycle.

### 4.2 Cycle detection

Cycle detection walks the transitive `dependsOn` graph of existing executions:

1. Start from each requested dependency ID.
2. Read that execution's stored dependency list.
3. Continue recursively until the graph is exhausted or a guardrail is hit.
4. Reject the request if the new execution's `workflowId` appears in the reachable set.

To keep validation bounded, the traversal should enforce depth and visited-node limits and fail with a clear validation error when those bounds are exceeded.

---

## 5. Workflow Execution Behavior

`MoonMind.Run` enforces dependencies before `planning`.

### 5.1 Lifecycle

For dependency-aware runs, the relevant lifecycle is:

1. `initializing`
2. `waiting_on_dependencies`
3. `planning`
4. `awaiting_slot`
5. `executing`
6. `awaiting_external`
7. `proposals`
8. `finalizing`
9. terminal state

If `initialParameters.task.dependsOn` is absent or empty, the workflow proceeds directly from `initializing` to `planning`.

### 5.2 Waiting mechanism

The workflow should wait on prerequisites using Temporal external workflow handles.

```python
handles = [
    workflow.get_external_workflow_handle(dep_id)
    for dep_id in depends_on
]
await asyncio.gather(*(handle.result() for handle in handles))
```

This is the canonical v1 mechanism because it is Temporal-native, avoids polling loops, and lets prerequisite failures surface directly through workflow result waiting. The `gather` call should be wrapped in a Temporal `CancellationScope` so that cancellation of the dependent run interrupts the wait cleanly.

### 5.3 State and control behavior

When dependencies are present, the workflow should:

1. read `initialParameters.task.dependsOn` after initialization,
2. set `mm_state` to `waiting_on_dependencies`,
3. expose dependency IDs in metadata needed by API and UI surfaces,
4. wait for all prerequisites to succeed before entering `planning`.

Cancellation and pause semantics from `docs/Tasks/TaskCancellation.md` still apply during the dependency wait:

- canceling the dependent run cancels only that run,
- pausing the dependent run pauses its own progress,
- dependency waiting must not cancel or mutate prerequisite runs.

### 5.4 Failure behavior

If any prerequisite fails, is canceled, is terminated, or cannot be resolved at runtime after successful create-time validation, the dependent run fails with a dependency-specific reason.

If all prerequisites have already completed successfully when the dependent run starts, the wait resolves immediately and the workflow continues without delay.

### 5.5 Finish summary

The finish summary should record dependency outcomes in both typed run results and `reports/run_summary.json`.

At minimum it records:

1. declared dependency IDs,
2. whether a dependency wait occurred,
3. dependency wait duration,
4. whether resolution was success or dependency failure,
5. the failed dependency ID when available.

---

## 6. Mission Control Expectations

### 6.1 Create flow

Mission Control should expose dependency configuration on `/tasks/new`.

The initial UX should provide:

- a dependency picker for existing `MoonMind.Run` executions,
- client-side enforcement of the 10-item limit,
- clear validation messaging when selected dependencies are invalid.

### 6.2 List and detail surfaces

- `waiting_on_dependencies` should continue to map to dashboard status `waiting`.
- Task detail should show a Dependencies panel with prerequisite links and current statuses.
- A downstream dependents view is useful, but it is follow-on scope rather than a v1 requirement.

---

## 7. Boundary With Plan DAG Semantics

Task dependencies are **inter-workflow** dependencies between separate `MoonMind.Run` executions.

They are distinct from plan-node or skill-node edges inside a single run. Intra-run plan DAG edges control execution order within one workflow. Task dependencies block one workflow on the terminal outcome of another workflow.

---

## 8. Failure Modes and Edge Cases

- **Circular dependencies:** rejected at create time.
- **Missing runtime target after validation:** treated as a dependency failure rather than an infinite wait.
- **Workflow timeout:** the workflow's standard execution timeout still applies while waiting.
- **Long dependency chains:** validation may reject overly deep or expensive graphs rather than allow unbounded traversal.

---

## 9. Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | Dependencies are an orchestration primitive across runs. |
| II. One-Click Agent Deployment | PASS | No new infrastructure is required. |
| III. Avoid Vendor Lock-In | PASS | The design uses Temporal-native orchestration concepts already central to MoonMind. |
| IV. Own Your Data | PASS | Dependency metadata remains in operator-controlled execution records and artifacts. |
| V. Skills Are First-Class | N/A | Not a skill contract change. |
| VI. Bittersweet Lesson | PASS | The contract stays thin and focused on durable orchestration boundaries. |
| VII. Runtime Configurability | PASS | `dependsOn` is request data, not a hardcoded workflow rule. |
| VIII. Modular Architecture | PASS | The feature extends execution contracts without introducing cross-cutting aliases. |
| IX. Resilient by Default | PASS | Validation, durable waiting, and explicit failure propagation support unattended execution. |
| X. Continuous Improvement | N/A | Not directly applicable. |
| XI. Spec-Driven | PASS | Phases 0–1 completed via specs `116-task-dep-phase0` and `117-task-dep-phase1`; remaining phases tracked in `docs/tmp/011-TaskDependenciesPlan.md`. |
| XII. Canonical Documentation Separates Desired State from Migration Backlog | PASS | Desired-state behavior stays here; phase sequencing lives in `docs/tmp/011-TaskDependenciesPlan.md`. |

---

## 10. Implementation Tracking

Open implementation sequencing for this feature is tracked in [`docs/tmp/011-TaskDependenciesPlan.md`](../tmp/011-TaskDependenciesPlan.md) and related remaining-work docs under `docs/tmp/remaining-work/`.
