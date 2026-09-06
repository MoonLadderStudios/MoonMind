# Workflow Dependencies

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Workflow lifecycle, API, orchestration, and dashboard contributors  
**Authority:** Inter-UserWorkflow dependency declaration, successful-completion waiting, signaling, bypass, and durable outcome semantics. Publication inheritance and code-transfer proof remain with their providing owners.  
**Owning Surface:** Workflow dependency admission and durable wait/notification boundary  
**Related Implementation:** `MoonMind.UserWorkflow`, DependencyResolved, BypassDependencies, and the existing dependsOn execution contract.

**Related Docs:** `docs/Api/ExecutionsApiContract.md`, `docs/Workflows/WorkflowArchitecture.md`, `docs/Workflows/WorkflowCancellation.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/UI/WorkflowConsoleArchitecture.md`, `docs/Workflows/WorkflowPublishing.md`, `docs/Workflows/PrMergeAutomation.md`

## 1. Purpose

Workflow Dependencies let one MoonMind.UserWorkflow wait on another independently durable, visible, cancelable, rerunnable execution. They express ordering and successful prerequisite completion, not publication-policy inheritance or implicit transfer of code into a workspace.

The single-policy batch contract is defined in [Workflow Publishing](WorkflowPublishing.md). A batch can create dependent workflows under its frozen publication scope, but a dependency edge alone never changes the policy of an independently authored workflow.

## 2. Contract Summary

Create requests may declare prerequisite workflow IDs in `payload.task.dependsOn`, normalized to `initialParameters.task.dependsOn`. Targets are existing MoonMind.UserWorkflow executions. WorkflowId is the durable target; runId is diagnostic/evidence identity, not a dependency target. Compatibility taskId equals workflowId for Temporal-backed work.

A prerequisite is satisfied only when it reaches MoonMind terminal state completed. Failed, canceled, terminated, timed_out, or runtime-unresolvable outcomes keep the dependent in waiting_on_dependencies until that same workflowId later completes or an operator cancels/bypasses the wait. A prerequisite failure does not automatically fail the dependent.

Dependencies are non-transitive at the contract level: C depending on B waits on B, not a separately expanded transitive graph.

### 2.1 Contract boundaries

The contract supports create-time declaration, UserWorkflow-to-UserWorkflow edges, at most 10 direct IDs, no editing of admitted edges, and no cross-workflow-type or general template-instantiated graph model. A preset's trusted sequential child-submission operation may create each new execution against already-created predecessor IDs under the same ordinary API rules; it does not introduce a second graph engine.

## 3. API Contract

```json
{
  "type": "task",
  "payload": {
    "task": {
      "instructions": "Run integration tests",
      "dependsOn": ["mm:01ABC...", "mm:01DEF..."]
    }
  }
}
```

This fragment omits the independently validated source/runtime/publication context. A dependsOn list never supplies those authorities.

### 3.1 Normalization and validation

The API requires an optional array of strings, trims values, removes blanks and duplicates preserving order, enforces the post-normalization limit of 10, and verifies every referenced execution exists, is visible to the caller, and has the supported workflow type. Reject run IDs, malformed self-reference, and unauthorized targets. Persist normalized IDs with the new execution.

### 3.2 Acyclicity

New edges point only from a new execution to pre-existing executions. This create-time constraint supplies acyclicity without requiring arbitrary deep graph traversal. Self-reference and invalid targets still fail validation.

### 3.3 Durable dependency edge data

Persist forward/reverse edges, startup resolution state, notification ownership, and safe list/detail metadata. Full edge details belong in existing read models/artifacts rather than unbounded indexed attributes.

## 4. Workflow Execution Behavior

### 4.1 Lifecycle

The normal order is scheduled when applicable, initializing, waiting_on_dependencies, then the ordinary planning/executing lifecycle. No declared dependencies means the gate is skipped.

### 4.2 Dependency resolution model

The canonical mechanism is explicit dependency-resolution signaling, not direct awaiting of an unrelated workflow result as the inter-workflow contract.

After initialization, read declared IDs and durably record the gate. Perform an immediate authoritative status check. Already-completed prerequisites satisfy immediately; non-success terminal outcomes record waiting_for_successful_rerun. Wait for unresolved prerequisites through workflow-native deterministic waits, interruptible by cancellation.

The gate clears when all prerequisites complete or an operator uses a supported bypass/skip. Missing source/code handoff is a separate requirement under section 7 and can still block active work.

### 4.3 Dependency resolution signals

A terminal prerequisite notifies dependents with an idempotent DependencyResolved signal carrying prerequisiteWorkflowId, terminalState, available Temporal close status, resolvedAt, and bounded message/failure context.

Duplicates, stale, and unexpected notifications are ignored safely. A bounded reconciliation path repairs uncertain delivery against durable state; an indefinite custom polling loop is not the primary design.

### 4.4 Waiting semantics

Waiting state is local to the dependent's durable workflow history, deterministic, cancellation-aware, and safe across worker restart/replay. Transient process memory is not the authority.

### 4.5 Control semantics

Canceling or pausing a dependent does not cancel, terminate, or mutate its prerequisites. Pausing does not suppress notifications: outcomes continue to be recorded, but the dependent does not pass its gate or start active work until resumed.

Non-success prerequisite results while paused retain waiting_for_successful_rerun. A scheduled execution begins dependency resolution only after leaving the scheduling gate. Ordinary workflow timeouts still apply to long waits.

### 4.6 Wait-through-rerun behavior

Each non-success outcome records workflowId, current domain/Temporal close status, failure category/count, lastFailedAt, human-readable message, and waiting_for_successful_rerun. Failure count increments only for a distinct observed non-success outcome, not repeated delivery.

If that workflowId later completes, mark satisfied_after_rerun and remove it from unresolved dependencies. Once all are satisfied, the dependency gate clears.

The only supported exits without prerequisite success are dependent cancellation, BypassDependencies, or SkipDependencyWait under the appropriate operator authority. Standard workflow timeout remains applicable.

Resolution ratchets: duplicate completed observations do nothing; duplicate failures with the same resolvedAt do not increment counts; a later stale failure cannot revert satisfied, satisfied_after_rerun, or bypassed state.

A successful later run satisfies the ordering gate by workflowId, but required code evidence must still identify the actual successful run/candidate. Do not restore an older failed run's workspace simply because the durable ID matches.

### 4.7 Continue-As-New and replay compatibility

Preserve declared IDs, resolved/unresolved outcomes, wait start/accumulated duration, failure metadata, and operator dispositions across rollover. Workflow command changes use the existing Temporal version/replay contract.

Publication scope, source bindings, and required handoff references are independently preserved by their owners. Rollover does not re-resolve a changed Auto default or change an independently authored prerequisite's policy.

## 5. Visibility, Metadata, and Artifacts

### 5.1 State and visibility

waiting_on_dependencies remains canonical domain state for this gate. Typed registered Search Attributes contain bounded flags/counts; full IDs/outcomes stay in memo, read models, or artifacts.

### 5.2 Read model requirements

Expose declared prerequisites, known current statuses, whether this gate is blocking, reverse dependents, and compact authorized titles/links. Publication and code-handoff status is separately identified rather than folded into a generic dependency success label.

### 5.3 Terminal summary

Every UserWorkflow result and `reports/run_summary.json` contains a stable dependencies block with declaredIds, waited, waitDurationMs, resolution, failedDependencyId, and outcomes.

```json
{
  "dependencies": {
    "declaredIds": [],
    "waited": false,
    "waitDurationMs": 0,
    "resolution": "not_applicable",
    "failedDependencyId": null,
    "outcomes": []
  }
}
```

Top-level resolutions are not_applicable, satisfied, satisfied_after_rerun, bypassed, and manual_override. Active per-dependency outcomes may be waiting_for_successful_rerun. Applicable outcome fields include workflowId, terminalState, closeStatus, resolvedAt, resolution, failureCount, lastFailedAt, failureCategory, and message.

The summary never claims a merge or source transfer from terminal state alone. Those facts reference their owning publication/workspace evidence.

## 6. Dashboard Expectations

### 6.1 Create flow

The shared Create form offers an authorized existing-UserWorkflow picker with duplicate prevention, the 10-ID limit, and field-addressable errors. It explains waiting through failed/canceled/timed-out prerequisites until a successful rerun or explicit cancellation/bypass.

Selecting a prerequisite may offer a verified candidate as a proposed source, but does not silently replace the visible repository, base branch, or publication selection. Any adopted source role uses normal validation and provenance.

### 6.2 List and detail surfaces

Map waiting_on_dependencies to dashboard waiting. The Dependencies panel links titles/statuses/outcomes and shows “Prerequisite failed; waiting for successful rerun,” failure count, and last failure where applicable. Prerequisites expose reverse Dependents; compact lists may show blocked-by summaries.

When ordering is satisfied but required code is unavailable, display that distinct source/handoff blocker. An open PR or review-clean outcome is not labeled merged. Bypassing the ordering wait does not grant missing repository authority or silently waive source-safety checks.

## 7. Boundary With Other Ordering Mechanisms

Dependencies are inter-workflow ordering for independently inspectable UserWorkflows. Plan-node/Skill ordering within a run belongs to the plan. Directly awaited parent-owned subordinate work uses child workflows.

Dependencies do not import an upstream internal DAG, merge workspaces, combine branches, or establish publication inheritance.

### 7.1 Publication scopes and fan-out

A batch's one authored publication intent follows its declared child-creation lineage and authenticated admission, not dependsOn edges. An intermediate coordinator preserves the frozen scope even when its own compiled mode is None.

Independently authored prerequisites keep their own policy. A caller cannot use a dependency link to change them or inherit more permissive credentials/publication rights. Created children can remain independently visible and durable while sharing the batch's admitted policy through recorded lineage.

### 7.2 Code availability is a separate handoff

A workflow completing under PR-only can leave its changes on an unmerged head. A fix-only review loop can complete without merging. A None workflow can save local results without publishing. None of those outcomes alone places code on another child's selected base.

A composition requiring predecessor code declares one supported handoff:

| Handoff | Required evidence |
| --- | --- |
| Merge into the shared base | Verified merged PR/current applicable base under the publishing owner |
| Candidate/checkpoint transfer | Exact successful run/candidate and authorized immutable saved-work/restore references |
| Serialized shared-branch sequence | Qualified exclusive/ordered branch update, refreshed remote state, exact candidate expectation, and conflict handling |

No new selector is required for routine presets; their metadata declares the handoff. Unsupported PR-only/None/fix-only combinations fail before known parent/issue/child effects. If dynamic evidence is unavailable later, expose a source/handoff blocker rather than running against stale code or silently changing policy.

PR-and-merge parents remain awaiting_external until the required merge lifecycle finishes, so their completed objective can establish the intended merged prerequisite when validated. The original parent workflowId stays the dependency target. Coordinator enqueue completion, however, does not prove all descendants completed or merged; a dependent requiring those outputs must use the declared aggregation/handoff, not infer it from the coordinator's terminal state.

### 7.3 Bypass boundaries

Bypass or SkipDependencyWait changes the ordering disposition only. It does not authorize absent code, grant a new repository/branch, override explicit None, or approve unverified checkpoint content. A composition that can proceed without predecessor code must explicitly admit that different source/objective through the existing authoring path.

## 8. Edge Cases and Failure Modes

Already-completed prerequisites resolve immediately. Already-failed prerequisites remain waiting for a successful rerun. Unknown runtime state is dependency_unresolved, not success. Duplicate notifications are idempotent, stale failures cannot reverse success, delivery uncertainty uses bounded reconciliation, and long waits preserve state across Continue-As-New and remain subject to normal timeout/cancel.

Cross-run code handoffs, a changed repository base, unavailable saved artifacts, merged-versus-open PRs, partial child dispatch, and coordinator-only success retain their own explicit evidence and failure boundaries. Dependencies do not manufacture atomic batch behavior or rollback completed effects.

### Conformance

Tests use actual create/read/signal/workflow and workspace/publication boundaries to prove normalization, ownership, acyclicity, limit enforcement, wait-through-rerun, pause/cancel/bypass, stale-signal ratcheting, and rollover.

Additional regressions prove that dependency edges do not change publication policy; nested coordinators retain scoped intent; PR-only, fix-only, and None do not imply predecessor code on the base; the exact successful run supplies restored candidate evidence; missing handoffs block safely; and operator bypass cannot bypass repository/source authorization. A successful ordering test alone is not code-transfer conformance.
