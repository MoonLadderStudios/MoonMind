# Workflow Dependencies — Operator Guide

Use this guide to configure, diagnose, and recover dependency waits. [Workflow Dependencies](WorkflowDependencies.md) owns admission, signals, state, and code-handoff semantics.

## 1. Configure prerequisites

At creation, put up to 10 existing, visible `MoonMind.UserWorkflow` IDs in `payload.workflow.dependsOn`, or select them in Create. Use `workflowId`, not `runId`. The API trims/deduplicates IDs; prerequisites must already exist. See the [request example](WorkflowDependencies.md#3-api-contract).

Edges cannot be edited or retargeted. Each dependent is a top-level execution with its own cancel/rerun controls. C depending on B waits on B, without expanding B's dependencies. There is no dependency-based priority, automatic prerequisite cancellation/rerun, general template graph, or automatic remediation workflow.

A dependency orders work; it does not transfer code, grant publication authority, or prove all a coordinator's descendants finished. Before relying on predecessor code, establish the [declared handoff](WorkflowDependencies.md#72-code-availability-is-a-separate-handoff), especially for PR-only, fix-only, and None workflows.

## 2. Read the waiting state

The gate runs after initialization and before planning/execution. It performs an immediate status reconciliation, receives durable `DependencyResolved` signals, and reconciles uncertain delivery every 30 seconds. All prerequisites must reach MoonMind `completed`.

| Observation | Operator interpretation |
| --- | --- |
| `completed` | This prerequisite is satisfied |
| `failed`, `canceled`, `terminated`, or `timed_out` | `waiting_for_successful_rerun`; the dependent does not auto-fail |
| Not found/runtime-unresolvable | Still waiting, with `failureCategory = dependency_unresolved`; not success |
| Same declared ID completes after a failure | `satisfied_after_rerun` |

Use Workflow Detail or `reports/run_summary.json` → `dependencies.outcomes`. Each outcome identifies the prerequisite, terminal/close state, failure category/message, distinct `failureCount`, and `lastFailedAt`. Repeated delivery is not a new failure. The top-level resolution is `satisfied_after_rerun` if any prerequisite recovered, otherwise `satisfied`.

## 3. Recover a failed prerequisite

1. Open the failed prerequisite from the dependency panel; diagnose its original failure and retained work.
2. Use its supported recovery/rerun operation and follow the returned destination identity.
3. Check that identity before waiting again: terminal [RequestRerun](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7-new-run-semantics) creates a new linked `workflowId` and leaves the source closed. Its success cannot satisfy an edge targeting the old ID. Cancel and recreate the dependent with the returned ID if that destination is required. Existing edges never follow rerun lineage automatically.
4. If the prerequisite is no longer needed, explicitly bypass/skip the dependent's wait; otherwise cancel the dependent. Do not wait for prerequisite failure to fail the dependent automatically. Normal workflow timeout still applies.

Bypass changes ordering only. It does not grant missing code, repository permissions, or publication authority. Re-admit a changed source/objective where necessary.

## 4. Pause and cancel behavior

| Action on dependent | Effect |
| --- | --- |
| Cancel | Cancels only this dependent; prerequisites remain untouched |
| Bypass dependency wait | Clears the gate with `resolution = bypassed` |
| `SkipDependencyWait` | Operator-only override with `resolution = manual_override` |
| Pause | Records incoming outcomes but cannot advance to planning/execution |
| Resume | Advances if all prerequisites resolved; otherwise resumes waiting with outcomes intact |

Canceling or terminating a prerequisite does not cancel its dependent. Signals continue while paused, and non-success observations remain waiting outcomes.

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

### A dependent is stuck in `waiting_on_dependencies`

- Check declared IDs and actual prerequisite state in detail. A completed rerun under a different ID is the most important identity trap.
- If the correct prerequisite completed, inspect service `dependency_signal_fan_out`/`dependency_signal_delivery_failed` events and their failure counts. Fan-out is bounded, best-effort, and continues past closed/missing dependents.
- Check `execution.dependency_status_snapshot` Activity failures and Temporal worker/container health. With workers down, signals queue and reconciliation cannot execute.
- Inspect undeclared-signal and reconciliation-mismatch diagnostics rather than treating them as completion. The [workflow gate](../../moonmind/workflows/temporal/workflows/run.py) and [notification service](../../moonmind/workflows/temporal/service.py) own these paths.

### A legacy dependent failed on prerequisite failure

Inspect `DependencyFailureError` and `failedDependencyId` in the summary's `dependencies` block. Recover the prerequisite, then create a new dependent targeting the successful destination ID. Retained histories may keep fail-fast behavior; restarting the worker does not migrate them.

### Reverse lookup shows no dependents

Check the exact `workflowId` against `execution_dependencies.prerequisite_workflow_id`. Confirm both `initialParameters.workflow.dependsOn` and durable edges were persisted at creation. A missing edge is not repaired by sending an undeclared signal.

## 7. Deployment Integrity and Replay Compatibility

The dependency gate is deployed under cooperating Temporal patches:

- **`dependency-gate-v1`** — installs the dependency gate itself. Workflows started before this patch was deployed skip the gate entirely (backward compatible).
- **`run-canonical-dependency-parameters-v1`** — reads dependencies from the admitted `workflow` envelope. Histories that already passed initialization without this marker retain their recorded command sequence; restarting a worker does not retroactively gate work that already launched. Cancel and recreate an affected dependent with its prerequisites to enforce the wait.
- **`dependency-wait-through-rerun-v1`** — installs the unified wait-through-rerun behavior. The first time a workflow under this patch records a prerequisite outcome, Temporal records a marker that pins the workflow to the new behavior across replays.
  - Workflows started **after** `dependency-wait-through-rerun-v1` was deployed never auto-fail on a prerequisite failure.
  - Workflows whose history predates `dependency-wait-through-rerun-v1` continue to use the legacy fail-fast code path until they terminate. Their `_dependency_failure` paths and `dependency_gate_failed` events remain accurate for those runs only.

Future dependency-gate changes must keep replay behavior explicit through patch gates, replay tests, or a documented cutover.

---
