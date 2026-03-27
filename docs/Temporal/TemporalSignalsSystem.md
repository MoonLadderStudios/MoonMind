# Temporal Signals System

**Implementation tracking:** [`docs/tmp/020-TemporalSignalsPlan.md`](../tmp/020-TemporalSignalsPlan.md)

Status: **Design Draft**
Owners: MoonMind Engineering
Last Updated: 2026-03-27

> [!NOTE]
> This document defines the desired-state Temporal Signals System for MoonMind.
> It describes which interactions should use Temporal Signals, how those signals are shaped, and how workflows must react to them.
> Migration sequencing, rollout work, and implementation tasks belong in [`docs/tmp/020-TemporalSignalsPlan.md`](../tmp/020-TemporalSignalsPlan.md).

---

## 1. Summary

MoonMind uses Temporal Signals as its asynchronous workflow event channel.

The system has four main signal roles:

- external events entering a running workflow without requiring an immediate response,
- workflow-to-workflow coordination between long-lived orchestration components,
- scheduling and wait-state control for already-running workflows,
- lightweight runtime and child-workflow notifications that unblock a parent workflow.

The core design rule is:

> Use **Temporal Updates** for request/response mutations that need synchronous acknowledgement, and use **Temporal Signals** for asynchronous events that should be recorded durably and processed inside workflow execution.

In the desired state, Signals are a first-class Temporal contract across `MoonMind.Run`, `MoonMind.AgentRun`, `MoonMind.AuthProfileManager`, and `MoonMind.OAuthSession`, with clear payload rules, idempotency rules, observability rules, and workflow-state effects.

---

## 2. Goals

The Temporal Signals System must support all of the following:

1. **Clear async control boundaries**
   - Signals represent asynchronous events, not general-purpose edit commands.

2. **Stable workflow contracts**
   - Each signal name, payload shape, and semantic effect is explicitly defined per workflow.

3. **Small durable payloads**
   - Signal inputs remain compact and durable-history-safe, with artifact references used for larger bodies.

4. **Workflow-to-workflow coordination**
   - Child workflows, singleton manager workflows, and session workflows can coordinate without database polling loops.

5. **Deterministic wait-state handling**
   - Workflows convert signal arrival into deterministic state changes and `workflow.wait_condition(...)` wake-ups.

6. **Signal-safe retries and duplicates**
   - Retries, duplicate callbacks, and repeated operator actions do not corrupt workflow state or trigger duplicate side effects.

7. **Operator-visible behavior**
   - Signal-driven state changes are reflected in search attributes, memo summaries, and durable execution records.

8. **Scheduling-aware semantics**
   - Deferred execution control uses the correct Temporal primitive for the required mutability.

---

## 3. Non-Goals

This design does **not** attempt to:

- use Signals as a replacement for all Updates,
- place large callback bodies, logs, or secrets directly in workflow history,
- expose every internal workflow-to-workflow signal as a public HTTP API,
- make Temporal task queues part of the signal contract,
- support compatibility aliases forever for internal-only signal names, or
- treat `start_delay` as a mutable scheduling mechanism.

---

## 4. Core Principles

### 4.1 Signals Are Async, Updates Are Acknowledged Mutations

MoonMind uses:

- **Updates** when the caller needs validation and an immediate accepted/rejected response, and
- **Signals** when the caller is reporting an event or requesting async handling without waiting for a response payload.

Examples:

- `Pause`, `Resume`, `Approve`, `Cancel`, `UpdateInputs`, and `RequestRerun` belong to the update-style control surface.
- `ExternalEvent`, `reschedule`, `request_slot`, `slot_assigned`, `finalize`, and `completion_signal` belong to the signal surface.

### 4.2 Signals Must Be Compact

Signal payloads must stay small enough to remain safe in workflow history.

Large or volatile content must be stored as an artifact and referenced by ID rather than embedded inline. This applies especially to:

- webhook bodies,
- provider status payloads,
- diagnostics,
- rich external result metadata.

### 4.3 Signal Handlers Mutate Durable Workflow State

Signal handlers should do the minimum deterministic work needed to:

- validate the payload shape,
- record compact state,
- set flags or bounded state objects,
- wake the main workflow loop through `workflow.wait_condition(...)`.

Signal handlers must not become general side-effect engines.

### 4.4 Signals Must Be Retry-Safe

Signal delivery may be retried by callers or repeated by external systems.

The desired-state system therefore requires explicit deduplication or no-op replay semantics for signal families that can be sent more than once, especially:

- provider callbacks,
- runtime slot requests,
- slot releases,
- cooldown reports,
- scheduling adjustments,
- child completion notifications.

### 4.5 Public and Internal Signal Names Are Separate Contract Surfaces

MoonMind has two different naming domains:

- **public execution control names** used at the HTTP/API boundary for Temporal-managed executions, and
- **internal workflow coordination names** used between workflows.

Desired-state naming rules:

- Public execution control names follow the canonical Temporal execution API surface already defined by MoonMind docs and schemas.
- Internal workflow coordination names remain explicit workflow-local contracts and are not treated as part of the general execution API.
- A signal name should exist in exactly one contract family unless reuse is intentional and documented.

### 4.6 Scheduling Control Must Match Temporal Semantics

If an execution must be controllable before work begins, MoonMind must not rely on immutable `start_delay` semantics alone.

Desired-state rules:

- use `start_delay` only for simple one-time deferred execution that does not need mutable pre-start control,
- use an in-workflow scheduled wait plus the `reschedule` signal when the target time can change after creation,
- do not depend on early control signals being delivered to a workflow that has not yet started its first task.

---

## 5. Signal Roles and Boundaries

### 5.1 API-to-Workflow Async Events

These are Signals initiated from the MoonMind API layer into a running workflow.

Primary desired-state use:

- `ExternalEvent` into `MoonMind.Run`.

Rules:

- API performs authentication and coarse validation first.
- Workflow validates correlation and workflow-local invariants.
- Large callback content is stored as an artifact before signaling when needed.

### 5.2 Workflow-to-Workflow Coordination

These are Signals sent from one workflow to another through `workflow.get_external_workflow_handle(...)`.

Primary desired-state use:

- `MoonMind.AgentRun` <-> `MoonMind.AuthProfileManager`,
- `MoonMind.AgentRun` -> parent `MoonMind.Run`,
- `MoonMind.AuthProfileManager` -> `MoonMind.AgentRun`.

Rules:

- coordination signals are explicit per-workflow contracts,
- sending workflow must treat the receiver as asynchronous and retry-safe,
- receiver must be robust to duplicates and stale senders.

### 5.3 Workflow-Local Wait Control

Signals may modify workflow-local wait conditions without representing a public edit API.

Primary desired-state use:

- `reschedule` on `MoonMind.Run`,
- `finalize` and `cancel` on `MoonMind.OAuthSession`.

### 5.4 External Provider Callback Ingress

External systems do not signal Temporal directly.

Desired-state callback path:

1. external provider calls a MoonMind API endpoint,
2. API authenticates and correlates the callback,
3. API stores raw payload as an artifact when needed,
4. API signals the target workflow with a compact `ExternalEvent` payload.

---

## 6. Canonical Signal Contracts by Workflow

## 6.1 `MoonMind.Run`

`MoonMind.Run` is the primary orchestration workflow and exposes a narrow signal surface.

### Signals

#### `ExternalEvent`

Purpose:

- carry asynchronous external integration progress or completion into the workflow.

Canonical payload concepts:

- `source`
- `event_type`
- `external_operation_id`
- `provider_event_id` when available
- `normalized_status` when known
- `provider_status` when known
- `observed_at`
- `payload_artifact_ref` when the full raw event body is stored separately

Required behavior:

- reject mismatched or unverifiable correlation,
- ignore duplicate provider events,
- ignore late non-terminal events after a terminal integration state,
- transition workflow state between `awaiting_external` and `executing` as appropriate,
- update summary and search attributes to reflect the new external state.

#### `reschedule`

Purpose:

- update the target start time of an already-created execution that is still in an in-workflow scheduled wait.

Canonical payload concepts:

- `scheduled_for`

Required behavior:

- only valid while the workflow is in the scheduled wait path,
- update `mm_scheduled_for`,
- remain deterministic and replay-safe,
- not be used as a generic replacement for recurring schedule editing.

### Explicit non-signal control surface

The desired state keeps `Pause`, `Resume`, `Approve`, and `Cancel` on the workflow-update path rather than the generic signal path because they represent acknowledged control mutations.

---

## 6.2 `MoonMind.AgentRun`

`MoonMind.AgentRun` is the durable child workflow for managed and external agent execution.

### Signals received

#### `completion_signal`

Purpose:

- accept an asynchronous terminal completion result from a runtime or callback bridge.

Canonical payload concepts:

- compact final result object or result artifact reference

Required behavior:

- set final result state exactly once,
- tolerate duplicate terminal notifications as no-ops,
- wake the completion wait path.

#### `slot_assigned`

Purpose:

- notify a managed agent run that a provider/auth profile slot was assigned.

Canonical payload concepts:

- `profile_id`

Required behavior:

- record the assigned profile,
- wake the slot wait path,
- ignore duplicate assignment of the same profile,
- fail fast on conflicting reassignment rather than silently swapping slots.

### Signals sent

#### To `MoonMind.AuthProfileManager`

- `request_slot`
- `release_slot`
- `report_cooldown`
- `sync_profiles`

These signals coordinate profile-slot allocation and rate-limit recovery for managed runtimes.

#### To parent `MoonMind.Run`

- `child_state_changed`
- `profile_assigned`

These signals provide parent-visible execution state without requiring polling.

Desired-state rule:

- parent/child coordination signals use a single structured payload object, not multiple positional arguments.

---

## 6.3 `MoonMind.AuthProfileManager`

`MoonMind.AuthProfileManager` is the singleton coordination workflow for per-runtime profile slot allocation.

### Signals received

#### `request_slot`

Purpose:

- enqueue or immediately satisfy a slot request from one `MoonMind.AgentRun`.

Canonical payload concepts:

- `requester_workflow_id`
- `runtime_id`
- `profile_selector`
- stable dedupe key for the request

Required behavior:

- treat duplicate requests from the same requester as idempotent,
- avoid enqueuing duplicate pending requests,
- preserve FIFO semantics unless selector rules require otherwise.

#### `release_slot`

Purpose:

- release a previously granted slot.

Canonical payload concepts:

- `requester_workflow_id`
- `profile_id`

Required behavior:

- be a safe no-op if the slot was already released,
- remove both in-memory and persisted lease state,
- trigger queue draining after release.

#### `report_cooldown`

Purpose:

- mark a profile unavailable for a bounded cooldown period after provider throttling.

Canonical payload concepts:

- `profile_id`
- `cooldown_seconds`

#### `sync_profiles`

Purpose:

- refresh manager-visible profile configuration from durable source-of-truth data.

Canonical payload concepts:

- `profiles`

#### `shutdown`

Purpose:

- request graceful singleton shutdown.

### Signals sent

#### `slot_assigned`

Purpose:

- notify the waiting `MoonMind.AgentRun` that it now owns a specific profile slot.

Required behavior:

- only emit after the lease is durably recorded,
- duplicate emission for an already-owned lease is allowed as a reconnect aid,
- assignment must remain tied to the same requester workflow ID.

---

## 6.4 `MoonMind.OAuthSession`

`MoonMind.OAuthSession` uses simple workflow-local control signals.

### Signals

#### `finalize`

Purpose:

- request transition from waiting-for-user to verification and registration.

#### `cancel`

Purpose:

- stop the session before completion.

Desired-state rule:

- these signals remain minimal and workflow-local,
- optional operator context may be added only if it stays compact and durable-history-safe.

---

## 7. Payload and Modeling Rules

### 7.1 Single Structured Argument

Each signal contract should use one structured payload object.

Benefits:

- forward-compatible field addition,
- simpler validation,
- less ambiguity across Python handler signatures,
- safer replay compatibility when evolving fields with defaults.

### 7.2 Artifact References for Large Content

If a signal needs to point to large content, it should carry:

- a compact summary field when useful, and
- an artifact reference for the full payload.

### 7.3 No Secrets in Signal Payloads

Signal payloads must never contain:

- raw credentials,
- OAuth tokens,
- provider API keys,
- session cookies,
- large environment dumps.

### 7.4 Explicit Validation

Each receiving workflow must validate:

- required fields,
- field types,
- correlation identity when relevant,
- allowed state transitions for the current workflow state.

Invalid signal input should fail fast and visibly rather than being silently normalized into a different meaning.

---

## 8. Idempotency, Ordering, and Concurrency

### 8.1 Idempotency Rules

Signal families must define dedupe semantics.

Required desired-state cases:

- `ExternalEvent` dedupes on provider event identity when available,
- `request_slot` dedupes per requester and request identity,
- `release_slot` is safe to repeat,
- `slot_assigned` is safe to repeat for the same assignment,
- `completion_signal` is safe to repeat for the same terminal result,
- `reschedule` replaces the pending scheduled target with the most recent valid value.

### 8.2 Ordering Rules

The system must not assume perfect external ordering.

Required behavior:

- late non-terminal external events cannot reopen a terminal integration state,
- duplicate completion notifications cannot create multiple completions,
- stale slot-management signals cannot transfer ownership to a different requester after release.

### 8.3 Handler Concurrency

Temporal signal handlers may run concurrently with workflow code and with each other.

Desired-state rules:

- handlers remain lightweight and bounded,
- awaited handler paths that mutate shared state must guard invariants explicitly,
- singleton coordinators such as `MoonMind.AuthProfileManager` protect queue and lease state against async interleaving.

---

## 9. Search Attributes, Memo, and Execution Records

Signal effects must be observable through the standard Temporal-facing execution metadata model.

Required behavior:

- signal-driven state transitions update `mm_state` when the domain state changes,
- relevant search attributes such as `mm_integration` or `mm_scheduled_for` stay aligned,
- memo `summary` reflects the latest operator-visible state,
- durable execution records mirror the workflow-authoritative result rather than inventing projection-only signal effects.

Examples:

- `ExternalEvent` may move a run from `awaiting_external` back to `executing`,
- `reschedule` updates scheduled metadata,
- `slot_assigned` drives the child run from `awaiting_slot` to `launching`,
- `finalize` moves an OAuth session from waiting to verification.

---

## 10. Continue-As-New and In-Flight Safety

Signal-aware workflows must preserve the minimum state needed to remain correct across Continue-As-New and worker restarts.

Required preserved state includes, as applicable:

- scheduled target time,
- integration correlation metadata,
- bounded callback dedupe state,
- current profile leases and pending requests,
- assigned profile identity,
- terminal-result state when already reached.

When signal contracts change, MoonMind must protect in-flight executions by either:

- preserving compatibility for persisted payload and invocation shapes, or
- using an explicit cutover plan that isolates old and new workflow histories safely.

---


### 10.1 Explicit Compatibility Notes (Phase 0)

To protect in-flight workflow histories during the transition to this desired-state contract:

1. **`MoonMind.Run` Control Aliases**:
   - `Pause`, `Resume`, `Approve`, and `Cancel` exist as **Updates** in code today, but legacy API clients and scripts might still attempt to send them as **Signals** (or as lowercase `pause` / `resume`).
   - *Compatibility rule*: `MoonMind.Run` must temporarily support a dynamic signal handler to gracefully catch and translate legacy `Pause`/`Resume` signals into the equivalent update-state mutations without breaking history.

2. **Parent/Child Positional Arguments**:
   - `child_state_changed` currently accepts positional arguments (`new_state: str, reason: str`).
   - *Compatibility rule*: When migrating to a single structured payload (Phase 3), the workflow must use `workflow.patched(...)` to accept the old positional signature for already-running executions.

3. **Raw Dict Payloads**:
   - Workflows like `AuthProfileManager` and `AgentRun` currently accept raw `dict[str, Any]` for signals like `request_slot` or `completion_signal`.
   - *Compatibility rule*: As these move to strict Pydantic contract models (Phase 1), the handler signatures must remain backward-compatible (e.g., using `dict | PydanticModel` and `workflow.patched(...)`) until all legacy callers have flushed from the system.

---

## 11. Relationship to Other Temporal Controls

The Temporal Signals System works alongside, not instead of, the rest of MoonMind's Temporal control model.

### Signals

- asynchronous events,
- webhook and callback ingress,
- workflow-to-workflow coordination,
- mutable wait-state nudges such as `reschedule`.

### Updates

- acknowledged mutations,
- title changes,
- input changes,
- reruns,
- pause/resume/approve/cancel operator actions.

### Cancellation

- Temporal cancellation remains the primary mechanism for stop-execution semantics at the execution-service layer.

---

## 12. Security and Authorization

The desired-state system must satisfy all of the following:

1. External callback signals enter through authenticated MoonMind APIs, not direct unauthenticated Temporal access.
2. Signal payloads contain no raw secrets.
3. Workflows validate correlation and state before accepting a signal's semantic effect.
4. Internal workflow-to-workflow signals are scoped to explicit known workflow IDs, not broad fan-out conventions.
5. Large raw provider payloads use artifact storage and redaction-aware presentation paths.

---

## 13. Testing Requirements

Signal-related changes must ship with workflow-boundary verification rather than isolated helper tests alone.

Minimum expectations:

1. workflow tests covering real signal/update invocation shapes,
2. duplicate-delivery cases for signal families with dedupe requirements,
3. start-time and wait-state tests for `reschedule` and deferred execution paths,
4. callback and polling race tests for `ExternalEvent`,
5. lease-manager tests covering duplicate request/release/cooldown behavior,
6. Continue-As-New or restart-safety coverage when signal-relevant state persists across long runs.

---

## 14. Completion Criteria

The Temporal Signals System is in its desired state when all of the following are true:

- every public and internal signal contract has a documented semantic owner,
- `MoonMind.Run` uses Signals only for asynchronous events and scheduling wait control,
- acknowledged execution edits are handled through Updates,
- callback ingestion, workflow coordination, and singleton manager signaling all use compact, explicit payloads,
- dedupe and replay safety rules are enforced for signal families that can repeat,
- signal-driven state transitions remain visible through search attributes, memo, and execution projections,
- tmp implementation-tracking material can eventually be removed without losing the stable contract.
