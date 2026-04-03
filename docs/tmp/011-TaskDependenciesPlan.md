# Task Dependencies Implementation Plan

Related: `docs/Tasks/TaskDependencies.md`, `docs/Api/ExecutionsApiContract.md`, `docs/UI/MissionControlArchitecture.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskCancellation.md`

---

## 1. Purpose

This document defines the implementation plan for **Task Dependencies** in MoonMind.

`docs/Tasks/TaskDependencies.md` is the desired-state contract. This document translates that contract into concrete engineering work, implementation boundaries, acceptance criteria, and rollout requirements.

This document is intentionally forward-looking. It does not track completion status.

---

## 2. Desired Outcome

When this plan is complete, MoonMind supports create-time task dependencies between separate top-level `MoonMind.Run` executions.

A dependent run may declare up to 10 prerequisite `workflowId` values in `payload.task.dependsOn`. The API normalizes and validates those IDs, persists both the execution-local dependency list and durable dependency edges, and starts a `MoonMind.Run` that:

1. initializes normally,
2. enters `waiting_on_dependencies` when dependencies are declared,
3. performs an immediate durable status reconciliation for each prerequisite,
4. waits durably for explicit dependency-resolution notifications for any unresolved prerequisites,
5. proceeds only when every prerequisite reaches terminal MoonMind state `completed`,
6. fails with structured dependency metadata when any prerequisite reaches a non-success terminal outcome.

Dependency metadata is surfaced consistently in workflow state, read models, list/detail APIs, Mission Control, and terminal summary artifacts.

---

## 3. V1 Scope

The first production version of task dependencies remains intentionally narrow:

- create-time dependency declaration only,
- `MoonMind.Run` → `MoonMind.Run` dependencies only,
- maximum of 10 direct dependency IDs,
- no dependency edits after create,
- no cross-workflow-type dependencies,
- no template-instantiated dependency graphs,
- no transitive waiting beyond direct prerequisites.

---

## 4. Locked Design Decisions

The following design decisions are part of this implementation plan and should be treated as locked unless the desired-state contract changes.

### 4.1 Independent top-level runs

Task dependencies are dependencies between separate top-level `MoonMind.Run` executions.

Use task dependencies when runs must remain:

- independently visible,
- independently durable,
- independently cancelable,
- independently rerunnable,
- separately inspectable.

Do not use task dependencies for direct parent-owned subordinate work. That case should use child workflows.

### 4.2 Direct-dependency contract only

Dependencies are non-transitive at the contract level. If C depends on B and B depends on A, C waits on B only.

### 4.3 Success semantics

A prerequisite satisfies the dependency gate only when it reaches MoonMind terminal state `completed`.

Any other terminal prerequisite outcome is a dependency failure, including:

- `failed`,
- `canceled`,
- `terminated`,
- `timed_out`,
- runtime inability to resolve a dependency after successful create-time validation.

### 4.4 Acyclicity by create-time-only edges

Dependencies are create-time only and may target only already-existing executions. New dependency edges therefore point from a newly created run to pre-existing runs.

The plan does not rely on deep transitive cycle walking as the primary correctness mechanism. The API must still reject malformed self-reference and invalid targets.

### 4.5 Signal-driven dependency resolution

The canonical dependency orchestration mechanism is **explicit dependency-resolution signaling**.

A dependent run must not treat direct waiting on external workflow results as the primary dependency contract. The dependent workflow waits on its own local dependency state, and that local state is updated by:

- initial reconciliation against durable execution state, and
- explicit `DependencyResolved` signals from prerequisite terminal transitions.

### 4.6 Workflow-native wait primitives only

Dependency waiting inside workflow code must use Temporal workflow-native waiting primitives.

Use:

- `workflow.wait_condition(...)` to wait on local dependency state,
- `workflow.wait(...)` when task-based waiting is required inside workflow code.

Do not use `asyncio.wait(...)` directly in workflow code for dependency orchestration.

### 4.7 Typed Search Attributes only

Dependency-related Search Attribute writes must use typed Temporal Search Attribute APIs.

Bounded indexed dependency metadata is allowed. Full dependency ID lists and rich per-dependency details belong in memo fields, read models, or artifacts rather than unbounded indexed fields.

### 4.8 Replay-safe deployment posture

Changes to dependency workflow logic must be replay-safe.

For the current rollout, patching is the required minimum. The implementation should also be compatible with later migration to Worker Versioning as the preferred production deployment model.

---

## 5. Target Execution Sequence

The intended end-to-end flow is:

1. A client submits a task-shaped create request with `payload.task.dependsOn`.
2. The executions API normalizes and validates the dependency IDs.
3. The API persists:
   - the normalized dependency list in `initialParameters.task.dependsOn`,
   - durable dependency edges for forward and reverse lookup.
4. The API starts a `MoonMind.Run`.
5. The workflow initializes and checks whether dependencies are present.
6. If dependencies exist, the workflow:
   - sets `mm_state` to `waiting_on_dependencies`,
   - initializes local dependency-tracking state,
   - performs immediate dependency status reconciliation through an activity-backed durable read,
   - fails immediately if any prerequisite is already terminal and non-successful,
   - marks already-completed prerequisites as satisfied,
   - waits on local dependency state for unresolved prerequisites.
7. When a prerequisite reaches terminal state, MoonMind looks up its downstream dependents and sends each one a `DependencyResolved` signal.
8. Each dependent workflow updates its local dependency state idempotently when the signal arrives.
9. The dependent workflow exits the dependency gate only when all prerequisites are satisfied.
10. The dependent workflow then proceeds into its normal post-initialization path.
11. Dependency outcomes are included in execution serialization and `reports/run_summary.json`.

---

## 6. Implementation Workstreams

## 6.1 API Contract And Create-Time Validation

### Deliverables

- Extend task-shaped create normalization in `/api/executions` to read `payload.task.dependsOn`.
- Normalize `dependsOn` by:
  - treating it as optional,
  - requiring an array of strings when present,
  - trimming whitespace,
  - removing blank values,
  - deduplicating while preserving order,
  - enforcing the 10-item maximum after normalization.
- Validate each dependency ID by:
  - requiring it to resolve to an existing execution,
  - requiring the caller to be authorized to reference it,
  - requiring the target workflow type to be `MoonMind.Run`,
  - rejecting `runId` values or other non-`workflowId` identifiers,
  - rejecting malformed self-reference if encountered.
- Persist the normalized dependency IDs into `initialParameters.task.dependsOn`.
- Persist durable dependency edges transactionally from the same normalized input.
- Define clear API error shapes for:
  - invalid payload shape,
  - missing target,
  - unauthorized target,
  - unsupported workflow type,
  - invalid identifier kind,
  - self-reference,
  - direct dependency count overflow.

### Acceptance criteria

- A valid create request produces a normalized dependency list with preserved order.
- The API does not accept non-string IDs, blank IDs, or duplicate IDs.
- A missing or unauthorized dependency target returns a clear validation error.
- A `runId` cannot be submitted as a dependency target.
- The execution-local dependency list and durable dependency edges are written from the same normalized payload in one transactional flow.
- Existing callers that do not provide `dependsOn` continue to work unchanged.

---

## 6.2 Durable Dependency Edge Model And Lookup Services

### Deliverables

Implement a durable dependency edge model separate from workflow-local state.

Recommended shape:

- `execution_dependencies`
  - `dependent_workflow_id`
  - `prerequisite_workflow_id`
  - `ordinal`
  - `created_at`

Recommended constraints and indexes:

- primary key or unique constraint on `(dependent_workflow_id, prerequisite_workflow_id)`,
- index on `dependent_workflow_id`,
- index on `prerequisite_workflow_id`.

Implement backend services for:

- writing dependency edges at create time,
- listing prerequisites for a dependent run,
- listing downstream dependents for a prerequisite run,
- fetching a dependency status snapshot for a set of prerequisite workflow IDs,
- enriching dependency IDs with compact titles or summaries for UI rendering.

### Source-of-truth rule

- `initialParameters.task.dependsOn` is the execution-local source of truth for what a dependent run declared.
- The durable dependency edge relation is the source of truth for reverse lookup and downstream fan-out.
- Both must be written from the same normalized input so they remain consistent.

### Acceptance criteria

- A dependent run can retrieve its declared prerequisites without reading arbitrary workflow history.
- A prerequisite run can retrieve its downstream dependents efficiently.
- Reverse lookup remains performant for list/detail UI rendering.
- Durable edge storage survives worker restarts and does not depend on in-memory state.

---

## 6.3 Dependency Signal Contract And Fan-Out

### Deliverables

Introduce a dedicated workflow signal for dependency resolution.

Recommended signal name:

- `DependencyResolved`

Recommended payload shape:

```json
{
  "prerequisiteWorkflowId": "mm:01ABC...",
  "terminalState": "completed",
  "closeStatus": "COMPLETED",
  "resolvedAt": "2026-04-03T17:24:16Z",
  "failureCategory": null,
  "message": null
}
````

The signal contract must support non-success terminal outcomes as well. Example:

```json
{
  "prerequisiteWorkflowId": "mm:01ABC...",
  "terminalState": "failed",
  "closeStatus": "FAILED",
  "resolvedAt": "2026-04-03T17:24:16Z",
  "failureCategory": "dependency_failed",
  "message": "Prerequisite run failed before dependent gate cleared"
}
```

Implement signal fan-out as follows:

1. When a prerequisite run reaches terminal state, MoonMind performs a durable reverse lookup for downstream dependents.
2. MoonMind sends a `DependencyResolved` signal to each dependent workflow.
3. Signal sending must be best-effort, bounded, and idempotent.
4. If a dependent workflow is already closed or missing, fan-out should record the condition and continue.

### Required behavior

* The dependent workflow must accept duplicate or stale signals safely.
* Unexpected signals for undeclared prerequisite IDs must be ignored safely.
* Conflicting repeated signals for an already-resolved prerequisite must not corrupt local state.
* Signal delivery failure must not create an infinite wait. Initial reconciliation and bounded repair must still converge.

### Acceptance criteria

* Dependency resolution does not rely on awaiting external workflow results.
* A dependent run can be unblocked by signals sent after it starts waiting.
* Duplicate signal delivery does not change the final outcome.
* Closed or missing dependents do not break prerequisite terminalization flow.

---

## 6.4 `MoonMind.Run` Dependency Gate

### Deliverables

Add a dependency gate to `MoonMind.Run` after initialization and before the workflow’s normal post-initialization path.

The workflow must:

1. read `initialParameters.task.dependsOn`,
2. skip the dependency gate entirely when the list is absent or empty,
3. set `mm_state` to `waiting_on_dependencies` when dependencies are present,
4. initialize local dependency-tracking state,
5. perform immediate dependency reconciliation through an activity-backed durable read,
6. fail immediately on any already-terminal non-success prerequisite,
7. mark already-completed prerequisites as satisfied,
8. wait durably for unresolved prerequisites using workflow-local state,
9. proceed only after all prerequisites are satisfied.

### Required workflow-local state

Recommended workflow-local dependency state:

* `declared_dependency_ids: list[str]`
* `dependency_outcomes_by_id: dict[str, DependencyOutcome]`
* `unresolved_dependency_ids: set[str]`
* `dependency_wait_started_at: datetime | None`
* `dependency_wait_duration_ms: int`
* `dependency_resolution: "not_applicable" | "satisfied" | "dependency_failed"`
* `failed_dependency_id: str | None`

### Signal handler behavior

Add a workflow signal handler for `DependencyResolved` that:

* validates the prerequisite ID belongs to the declared dependency set,
* ignores undeclared dependency IDs,
* records the first terminal outcome for that prerequisite,
* removes completed prerequisites from `unresolved_dependency_ids`,
* records non-success outcomes as dependency failure state,
* behaves idempotently for duplicates.

### Waiting semantics

The dependency gate must wait on **local state only**.

Recommended wait pattern:

* use `workflow.wait_condition(...)` to wait until:

  * a dependency failure has been recorded, or
  * all prerequisites are resolved and the workflow is not paused.

If task-based waiting is required internally, use `workflow.wait(...)` rather than `asyncio.wait(...)`.

### Pause and cancel semantics

Dependency gate behavior must match the desired-state contract:

* canceling the dependent run cancels only that run,
* prerequisite runs are never canceled, terminated, or paused by dependency waiting,
* pausing the dependent run does not suppress receipt of dependency-resolution signals,
* while paused, dependency outcomes may continue to be recorded,
* while paused, the workflow must not leave the dependency gate and enter planning or execution,
* if a prerequisite reaches a non-success terminal outcome while the dependent run is paused, the dependent run fails immediately.

### Failure behavior

Introduce a structured dependency failure type rather than a generic exception message.

At minimum, the failure record must include:

* `failedDependencyId`,
* prerequisite terminal MoonMind state,
* prerequisite close status when available,
* dependency failure category,
* human-readable message.

### Continue-As-New behavior

When dependency-aware workflows Continue-As-New, preserve:

* declared dependency IDs,
* already-resolved dependency outcomes,
* unresolved dependency IDs,
* wait start time and accumulated wait duration,
* dependency resolution value,
* failed dependency ID when present.

### Acceptance criteria

* A dependency-free run behaves exactly as before.
* A dependent run enters `waiting_on_dependencies` before planning/execution.
* A dependent run unblocks after all prerequisites complete successfully.
* A dependent run fails immediately on any non-success prerequisite outcome.
* Cancel while blocked cancels only the dependent run.
* Pause while blocked preserves the gate and does not lose signal-driven updates.
* Replay and worker restart do not break dependency state.

---

## 6.5 Search Attributes, Memo, And State-Model Alignment

### Deliverables

Centralize and align dependency-related state and visibility behavior across workflow code, service code, and UI code.

Required actions:

* centralize the canonical set of allowed `mm_state` values,
* ensure `waiting_on_dependencies` is treated as a valid non-terminal domain state everywhere,
* reconcile any drift between workflow constants, service projections, list filters, and UI state mapping,
* ensure adjacent documented states such as `awaiting_slot` and `proposals` are also aligned where currently referenced by the lifecycle contract.

### Typed Search Attribute plan

Use typed Search Attribute APIs for dependency-related visibility fields.

Recommended bounded fields:

* `mm_state` (existing canonical state),
* `mm_has_dependencies` (bool),
* `mm_dependency_state` (keyword; example values: `none`, `blocked`, `satisfied`, `dependency_failed`),
* `mm_dependency_count` (int).

Do not place full dependency ID lists in single-value keyword fields or other incorrectly typed indexed fields.

Full dependency IDs, rich per-dependency status, and reverse-link detail belong in:

* memo,
* read models,
* execution serialization,
* summary artifacts.

### Acceptance criteria

* List filtering recognizes `waiting_on_dependencies`.
* No dependency-related workflow code uses deprecated dict-form `upsert_search_attributes`.
* Indexed dependency fields remain bounded and correctly typed.
* Full dependency ID lists are available to UI/detail consumers without relying on unbounded Search Attributes.

---

## 6.6 Execution Serialization, Summary Artifacts, And Read Models

### Deliverables

Extend execution serialization and terminal summary output to expose dependency metadata consistently.

Required detail-view data:

* declared prerequisite IDs,
* current per-prerequisite status when known,
* whether the run is currently blocked on dependencies,
* downstream dependents for a prerequisite run,
* compact linked titles or summaries when available,
* failed dependency ID when applicable.

Required `reports/run_summary.json` shape:

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

Required resolution values:

* `not_applicable`
* `satisfied`
* `dependency_failed`

Recommended per-dependency outcome fields:

* `workflowId`
* `terminalState`
* `closeStatus`
* `resolvedAt`
* `failureCategory`
* `message`

### Acceptance criteria

* Every `MoonMind.Run` summary includes a stable `dependencies` block, even when no dependencies were declared.
* Detail surfaces can render dependency state without scraping workflow history.
* Reverse lookup data is available for prerequisite detail views.
* Wait duration and failure metadata are populated from actual dependency gate behavior.

---

## 6.7 Mission Control UX

### Deliverables

Implement end-user dependency UX in Mission Control.

### Create flow

Add a **Dependencies** section to `/tasks/new` that provides:

* a picker for existing `MoonMind.Run` executions,
* duplicate prevention,
* client-side 10-item limit enforcement,
* clear validation messages for invalid or unauthorized targets,
* clear messaging that the new run will remain blocked until prerequisites complete successfully.

### List and detail flow

* Map `waiting_on_dependencies` to the dashboard’s waiting presentation.
* Show a **Dependencies** panel on task detail with:

  * prerequisite links,
  * titles,
  * current statuses,
  * terminal outcomes when known.
* Show a **Dependents** panel or equivalent reverse-lookup surface on prerequisite detail.
* Show a compact blocked-by summary in list or quick-view surfaces when useful.

### Acceptance criteria

* A user can declare dependencies from `/tasks/new` without manually entering raw JSON.
* A dependent run blocked on prerequisites is visibly distinct from other waiting states.
* A user can navigate from a dependent run to its prerequisites and from a prerequisite run to its dependents.

---

## 6.8 Testing Strategy

### Unit coverage

Add unit tests for:

* request normalization,
* blank trimming,
* deduplication while preserving order,
* max-count enforcement,
* invalid type rejection,
* `runId` rejection,
* unauthorized target rejection,
* unsupported workflow-type rejection,
* malformed self-reference rejection,
* stable summary shape when no dependencies are present,
* structured dependency failure serialization.

### Workflow integration coverage

Add integration tests for:

* single prerequisite success,
* multiple-prerequisite fan-in,
* chained dependencies,
* prerequisite already completed before dependent starts,
* prerequisite already failed before dependent starts,
* signal-driven unblocking after startup reconciliation,
* duplicate `DependencyResolved` signals,
* stale or unexpected dependency signals,
* prerequisite failure propagation,
* prerequisite cancellation propagation,
* prerequisite termination propagation,
* dependent cancel while blocked,
* dependent pause while blocked,
* dependent pause followed by successful prerequisite completion,
* dependent pause followed by prerequisite failure,
* worker restart or replay safety,
* Continue-As-New preservation of dependency context.

### UI and serialization coverage

Add tests for:

* `/tasks/new` dependency picker submission,
* detail page dependency panel rendering,
* prerequisite dependents rendering,
* waiting state mapping in list views,
* summary and API serialization of dependency outcomes.

### Acceptance criteria

* Dependency behavior is covered at API, workflow, serialization, and UI layers.
* Replay-sensitive code paths are explicitly exercised.
* Long-wait and restart scenarios do not regress dependency correctness.

---

## 6.9 Rollout And Operational Readiness

### Deliverables

Roll out the dependency gate as a replay-safe workflow change.

Recommended approach:

* introduce the new signal-driven dependency gate under a new workflow version branch,
* keep pre-change histories replay-safe,
* use patching as the minimum requirement for rollout,
* prepare the workflow type and deployment posture for later Worker Versioning adoption.

### Required observability

Add structured logs and metrics for at least:

* dependency gate entered,
* dependency gate satisfied,
* dependency gate failed,
* dependency wait duration,
* dependency signals sent,
* dependency signal delivery failures,
* dependency reconciliation mismatches,
* reverse-lookup fan-out counts.

### Operator guidance

Document:

* the 10-dependency limit,
* direct-dependency-only semantics,
* failure propagation rules,
* pause and cancel behavior while blocked,
* reverse-lookup expectations,
* known non-goals of v1,
* recommended remediation when a prerequisite fails.

### Acceptance criteria

* The feature can be deployed without breaking replay for in-flight workflows.
* Operators can distinguish blocked, satisfied, and dependency-failed runs from logs and metrics.
* The on-call path for diagnosing dependency waits and failures is documented.

---

## 7. Non-Goals

The following are explicitly out of scope for this plan:

* editing dependencies after create,
* cross-workflow-type dependencies,
* template-authored dependency graphs,
* automatic transitive dependency expansion,
* auto-canceling prerequisite runs,
* dependency-based priority or queue-order guarantees,
* replacing child workflows with task dependencies,
* storing large dependency history or event logs in Search Attributes.

---

## 8. Definition Of Done

Task Dependencies are done when all of the following are true:

* create requests can declare `payload.task.dependsOn` and receive correct validation,
* normalized dependency IDs are persisted both in workflow input and durable dependency edges,
* `MoonMind.Run` enters `waiting_on_dependencies` and uses a signal-driven dependency gate,
* dependency waiting is deterministic, replay-safe, and safe across worker restarts,
* non-success prerequisite outcomes fail dependents with structured dependency failure metadata,
* dependency metadata is exposed consistently in execution serialization, summary artifacts, and UI surfaces,
* Mission Control supports declaring and inspecting dependencies,
* dependency-related state constants are aligned across workflow, service, and UI layers,
* typed Search Attribute usage is correct and bounded,
* replay, Continue-As-New, fan-in, chain, pause, cancel, and failure scenarios are covered by tests,
* deployment and operator guidance are in place for production rollout.

---

## 9. Deliverable Priority Order

Recommended implementation order:

1. API normalization and durable edge persistence,
2. dependency edge lookup services,
3. `DependencyResolved` signal contract and fan-out,
4. `MoonMind.Run` signal-driven dependency gate,
5. dependency summaries and execution serialization,
6. state-model and Search Attribute cleanup,
7. Mission Control create/detail UX,
8. integration, replay, Continue-As-New, and UI hardening,
9. production rollout, observability, and operator guidance.
