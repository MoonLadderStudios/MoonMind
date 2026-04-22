# Task Dependencies — Operator Guide

This document describes operational behavior, limits, diagnostics, and remediation for the **Task Dependencies** feature in MoonMind.

---

## 1. What Are Task Dependencies?

A `MoonMind.Run` execution can declare up to **10 prerequisite `workflowId` values** at create time via `payload.task.dependsOn`. The dependent run:

1. Initializes normally.
2. Enters `waiting_on_dependencies` state.
3. Performs an immediate durable status reconciliation for each prerequisite.
4. Waits durably for explicit `DependencyResolved` signals from any unresolved prerequisites.
5. Proceeds only when **every** prerequisite reaches terminal MoonMind state `completed`.
6. Fails with structured metadata if any prerequisite reaches a non-success terminal outcome.

Each dependent run is a **top-level, independently visible, cancelable, and rerunnable** execution — not a child workflow.

---

## 2. Limits and Constraints (V1)

| Constraint | Value |
|---|---|
| Maximum direct dependencies per run | **10** |
| Cross-workflow-type dependencies | **Not supported** — prerequisites must also be `MoonMind.Run` |
| Editing dependencies after create | **Not supported** |
| Transitive dependency expansion | **Not supported** — C depends on B depends on A means C waits on B only |
| Auto-canceling prerequisite runs | **Not supported** |
| Template-authored dependency graphs | **Not supported** |
| Dependency-based priority/queue ordering | **Not supported** |

---

## 3. Failure Propagation Rules

A prerequisite satisfies the dependency gate **only** when it reaches MoonMind terminal state `completed`.

Any other terminal prerequisite outcome causes **immediate failure** of the dependent run:

| Prerequisite Terminal State | Dependent Run Behavior |
|---|---|
| `completed` | Gate satisfied for that prerequisite |
| `failed` | Dependent run fails immediately |
| `canceled` | Dependent run fails immediately |
| `terminated` | Dependent run fails immediately |
| `timed_out` | Dependent run fails immediately |
| Unresolvable (not found) | Dependent run fails on reconciliation (often immediately at startup) |

When a prerequisite fails, the dependent run records a structured `DependencyFailureError` with:
- `failedDependencyId`
- Prerequisite terminal MoonMind state
- Prerequisite close status
- Dependency failure category
- Human-readable message

---

## 4. Pause and Cancel Behavior

### While Blocked on Dependencies

| Action | Effect |
|---|---|
| **Cancel** dependent run | Cancels only the dependent run. Prerequisites are untouched. |
| **Bypass dependency wait** | Manually clears the dependency gate and lets the dependent run continue even if one or more prerequisites are still unresolved. Prerequisites are untouched. |
| **Pause** dependent run | Pauses progression through the dependency gate. Signals continue to be received and recorded; if a prerequisite failure is recorded while paused, the dependent run fails immediately. |
| **Resume** dependent run | If all prerequisites resolved successfully while paused, proceeds normally. Any prerequisite failure recorded during pause would already have failed the dependent run before resume. |
| Cancel/prerequisite run | **Never** affected by dependent run's state. |

### Key Guarantees

- Pausing the dependent run does **not** suppress receipt of `DependencyResolved` signals.
- While paused, dependency outcomes continue to be recorded in local state.
- Pausing does **not** defer dependency-failure propagation; a prerequisite failure recorded while paused fails the dependent run immediately.
- While paused, the workflow does **not** leave the dependency gate and enter planning or execution unless it is failed by dependency resolution.
- Bypassing the dependency wait is an explicit operator override for cases where the remaining prerequisites are no longer required for reasons MoonMind cannot infer automatically. The run summary records dependency resolution as `bypassed`.

---

## 5. Diagnostic Event Log Reference

The following structured log events are emitted during dependency lifecycle:

| Event | Level | Where | Key Fields |
|---|---|---|---|
| `dependency_gate_entered` | INFO | Workflow | `dependency_count`, `dependency_ids` |
| `dependency_gate_satisfied` | INFO | Workflow | `dependency_count`, `wait_duration_ms` |
| `dependency_gate_bypassed` | INFO | Workflow | `dependency_count`, `unresolved_dependency_count`, `wait_duration_ms` |
| `dependency_gate_failed` | ERROR | Workflow | `failed_dependency_id`, `terminal_state`, `close_status`, `failure_category`, `wait_duration_ms` |
| `dependency_signal_received` | INFO | Workflow | `prerequisite_workflow_id`, `terminal_state`, `close_status` |
| `dependency_signal_failure` | WARNING | Workflow | `prerequisite_workflow_id`, `terminal_state`, `close_status`, `failure_category` |
| `dependency_signal_ignored_undeclared` | WARNING | Workflow | `prerequisite_workflow_id` |
| `dependency_reconciliation_mismatch` | WARNING | Workflow | `prerequisite_workflow_id`, `mismatch_type` |
| `dependency_signal_fan_out` | INFO | Service | `prerequisite_workflow_id`, `fan_out_count`, `signals_sent`, `signals_failed` |
| `dependency_signal_delivery_failed` | WARNING | Service | `dependent_workflow_id`, `prerequisite_workflow_id`, `error` |

---

## 6. Troubleshooting

### A Dependent Run Is Stuck in `waiting_on_dependencies`

1. **Check prerequisites**: Use the detail API or Mission Control to see which prerequisites are still running.
2. **Check signal delivery**: If a prerequisite completed but the dependent is still waiting, check service logs for `dependency_signal_fan_out` events. If `signals_failed > 0`, the signal may not have reached the dependent.
3. **Check reconciliation**: The workflow reconciles every 30 seconds via `execution.dependency_status_snapshot` activity. If the activity is failing, the workflow won't detect completed prerequisites via reconciliation.
4. **Worker health**: If the Temporal worker is down, signals queue up and reconcile activities won't execute. Check worker logs and container health.

### A Dependent Run Failed Due to a Prerequisite Failure

1. Check the `DependencyFailureError` detail in the run summary artifact (`reports/run_summary.json`) under the `dependencies` block.
2. The `failedDependencyId` field identifies which prerequisite caused the failure.
3. The `terminalState` and `failureCategory` fields explain the nature of the failure.
4. **Remediation**: Fix or rerun the failed prerequisite, then rerun the dependent task.

### Reverse Lookup Shows No Dependents

Dependents are stored in the `execution_dependencies` durable edge table. If reverse lookup returns empty:

1. Verify the prerequisite's `workflowId` matches the `prerequisite_workflow_id` column.
2. Check that the dependency edges were persisted at create time (both `initialParameters.task.dependsOn` and the edge table must be written).

---

## 7. Deployment and Replay Safety

The dependency gate is deployed under a Temporal patch:

- **Patch ID**: `dependency-gate-v1`
- Workflows started **before** the patch was deployed skip the dependency wait entirely (backward compatible).
- Workflows started **after** the patch is deployed execute the full dependency gate.
- Future dependency-gate changes must keep replay behavior explicit through patch gates, replay tests, or a documented cutover.

---

## 8. Signal Contract

### `DependencyResolved` Signal

Sent to dependent workflows when a prerequisite reaches terminal state.

**Payload shape:**

```json
{
  "prerequisiteWorkflowId": "mm:01ABC...",
  "terminalState": "completed",
  "closeStatus": "completed",
  "resolvedAt": "2026-04-03T17:24:16Z",
  "failureCategory": null,
  "message": null
}
```

**Non-success payload:**

```json
{
  "prerequisiteWorkflowId": "mm:01ABC...",
  "terminalState": "failed",
  "closeStatus": "failed",
  "resolvedAt": "2026-04-03T17:24:16Z",
  "failureCategory": "dependency_failed",
  "message": "Prerequisite run failed before dependent gate cleared"
}
```

**Fan-out behavior:** Best-effort, bounded, idempotent. If a dependent workflow is already closed or missing, the failure is logged and fan-out continues.

---

## 9. Known Non-Goals (V1)

- Editing dependencies after create
- Cross-workflow-type dependencies
- Template-authored dependency graphs
- Automatic transitive dependency expansion
- Auto-canceling prerequisite runs
- Dependency-based priority or queue-order guarantees
- Replacing child workflows with task dependencies
- Storing large dependency history in Search Attributes

---

## 10. Recommended Remediation When a Prerequisite Fails

1. Identify the failed prerequisite from the dependent run's summary artifact or Mission Control detail view.
2. Diagnose and resolve the prerequisite failure (rerun, fix input, etc.).
3. Once the prerequisite reaches `completed` state, **rerun the dependent task** — dependencies are create-time only and cannot be edited.
4. If the prerequisite cannot be recovered, create a new dependent run with corrected prerequisites.
