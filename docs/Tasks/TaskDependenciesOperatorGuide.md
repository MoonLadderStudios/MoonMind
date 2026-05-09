# Task Dependencies — Operator Guide

This document describes operational behavior, limits, diagnostics, and remediation for the **Task Dependencies** feature in MoonMind.

---

## 1. What Are Task Dependencies?

A `MoonMind.Run` execution can declare up to **10 prerequisite `workflowId` values** at create time via `payload.task.dependsOn`. The dependent run:

1. Initializes normally.
2. Enters `waiting_on_dependencies` state.
3. Performs an immediate durable status reconciliation for each prerequisite.
4. Waits durably for explicit `DependencyResolved` signals from any unresolved prerequisites.
5. Proceeds only when **every** prerequisite reaches terminal MoonMind state `completed` — possibly after one or more prerequisite reruns.
6. **Never auto-fails on a prerequisite failure.** A prerequisite reaching `failed`, `canceled`, `terminated`, `timed_out`, or runtime-unresolvable keeps the dependent in `waiting_on_dependencies` and records a `waiting_for_successful_rerun` outcome for that prerequisite. The only ways to leave the gate without prerequisite success are operator cancel, operator bypass, or operator manual skip.

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

## 3. Wait-Through-Rerun Behavior

A prerequisite satisfies the dependency gate **only** when it reaches MoonMind terminal state `completed`.

Under the unified wait-through-rerun model, no prerequisite terminal outcome auto-fails the dependent. The dependent stays in `waiting_on_dependencies` and records structured per-dependency diagnostics until either prerequisites complete, an operator cancels the dependent, or an operator bypasses the dependency wait.

| Prerequisite Terminal State | Dependent Run Behavior |
|---|---|
| `completed` | Gate satisfied for that prerequisite |
| `failed` | Dependent stays in `waiting_on_dependencies`; records `waiting_for_successful_rerun` |
| `canceled` | Dependent stays in `waiting_on_dependencies`; records `waiting_for_successful_rerun` |
| `terminated` | Dependent stays in `waiting_on_dependencies`; records `waiting_for_successful_rerun` |
| `timed_out` | Dependent stays in `waiting_on_dependencies`; records `waiting_for_successful_rerun` |
| Unresolvable (not found) | Dependent stays in `waiting_on_dependencies`; records `waiting_for_successful_rerun` with `failureCategory = "dependency_unresolved"` |

When a prerequisite fails, the dependent records bounded per-dependency metadata:

- prerequisite `workflowId`,
- latest terminal MoonMind state,
- latest close status,
- `failureCategory`,
- `failureCount` (number of distinct non-success terminal observations),
- `lastFailedAt` (most recent non-success terminal timestamp),
- human-readable message,
- `resolution = "waiting_for_successful_rerun"`.

When the same prerequisite is later rerun and reaches `completed` under the same `workflowId`, the per-dependency entry transitions to `resolution = "satisfied_after_rerun"` and the prerequisite is removed from the unresolved set. Once every prerequisite is satisfied, the gate clears and the dependent proceeds.

The top-level run summary `resolution` will be `satisfied_after_rerun` if any prerequisite required at least one rerun cycle, or `satisfied` if every prerequisite cleared on first observation.

---

## 4. Pause and Cancel Behavior

### While Blocked on Dependencies

| Action | Effect |
|---|---|
| **Cancel** dependent run | Cancels only the dependent run. Prerequisites are untouched. The only routine way to release a dependent that is waiting on a prerequisite that will never complete. |
| **Bypass dependency wait** | Manually clears the dependency gate and lets the dependent run continue even if one or more prerequisites are still unresolved. Prerequisites are untouched. |
| **Manual skip** (`SkipDependencyWait`) | Operator-only update that clears unresolved prerequisites and lets the dependent advance with `resolution = manual_override`. |
| **Pause** dependent run | Pauses progression through the dependency gate. Signals continue to be received and recorded; non-success terminal outcomes recorded while paused do **not** fail the run, they accumulate as `waiting_for_successful_rerun` entries. |
| **Resume** dependent run | If all prerequisites resolved successfully while paused, proceeds normally. Otherwise the dependent re-enters its waiting loop with the recorded outcomes intact. |
| Cancel/terminate prerequisite run | **Never** affects the dependent run's state automatically. The dependent will continue waiting for that prerequisite to be rerun to success, or for an operator to cancel/bypass on the dependent side. |

### Key Guarantees

- Pausing the dependent run does **not** suppress receipt of `DependencyResolved` signals.
- While paused, dependency outcomes continue to be recorded in local state.
- A prerequisite non-success terminal observation never fails the dependent under the wait-through-rerun contract — neither while running, nor while paused.
- While paused, the workflow does **not** leave the dependency gate and enter planning or execution.
- Bypassing the dependency wait is an explicit operator override for cases where the remaining prerequisites are no longer required for reasons MoonMind cannot infer automatically. The run summary records dependency resolution as `bypassed`.

---

## 5. Diagnostic Event Log Reference

The following structured log events are emitted during dependency lifecycle:

| Event | Level | Where | Key Fields |
|---|---|---|---|
| `dependency_gate_entered` | INFO | Workflow | `dependency_count`, `dependency_ids` |
| `dependency_gate_satisfied` | INFO | Workflow | `dependency_count`, `wait_duration_ms` |
| `dependency_gate_waiting_for_rerun` | INFO | Workflow | `prerequisite_workflow_id`, `terminal_state`, `close_status`, `failure_count` |
| `dependency_gate_satisfied_after_rerun` | INFO | Workflow | `prerequisite_workflow_id`, `failure_count`, `wait_duration_ms` |
| `dependency_gate_bypassed` | INFO | Workflow | `dependency_count`, `unresolved_dependency_count`, `wait_duration_ms` |
| `dependency_gate_failed` | ERROR | Workflow | (Legacy fail-fast path; emitted only by in-flight workflows whose history predates `dependency-wait-through-rerun-v1`.) |
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

### A Dependent Run Is Waiting Because a Prerequisite Failed

Under the wait-through-rerun contract, a failed prerequisite does **not** fail the dependent. Instead the dependent stays in `waiting_on_dependencies` with a per-dependency outcome marked `waiting_for_successful_rerun`.

1. Open the dependent's task detail panel (or read `reports/run_summary.json` under `dependencies.outcomes`) and find any per-dependency outcome with `resolution = "waiting_for_successful_rerun"`.
2. The `failureCount` and `lastFailedAt` fields tell you how many failure cycles have happened and when the most recent failure occurred.
3. The `terminalState`, `closeStatus`, and `failureCategory` fields explain the nature of the most recent failure.
4. **Remediation paths:**
   - Rerun the failed prerequisite. Once it reaches `completed` under the same `workflowId`, the dependent unblocks automatically. The dependent's resolution becomes `satisfied_after_rerun`.
   - If the prerequisite cannot be recovered, **cancel the dependent run** or use **Bypass Dependency Wait** to advance the dependent without that prerequisite.
   - Do **not** wait for the dependent to fail itself: under wait-through-rerun, it never will.

### A Dependent Run Failed Due to a Legacy Prerequisite Failure

Workflows that started **before** `dependency-wait-through-rerun-v1` was deployed may still fail-fast on a prerequisite failure. For those legacy runs:

1. Check the `DependencyFailureError` detail in the run summary artifact (`reports/run_summary.json`) under the `dependencies` block.
2. The `failedDependencyId` field identifies which prerequisite caused the failure.
3. The `terminalState` and `failureCategory` fields explain the nature of the failure.
4. **Remediation**: Fix or rerun the failed prerequisite, then rerun the dependent task. Newly created dependent runs use the wait-through-rerun behavior automatically.

### Reverse Lookup Shows No Dependents

Dependents are stored in the `execution_dependencies` durable edge table. If reverse lookup returns empty:

1. Verify the prerequisite's `workflowId` matches the `prerequisite_workflow_id` column.
2. Check that the dependency edges were persisted at create time (both `initialParameters.task.dependsOn` and the edge table must be written).

---

## 7. Deployment and Replay Safety

The dependency gate is deployed under cooperating Temporal patches:

- **`dependency-gate-v1`** — installs the dependency gate itself. Workflows started before this patch was deployed skip the gate entirely (backward compatible).
- **`dependency-wait-through-rerun-v1`** — installs the unified wait-through-rerun behavior. The first time a workflow under this patch records a prerequisite outcome, Temporal records a marker that pins the workflow to the new behavior across replays.
  - Workflows started **after** `dependency-wait-through-rerun-v1` was deployed never auto-fail on a prerequisite failure.
  - Workflows whose history predates `dependency-wait-through-rerun-v1` continue to use the legacy fail-fast code path until they terminate. Their `_dependency_failure` paths and `dependency_gate_failed` events remain accurate for those runs only.

Future dependency-gate changes must keep replay behavior explicit through patch gates, replay tests, or a documented cutover.

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
- Retargeting a dependency to a different `workflowId` after create
- Cross-workflow-type dependencies
- Template-authored dependency graphs
- Automatic transitive dependency expansion
- Auto-canceling prerequisite runs
- Auto-rerunning failed prerequisites
- Auto-creating remediation tasks for failed prerequisites
- Dependency-based priority or queue-order guarantees
- Replacing child workflows with task dependencies
- Storing large dependency history in Search Attributes

---

## 10. Recommended Remediation When a Prerequisite Fails

1. Identify the failed prerequisite from the dependent run's task detail dependency panel or run summary artifact (look for an outcome with `resolution = "waiting_for_successful_rerun"`).
2. Diagnose the prerequisite failure (its terminal state, close status, failure category, and message are surfaced on the prerequisite link, and `failureCount` / `lastFailedAt` on the dependent's outcome).
3. **Rerun the prerequisite task** — keeping the same `workflowId` is automatic when you use the standard rerun action.
4. Once the prerequisite reaches `completed`, the dependent unblocks itself; no action is required on the dependent. Its dependency resolution becomes `satisfied_after_rerun`.
5. If the prerequisite cannot be recovered:
   - **Bypass the dependency wait** on the dependent if the remaining prerequisites are no longer required, or
   - **Cancel the dependent run** if it should not proceed without the prerequisite, then create a new dependent run with corrected prerequisites.
6. Dependencies are create-time only and cannot be edited or retargeted after create. To change which `workflowId` a dependent waits on, cancel and recreate.
