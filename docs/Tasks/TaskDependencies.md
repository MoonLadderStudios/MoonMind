# Task Dependencies

Related: `docs/Api/ExecutionsApiContract.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskCancellation.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/UI/MissionControlArchitecture.md`

---

## 1. Purpose

This document defines the desired-state contract for **Task Dependencies** in MoonMind.

Task dependencies allow one `MoonMind.Run` execution to wait on one or more other `MoonMind.Run` executions before entering its own active work. This supports multi-stage orchestration across separate runs while keeping each execution independently durable, observable, cancelable, rerunnable, and separately inspectable.

---

## 2. Contract Summary

- A task-shaped create request may declare prerequisite execution IDs in `payload.task.dependsOn`.
- The executions API normalizes this into `initialParameters.task.dependsOn` for `MoonMind.Run`.
- Each `dependsOn` value is a `workflowId` for an existing `MoonMind.Run` execution.
- For Temporal-backed task surfaces, `taskId == workflowId`.
- `runId` is diagnostic only and is never a valid dependency target.
- A prerequisite is satisfied only when it reaches terminal MoonMind state `completed`.
- Any other prerequisite terminal outcome — `failed`, `canceled`, `terminated`, `timed_out`, or runtime unresolvable — keeps the dependent run blocked in `waiting_on_dependencies` until the same prerequisite `workflowId` later reaches `completed`. The dependent never auto-fails on a prerequisite failure. The only way out without prerequisite success is for an operator to cancel the dependent run or bypass the dependency.
- Dependencies are **non-transitive** at the contract level: if C depends on B and B depends on A, C waits on B only.

### 2.1 Contract boundaries

The current contract is intentionally narrow:

- create-time dependency declaration only,
- `MoonMind.Run` → `MoonMind.Run` dependencies only,
- maximum of **10** direct dependency IDs,
- no dependency editing after create,
- no cross-workflow-type dependencies,
- no template-instantiated dependency graphs.

---

## 3. API Contract

Task dependencies are part of the Temporal-backed create flow through `/api/executions`.

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
````

The router normalizes this into `initialParameters.task.dependsOn` for `MoonMind.Run`.

### 3.1 Normalization and validation

At create time, the API must:

1. treat `dependsOn` as optional,
2. when present, require an array of strings,
3. trim whitespace, remove blank entries, and deduplicate while preserving order,
4. enforce the 10-item maximum after normalization,
5. require each ID to resolve to an existing execution the caller is authorized to reference,
6. require each target execution to be `MoonMind.Run`,
7. reject `runId` values or other non-`workflowId` identifiers,
8. reject malformed self-reference if encountered,
9. persist the normalized dependency IDs durably with the dependent execution.

### 3.2 Acyclicity

Under this contract, dependencies are create-time only and may target only already-existing executions. New edges therefore point from the newly created run to pre-existing runs, which keeps the dependency graph acyclic by construction.

The core contract does not depend on deep transitive cycle walks. The API should defend against malformed self-reference and invalid targets, but acyclicity is primarily a property of the create-time-only design.

### 3.3 Durable dependency edge data

The platform must persist dependency edge data in a form that supports:

* forward lookup from a dependent run to its prerequisites,
* reverse lookup from a prerequisite run to its downstream dependents,
* startup status checks for dependent runs,
* downstream notification when a prerequisite completes,
* list/detail UI rendering for both prerequisites and dependents.

---

## 4. Workflow Execution Behavior

`MoonMind.Run` enforces dependencies before it proceeds into its normal post-initialization work.

### 4.1 Lifecycle

For dependency-aware runs, the relevant lifecycle is:

1. `scheduled` *(when delayed start is used)*
2. `initializing`
3. `waiting_on_dependencies`
4. normal post-initialization path (`planning`, `executing`, or other standard `MoonMind.Run` flow)
5. terminal state

If `initialParameters.task.dependsOn` is absent or empty, the workflow skips `waiting_on_dependencies` and proceeds directly into its normal path.

### 4.2 Dependency resolution model

The canonical mechanism for inter-run task dependencies is **explicit dependency-resolution signaling**. Dependent runs do not treat direct waiting on external workflow results as the primary dependency contract.

When dependencies are present, the workflow must:

1. read `initialParameters.task.dependsOn` after initialization,
2. transition `mm_state` to `waiting_on_dependencies`,
3. initialize local dependency-tracking state for all declared prerequisite IDs,
4. perform an immediate status check for each prerequisite using durable execution state,
5. mark prerequisites already in `completed` as satisfied immediately,
6. record any prerequisite already in a non-success terminal state as a retryable outcome (`waiting_for_successful_rerun`) and continue waiting,
7. wait for explicit dependency-resolution notifications for the remaining unresolved prerequisites,
8. proceed only after all declared prerequisites reach `completed` (possibly after one or more prerequisite reruns), or after an operator cancels the dependent run or bypasses the dependency.

### 4.3 Dependency resolution signals

The platform must use callback-first notification for ongoing waits:

* when a prerequisite reaches terminal state, the system emits a `DependencyResolved` signal to each dependent workflow,
* the dependent workflow updates its local dependency state on receipt of that signal,
* the dependent workflow advances only when all prerequisites are satisfied.

The `DependencyResolved` signal must be structured and idempotent. At minimum it must include:

* `prerequisiteWorkflowId`,
* `terminalState`,
* Temporal close status when available,
* `resolvedAt`,
* an optional compact message or failure category.

Duplicate, stale, or unexpected signals must be ignored safely.

Timer-based reconciliation may exist as a bounded repair path when signal delivery is uncertain, but indefinite polling is not part of the dependency contract.

### 4.4 Waiting semantics

A dependent workflow must wait on its **local dependency state** using Temporal workflow-native waiting primitives.

Dependency waiting must be:

* durable,
* deterministic,
* interruptible by cancel,
* safe across replay,
* safe across worker restarts.

The workflow waits on dependency state transitions inside its own history. It does not rely on transient in-memory coordination outside workflow state.

### 4.5 Control semantics

Cancellation and pause behavior during dependency waiting is:

* canceling the dependent run cancels only that run,
* dependency waiting never cancels, terminates, or mutates prerequisite runs,
* pausing the dependent run does not stop prerequisite runs,
* pausing the dependent run does not suppress receipt of dependency-resolution signals,
* while paused, the workflow continues recording dependency outcomes, but it must not leave the dependency gate or enter active planning/execution until unpaused,
* a prerequisite non-success terminal outcome while the dependent is paused does not fail the dependent; the outcome is recorded as `waiting_for_successful_rerun` and the dependent stays blocked until prerequisites complete, the dependent is canceled, or the dependency is bypassed,
* scheduled runs do not begin dependency resolution until they leave `scheduled` and enter `initializing`.

### 4.6 Wait-through-rerun behavior

A dependent run clears the dependency gate only when **every** declared prerequisite reaches MoonMind terminal state `completed`.

A prerequisite non-success terminal outcome is **not** a dependency failure under the current contract. The following terminal outcomes keep the dependent in `waiting_on_dependencies`:

* `failed`,
* `canceled`,
* `terminated`,
* `timed_out`,
* runtime inability to resolve a dependency.

For each non-success terminal outcome the workflow records a structured per-dependency entry that surfaces:

* `workflowId`,
* latest prerequisite terminal MoonMind state,
* latest prerequisite Temporal close status when available,
* failure category,
* `failureCount` — number of distinct non-success terminal observations for this prerequisite,
* `lastFailedAt` — most recent non-success terminal timestamp,
* human-readable message,
* `resolution = "waiting_for_successful_rerun"`.

If the same prerequisite later reaches `completed` (under the same `workflowId` after rerun), the per-dependency entry transitions to `resolution = "satisfied_after_rerun"` and the prerequisite is removed from the unresolved set. Once every prerequisite is satisfied, the gate clears.

The only ways to leave the dependency gate without prerequisite success are:

* an operator cancels the dependent run,
* an operator bypasses the dependency wait (`BypassDependencies` signal),
* an operator manually skips the wait (`SkipDependencyWait` update).

A dependent run that has been waiting for a rerun and never receives one will stay in `waiting_on_dependencies` until standard workflow timeouts apply or an operator intervenes.

Stale or duplicate dependency-resolution signals must be ignored:

* a `completed` signal that arrives after the prerequisite is already recorded as `satisfied` or `satisfied_after_rerun` is a no-op,
* a non-success terminal observation with the same `resolvedAt` as the prerequisite's already-recorded `lastFailedAt` does not increment `failureCount`,
* a non-success terminal signal that arrives after the prerequisite is already recorded as `satisfied`, `satisfied_after_rerun`, or `bypassed` is a no-op and never reverts the prior resolution.

### 4.7 Continue-As-New and replay safety

Dependency behavior must be safe across replay and Continue-As-New.

When a dependency-aware workflow continues as new, it must preserve:

* declared dependency IDs,
* already-resolved dependency outcomes,
* unresolved dependency IDs,
* dependency wait start time and accumulated wait duration,
* any dependency failure metadata already determined.

Changes to dependency orchestration logic must use Temporal-safe workflow versioning so in-flight workflow histories remain replayable.

---

## 5. Visibility, Metadata, and Artifacts

Dependency metadata must be surfaced consistently across workflow state, execution serialization, read models, and terminal artifacts.

### 5.1 State and visibility

* `mm_state` remains the canonical state field for list filtering.
* `waiting_on_dependencies` is the canonical domain state for runs blocked on prerequisites.
* Dependency-related Search Attributes, when used, must use typed Temporal Search Attribute APIs and schema-correct value types.
* Indexed dependency metadata must remain bounded and filter-oriented, such as small flags, counts, or compact state markers.
* Full dependency ID lists and richer per-dependency details belong in memo fields, read models, or artifacts rather than unbounded indexed fields.

### 5.2 Read model requirements

Execution serialization and list/detail read models must expose, at minimum:

* declared prerequisite IDs,
* current prerequisite statuses when known,
* whether the run is currently blocked on dependencies,
* downstream dependents for a prerequisite run,
* compact linked-run titles or summaries when available.

### 5.3 Terminal summary

Typed run results and `reports/run_summary.json` must include a stable `dependencies` block for every `MoonMind.Run`, including runs with no declared dependencies.

At minimum, the `dependencies` block must include:

* `declaredIds`,
* `waited`,
* `waitDurationMs`,
* `resolution`,
* `failedDependencyId`,
* `outcomes`.

Recommended top-level `resolution` values are:

* `not_applicable` — no dependencies declared,
* `satisfied` — every prerequisite cleared on its first observed terminal outcome,
* `satisfied_after_rerun` — at least one prerequisite reached `completed` only after one or more prior non-success terminal outcomes,
* `bypassed` — operator bypassed the dependency wait,
* `manual_override` — operator manually skipped the dependency wait.

Per-dependency outcomes carry their own `resolution` field that may also include the non-terminal marker `waiting_for_successful_rerun` while the workflow is still active.

When no dependencies are declared, the stable empty shape is:

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

When dependencies are declared, the summary should capture per-dependency outcomes when known. Each per-dependency outcome carries `workflowId`, `terminalState`, `closeStatus`, `resolvedAt`, `resolution`, `failureCount`, `lastFailedAt`, `failureCategory`, and `message` fields when applicable.

Example: every prerequisite cleared on first observation.

```json
{
  "dependencies": {
    "declaredIds": ["mm:01ABC...", "mm:01DEF..."],
    "waited": true,
    "waitDurationMs": 18342,
    "resolution": "satisfied",
    "failedDependencyId": null,
    "outcomes": [
      {
        "workflowId": "mm:01ABC...",
        "terminalState": "completed",
        "closeStatus": "completed",
        "resolvedAt": "2026-04-03T17:24:16Z",
        "resolution": "satisfied",
        "failureCount": 0,
        "lastFailedAt": null
      },
      {
        "workflowId": "mm:01DEF...",
        "terminalState": "completed",
        "closeStatus": "completed",
        "resolvedAt": "2026-04-03T17:24:18Z",
        "resolution": "satisfied",
        "failureCount": 0,
        "lastFailedAt": null
      }
    ]
  }
}
```

Example: a prerequisite failed and was rerun to success.

```json
{
  "dependencies": {
    "declaredIds": ["mm:01ABC..."],
    "waited": true,
    "waitDurationMs": 945000,
    "resolution": "satisfied_after_rerun",
    "failedDependencyId": null,
    "outcomes": [
      {
        "workflowId": "mm:01ABC...",
        "terminalState": "completed",
        "closeStatus": "completed",
        "resolvedAt": "2026-04-03T17:39:18Z",
        "resolution": "satisfied_after_rerun",
        "failureCount": 1,
        "lastFailedAt": "2026-04-03T17:24:16Z",
        "message": "Prerequisite completed after rerun."
      }
    ]
  }
}
```

While the workflow is active and a prerequisite is currently failed, the per-dependency outcome carries the non-terminal marker `waiting_for_successful_rerun`:

```json
{
  "workflowId": "mm:01ABC...",
  "terminalState": "failed",
  "closeStatus": "failed",
  "resolvedAt": null,
  "resolution": "waiting_for_successful_rerun",
  "failureCount": 1,
  "lastFailedAt": "2026-04-03T17:24:16Z",
  "failureCategory": "dependency_failed",
  "message": "Prerequisite execution 'mm:01ABC...' reached terminal state 'failed'; waiting for successful rerun."
}
```

---

## 6. Mission Control Expectations

### 6.1 Create flow

Mission Control should expose dependency configuration on `/tasks/new`.

The create UX should provide:

* a dependency picker for existing `MoonMind.Run` executions,
* client-side enforcement of the 10-item limit,
* duplicate prevention,
* clear validation messaging for invalid targets,
* clear messaging that the new run stays blocked while a prerequisite is running, failed, canceled, terminated, timed out, or unresolvable, and unblocks once the prerequisite completes successfully — and that the only ways out without prerequisite success are canceling the dependent run or bypassing the dependency wait.

### 6.2 List and detail surfaces

* `waiting_on_dependencies` maps to dashboard status `waiting`.
* Task detail shows a **Dependencies** panel with prerequisite links, titles, statuses, and terminal outcomes.
* When a per-dependency outcome carries `resolution = "waiting_for_successful_rerun"`, the panel must surface a clear "Prerequisite failed; waiting for successful rerun" indicator alongside the failure count and last-failed timestamp.
* Prerequisite detail shows a **Dependents** panel or equivalent reverse-lookup view.
* List and quick-view surfaces may show a compact blocked-by summary when useful.

---

## 7. Boundary With Other Ordering Mechanisms

Task dependencies are **inter-workflow** dependencies between separate top-level `MoonMind.Run` executions.

Use task dependencies when the involved runs must remain:

* independently visible,
* independently durable,
* independently cancelable,
* independently rerunnable,
* separately inspectable.

Do **not** use task dependencies for:

* plan-node or skill-node ordering inside a single run,
* direct parent-owned subordinate work that should be awaited inside one orchestration history.

For parent-owned subordinate work that should be directly awaited, use **child workflows**.

Task dependencies block one workflow on the terminal outcome of another workflow. They do not import, merge, or replace the upstream run’s internal plan DAG.

---

## 8. Edge Cases and Failure Modes

* **Prerequisite already completed:** resolve immediately and continue if all prerequisites are satisfied.
* **Prerequisite already terminal and non-successful:** record a `waiting_for_successful_rerun` outcome and keep the dependent blocked. The dependent does not auto-fail.
* **Prerequisite cycles between failed and completed across reruns:** the gate ratchets — once a prerequisite is recorded as `satisfied` or `satisfied_after_rerun`, subsequent stale failure observations are ignored.
* **Duplicate or stale notifications:** ignore idempotently. Duplicate non-success observations sharing the same `resolvedAt` do not increment `failureCount`.
* **Missing runtime target after validation:** record `waiting_for_successful_rerun` with `failureCategory = "dependency_unresolved"`. The dependent stays blocked until the prerequisite resolves to a `completed` outcome, the dependent is canceled, or the dependency is bypassed.
* **Signal delivery uncertainty:** use bounded reconciliation against durable execution state; do not rely on unbounded polling loops.
* **Workflow timeout while waiting:** standard workflow timeout behavior still applies. A run that waits indefinitely can still be timed out by ordinary workflow timeouts or operator cancel.
* **Long waits or long chains:** preserve dependency context across Continue-As-New; the direct-dependency limit remains 10 per run.
