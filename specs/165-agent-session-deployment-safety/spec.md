# Feature Specification: Agent Session Deployment Safety

**Feature Branch**: `165-agent-session-deployment-safety`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 6 using test-driven development for the Codex managed session plane rollout, excluding the delayed standalone-image path. Runtime mode is required. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Control Sessions Without Leaks (Priority: P1)

An operator needs Codex managed session controls to use one stable control vocabulary and to enforce cleanup, cancellation, interruption, steering, clearing, and termination semantics reliably, so managed session containers and supervision records do not drift or leak.

**Why this priority**: A managed session is not robust until the public mutation surface is deterministic and termination cannot leave a runtime container or supervision record behind.

**Independent Test**: Exercise each managed session control through the public workflow boundary, then verify state, artifacts, recovery records, and runtime cleanup outcomes without inspecting container-local cache as durable truth.

**Acceptance Scenarios**:

1. **Given** a task-scoped Codex managed session is active, **When** a follow-up turn, steering request, interruption, clear, cancel, or terminate request is submitted, **Then** the request uses the canonical session-control vocabulary and updates one coherent session state.
2. **Given** a terminate request is accepted, **When** termination completes, **Then** the runtime container is removed or finalized, supervision is terminal, artifacts and bounded metadata reflect termination, and no orphaned managed session remains.
3. **Given** a cancel request is accepted, **When** cancellation completes, **Then** active work is stopped without destroying the session container and the session remains recoverable or idle.
4. **Given** a steering request is accepted for an active turn, **When** the runtime supports steering, **Then** the request changes the active turn through the managed runtime boundary instead of returning an unsupported stub.

---

### User Story 2 - Keep Long-Lived Sessions Safe (Priority: P2)

A maintainer needs long-lived, message-heavy session workflows to process concurrent controls safely and continue as new before history grows without bound, while preserving enough bounded state to keep the same logical session usable.

**Why this priority**: Session workflows can outlive a single turn and accept many messages; race hazards or unbounded history growth can break unattended runs and in-flight operator controls.

**Independent Test**: Drive concurrent and early-arriving controls, force a shortened history rollover, and verify serialized state changes, readiness handling, handler drain, and carry-forward state.

**Acceptance Scenarios**:

1. **Given** multiple async controls arrive close together, **When** they mutate locator, thread, active turn, status, or continuity refs, **Then** each mutation observes a consistent state and the final query state reflects one complete ordered outcome.
2. **Given** a runtime-bound control arrives before runtime handles are attached, **When** the handles become available, **Then** the accepted control proceeds deterministically using the attached locator.
3. **Given** a session reaches a history rollover threshold, **When** the workflow continues as new, **Then** binding identity, current epoch, runtime locator, latest control metadata, continuity refs, and request-tracking state survive the handoff.
4. **Given** a workflow is completing or handing off, **When** accepted async handlers are still running, **Then** completion or handoff waits until handlers finish so clients can retrieve update results.

---

### User Story 3 - Recover and Observe Sessions Safely (Priority: P3)

An operator needs session identity, phase, degradation, latest continuity refs, and recovery status to be visible through bounded operational surfaces without exposing prompts, transcripts, scrollback, raw logs, credentials, or secrets.

**Why this priority**: Operators need enough information to diagnose and recover sessions, but indexed visibility and workflow metadata must remain compact and safe.

**Independent Test**: Transition a managed session through launch, active turn, interruption, clear, degradation, cancellation, and termination, then verify operational metadata, history summaries, telemetry correlation, and recurring reconciliation outcomes.

**Acceptance Scenarios**:

1. **Given** a managed session changes phase, **When** an operator inspects workflow-visible status, **Then** the surface identifies task run, runtime, session, epoch, status, degradation state, and compact continuity refs only.
2. **Given** control operations appear in durable history, **When** an operator reviews the timeline, **Then** each operation has a readable bounded summary that distinguishes cancel from terminate.
3. **Given** a session record is stale, degraded, missing a runtime container, or associated with an orphaned runtime, **When** recurring reconciliation runs, **Then** it reattaches, marks degraded, finalizes, or reports the bounded recovery outcome.
4. **Given** telemetry is emitted for managed session work, **When** logs, metrics, or traces are correlated, **Then** they use bounded task, runtime, session, epoch, status, and degradation identifiers and exclude sensitive or unbounded content.

---

### User Story 4 - Gate Workflow Changes Before Rollout (Priority: P4)

A maintainer deploying managed session workflow changes needs versioning, patching, replay validation, and cutover guidance to prevent in-flight workflow histories from breaking when handler shapes, payloads, lifecycle semantics, or visibility fields evolve.

**Why this priority**: The session workflow is durable code; deployment safety matters whenever workflow shape or persisted state changes.

**Independent Test**: Run representative replay and deployment-safety validation for a workflow-shape change, then verify rollout is blocked unless versioning or patching and replay coverage are present.

**Acceptance Scenarios**:

1. **Given** an incompatible managed session workflow evolution is prepared, **When** deployment checks run, **Then** worker versioning or an explicit versioned cutover is required before rollout.
2. **Given** an intermediate replay bridge is needed, **When** the change is deployed, **Then** patching is scoped to replay-sensitive behavior and has an explicit removal condition.
3. **Given** representative open or closed managed session histories exist, **When** replay validation runs, **Then** replay success is treated as a deployment gate, not optional evidence.
4. **Given** steering, Continue-As-New, cancel/terminate semantics, or new visibility metadata is enabled, **When** operators follow the cutover playbook, **Then** the rollout path states prerequisites, validation gates, and rollback or removal conditions.

### Edge Cases

- The delayed standalone-image path is explicitly out of scope.
- A mutating request arrives before runtime handles are attached.
- A turn-specific request is submitted with no active turn.
- A request uses a stale session epoch.
- A clear request arrives while clearing is already in progress.
- Duplicate launch, clear, interrupt, cancel, steer, or terminate requests are retried by a client or activity retry.
- A control activity partially changes runtime state before failing.
- A termination request races with parent workflow shutdown or another active mutator.
- Runtime cleanup succeeds but artifact publication or supervision finalization is delayed.
- Reconciliation observes a record without a container, a container without a healthy record, or a stale degraded session.
- Continue-As-New is suggested while a mutating handler is active.
- Existing workflow histories must replay after a workflow-shape change.
- Replay fixtures or metadata inputs contain prompt-like, transcript-like, raw-log-like, credential-like, or secret-like strings.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve runtime intent for this feature; deliverables MUST include production runtime code changes and validation tests, not documentation or specification updates alone.
- **FR-002**: The system MUST keep the delayed standalone-image path out of scope for this feature.
- **FR-003**: The system MUST define and expose one canonical managed-session control vocabulary covering start, resume, send, steer, interrupt, clear, cancel, and terminate actions.
- **FR-004**: The system MUST reject invalid session mutations deterministically before they change session state, including stale epochs, missing runtime handles where required, missing active turns for turn-specific controls, clear while already clearing, and mutators after termination begins.
- **FR-005**: The system MUST keep fire-and-forget state propagation separate from mutating controls so production mutation outcomes are request/response visible.
- **FR-006**: The system MUST implement interruption and steering as real runtime-bound behaviors when the active runtime supports them.
- **FR-007**: The system MUST make termination a cleanup-complete lifecycle action that finalizes runtime container state, supervision state, workflow state, and bounded operator metadata before the session is considered complete.
- **FR-008**: The system MUST keep cancellation distinct from termination by stopping active work while preserving the intended recoverable or idle session state.
- **FR-009**: The system MUST make launch, clear, interrupt, cancel, steer, and terminate retry-safe or deduplicated at the activity/controller boundary.
- **FR-010**: The system MUST classify permanent session-control failures explicitly so they are not retried as transient failures.
- **FR-011**: The system MUST ensure meaningfully blocking session activities can receive cancellation in time to avoid stuck cleanup or control operations.
- **FR-012**: The system MUST serialize async mutators that touch shared session state, including locator, thread, active turn, status, control metadata, artifact refs, and degradation state.
- **FR-013**: The system MUST gate runtime-bound accepted controls on runtime-handle readiness rather than failing nondeterministically during launch.
- **FR-014**: The system MUST wait for accepted async handlers to finish before workflow completion or workflow handoff.
- **FR-015**: The system MUST continue as new from the main workflow execution path when history rollover policy requires it.
- **FR-016**: The system MUST carry forward bounded session identity, current epoch, runtime locator, last control action, last control reason, continuity refs, and request-tracking or dedupe state across handoff.
- **FR-017**: The system MUST expose only bounded operator-visible session metadata for workflow visibility, indexed lookup, activity summaries, schedules, logs, metrics, and traces.
- **FR-018**: The system MUST NOT place prompts, transcripts, terminal scrollback, raw logs, credentials, secrets, or unbounded provider output in indexed visibility, workflow metadata, schedule metadata, activity summaries, telemetry dimensions, or replay fixtures.
- **FR-019**: The system MUST publish production session continuity artifacts through the managed session controller and supervisor path; container-local publication helpers may be fallback-only and must not be the production source of operator/audit truth.
- **FR-020**: The system MUST treat artifacts plus bounded workflow metadata as the operator/audit truth, the managed session record as the operational recovery index, and container-local state as disposable cache.
- **FR-021**: The system MUST provide recurring reconciliation that checks stale degraded sessions, missing runtime containers, orphaned runtime state, and supervision drift, returning bounded recovery outcomes.
- **FR-022**: The system MUST keep heavy runtime/container side effects separated from workflow-processing work through the runtime boundary.
- **FR-023**: The system MUST include lifecycle validation for session creation, runtime handle attachment, follow-up turns, clear/reset invariants, interruption, cancellation, steering, termination cleanup, restart/reconcile, races, idempotency, and Continue-As-New carry-forward.
- **FR-024**: The system MUST include replay validation for representative managed session histories whenever workflow definition shape, handler shape, payload shape, or persisted carry-forward state changes.
- **FR-025**: The system MUST require worker versioning, workflow patching, or an explicit versioned cutover for incompatible managed session workflow evolution.
- **FR-026**: The system MUST document and validate cutover playbooks for enabling steering, enabling Continue-As-New, changing cancel/terminate semantics, and introducing new visibility fields.
- **FR-027**: The system MUST treat replay results and fault-injected lifecycle tests as rollout gates for broad deployment.
- **FR-028**: The implementation process MUST be test-driven for this feature: add or update validation coverage before relying on production runtime changes as complete.

### Key Entities *(include if feature involves data)*

- **Managed Session**: One task-scoped Codex runtime continuity scope with session identity, epoch, runtime locator, active turn, status, and latest continuity references.
- **Control Request**: A mutation request that starts, resumes, sends, steers, interrupts, clears, cancels, or terminates a managed session.
- **Runtime Locator**: The bounded handle set needed to address the active managed runtime, including session, epoch, container, and thread identity.
- **Operational Recovery Record**: The supervision index used for recovery and reconciliation, including the currently known runtime state and latest bounded artifact references.
- **Operator/Audit Surface**: Artifact references plus bounded workflow metadata used for presentation, audit, and continuity review.
- **Disposable Runtime Cache**: Container-local runtime state used for performance and continuity but not as durable operator truth.
- **Continue-As-New Carry-Forward State**: The bounded session state preserved when a long-lived session workflow hands off to a new run.
- **Replay Gate**: Required validation evidence showing representative histories remain deterministic or are protected by an explicit versioned cutover.
- **Cutover Playbook**: Operator and maintainer guidance that defines prerequisites, rollout gates, rollback expectations, and patch-removal conditions for workflow-shape changes.

### Assumptions

- Codex managed sessions remain task-scoped and Docker-backed for this feature.
- The feature may verify and preserve already-completed rollout work instead of reimplementing it.
- Provider verification with live external credentials is outside the required validation path unless separately enabled by an operator.
- Bounded artifact references are safe to expose in workflow metadata; artifact contents remain outside indexed visibility and summaries.
- Runtime-mode implementation is the default; docs-only work is insufficient.
- Test-driven development is required for this feature; existing compliant behavior may be preserved only when covered by validation evidence.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Public managed session controls match the canonical control vocabulary, and invalid controls are rejected deterministically before mutating state.
- **SC-002**: Termination validation proves runtime cleanup, supervision finalization, terminal session state, and absence of orphaned managed runtime state.
- **SC-003**: Cancellation validation proves cancellation remains distinct from termination and preserves the defined recoverable or idle session behavior.
- **SC-004**: Steering and interruption validation proves active-turn controls are wired through the runtime boundary and reflected in query state and artifacts.
- **SC-005**: Race and idempotency validation proves duplicate controls, stale epochs, early updates, and shutdown races do not corrupt session state.
- **SC-006**: Continue-As-New validation proves locator, epoch, control metadata, continuity refs, and request-tracking or dedupe state survive handoff.
- **SC-007**: Operational metadata and telemetry safety review finds zero prompts, transcripts, scrollback, raw logs, credentials, secrets, or unbounded provider output in bounded surfaces.
- **SC-008**: Recurring reconciliation validation proves missing containers, orphaned runtime state, stale degraded sessions, and supervision drift are detected with bounded outcomes.
- **SC-009**: Replay validation covers representative managed session histories and blocks rollout when replay is missing or failing for workflow-shape changes.
- **SC-010**: Worker versioning, patching, or an explicit versioned cutover is present before incompatible managed session workflow changes can be deployed.
- **SC-011**: The repository's required runtime validation suite for affected workflow, runtime, integration, and replay boundaries passes without requiring external provider credentials.
