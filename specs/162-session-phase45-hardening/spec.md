# Feature Specification: Codex Managed Session Phase 4/5 Hardening

**Feature Branch**: `162-session-phase45-hardening`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 and Phase 5 of the Codex managed session plane rollout using test-driven development, only for parts not fully implemented already. Phase 4 adds idiomatic operational observability and separation: bounded Temporal operator metadata, safe Search Attributes, readable activity summaries, metrics/tracing/log correlation, runtime activity worker separation, and a recurring scheduled reconcile/sweeper for managed sessions. Phase 5 adds integration and replay coverage for lifecycle, clear_session invariants, interrupt_turn, terminate_session cleanup, cancel_session distinct semantics, steer_turn contract, restart/reconcile, race/idempotency, Continue-As-New carry-forward, and replay tests. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve security constraints: no prompts, transcripts, scrollback, raw logs, credentials, or secrets in indexed visibility, workflow metadata, schedule metadata, activity summaries, or replay fixtures."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inspect Session Health Safely (Priority: P1)

An operator monitoring a Codex managed session needs to identify the session, current phase, epoch, degradation state, and latest continuity references from bounded operational metadata without opening raw logs or provider output.

**Why this priority**: Operators need a safe, compact status view before they can decide whether a session is healthy, degraded, interrupted, cleared, or terminating.

**Independent Test**: Start and transition a managed session, then verify the operational visibility surface contains only bounded identity, status, degradation, and artifact reference values.

**Acceptance Scenarios**:

1. **Given** a managed session starts, **When** an operator inspects workflow-visible metadata, **Then** the metadata identifies the task run, runtime, session, epoch, status, and degradation state.
2. **Given** a session changes phase, **When** it starts a turn, is interrupted, clears to a new epoch, degrades, terminates, or finishes termination, **Then** the current operator details reflect that latest phase and compact continuity references.
3. **Given** prompts, transcripts, scrollback, raw logs, credentials, or secret-like values exist elsewhere in the system, **When** operational metadata is produced, **Then** none of those values appear in indexed visibility, workflow metadata, schedule metadata, activity summaries, or replay fixtures.
4. **Given** runtime workers emit metrics, traces, or logs for managed session operations, **When** those telemetry records are correlated with a session, **Then** they use bounded task/session/runtime identifiers and exclude prompts, transcripts, raw logs, credentials, and secrets.

---

### User Story 2 - Review Control History Without Payload Inspection (Priority: P2)

An operator reviewing a session incident needs control operations to be readable from the durable history timeline without opening every low-level event payload.

**Why this priority**: Readable history reduces incident triage time and avoids unnecessary exposure to runtime payloads.

**Independent Test**: Drive launch, send, interrupt, clear, cancel, steer, and terminate operations and verify each produces a readable bounded control summary.

**Acceptance Scenarios**:

1. **Given** a managed session launch or control operation is scheduled, **When** it appears in durable history, **Then** the entry names the operation and bounded session identifiers.
2. **Given** an operation includes operator instructions, runtime output, raw errors, or secret-like values, **When** its history summary is rendered, **Then** the summary excludes those unbounded or sensitive values.
3. **Given** cancellation and termination are both requested in separate scenarios, **When** an operator reviews the history, **Then** cancellation is distinguishable from termination.

---

### User Story 3 - Recover Sessions Recurringly (Priority: P3)

An operator needs managed session reconciliation to run durably on a recurring basis so orphaned containers, missing runtime state, or stale degraded sessions are discovered without manual polling.

**Why this priority**: Recurring recovery prevents resource leaks and keeps the operational recovery record aligned with the actual managed runtime environment.

**Independent Test**: Configure the recurring recovery trigger, execute the reconcile target, and verify it reports a bounded outcome while delegating runtime checks to the runtime boundary.

**Acceptance Scenarios**:

1. **Given** a managed session record exists, **When** recurring reconciliation runs, **Then** the system checks whether the runtime is still present and records a bounded reconciliation outcome.
2. **Given** a runtime container is missing or a session is stale and degraded, **When** reconciliation runs, **Then** the operational record is reattached or marked degraded with a bounded operator-readable reason.
3. **Given** many sessions are stale at once, **When** reconciliation reports its result, **Then** the outcome remains compact and does not include raw logs, transcripts, credentials, or full record dumps.

---

### User Story 4 - Prove Lifecycle Semantics End-to-End (Priority: P4)

A maintainer changing the managed session workflow needs integration and replay coverage that proves lifecycle controls, recovery behavior, and long-lived workflow handoff remain correct.

**Why this priority**: The session workflow is durable code; changes to handler shapes, lifecycle semantics, and carry-forward state must be proven before broad rollout.

**Independent Test**: Run fault-injected lifecycle and replay tests that cover session creation, follow-up turns, clear/reset, interrupt, cancel, steer, termination cleanup, restart/reconcile, race/idempotency, and Continue-As-New handoff.

**Acceptance Scenarios**:

1. **Given** a session is cleared, **When** the clear completes, **Then** the session keeps the same session identity and container identity, increments the epoch, receives a new thread identity, clears the active turn, and records the expected control/reset artifacts.
2. **Given** an active turn exists, **When** it is interrupted, **Then** active turn state is cleared or updated, interruption is reflected in artifacts and query state, and the controller-visible event is recorded.
3. **Given** a session is terminated, **When** the termination completes, **Then** the runtime is removed, supervision is finalized, the record is terminal, and no orphan remains.
4. **Given** a session is canceled, **When** cancellation completes, **Then** it is not treated as termination and the session remains recoverable or idle according to the defined cancellation contract.
5. **Given** a workflow reaches its history rollover threshold, **When** it continues as new, **Then** session locator, epoch, continuity references, and dedupe or request-tracking state survive the handoff.
6. **Given** representative open and closed workflow histories exist, **When** replay validation runs, **Then** workflow-definition changes remain deterministic or fail the validation gate.

### Edge Cases

- Updates arrive before runtime handles are attached.
- Duplicate clear, interrupt, cancel, steer, or terminate requests are received.
- A request uses a stale session epoch.
- A parent workflow and child session workflow shut down at nearly the same time.
- Reconciliation observes a durable record but no matching runtime container.
- Reconciliation observes a runtime container without a healthy supervision record.
- A control activity fails after partially changing runtime state.
- A replay fixture or test payload contains prompt-like, transcript-like, raw-log-like, or secret-like strings.
- Continue-As-New occurs while bounded state or request-tracking data exists.
- Existing Phase 4 or Phase 5 behavior is already fully implemented and should not be reimplemented.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST implement only Phase 4 and Phase 5 managed session behavior that is not already fully implemented.
- **FR-002**: Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.
- **FR-003**: System MUST update bounded operator-visible session details for session start, active turn running, interruption, clear/reset to a new epoch, degradation, termination in progress, and terminated state.
- **FR-004**: System MUST expose only the bounded indexed visibility fields `TaskRunId`, `RuntimeId`, `SessionId`, `SessionEpoch`, `SessionStatus`, and `IsDegraded` for managed session workflow visibility.
- **FR-005**: System MUST NOT place prompts, transcripts, scrollback, raw logs, credentials, secret values, or unbounded provider output in indexed visibility fields, workflow metadata, schedule metadata, activity summaries, or replay fixtures.
- **FR-006**: System MUST provide readable bounded summaries for launch, send, interrupt, clear, cancel, steer, and terminate control operations.
- **FR-007**: System MUST keep heavy runtime/container work separated from workflow-processing work so runtime side effects occur only through the runtime boundary.
- **FR-008**: System MUST provide a durable recurring reconcile or sweeper trigger for managed sessions.
- **FR-009**: Reconciliation MUST detect stale degraded sessions, missing runtime containers, and orphaned runtime state, then return a bounded operator-readable outcome.
- **FR-010**: System MUST provide integration coverage for task-scoped session creation, runtime handle attachment, follow-up turn execution, query state, and continuity references.
- **FR-011**: System MUST validate `clear_session` invariants: same session identity, same container identity, incremented epoch, new thread identity, cleared active turn identity, and correct reset/control artifacts.
- **FR-012**: System MUST validate `interrupt_turn` end to end, including active turn interruption, state updates, controller-visible interruption event, artifacts, and query state.
- **FR-013**: System MUST validate `terminate_session` cleanup end to end, including runtime removal, supervision finalization, terminal record state, and absence of orphaned runtime state.
- **FR-014**: System MUST validate that `cancel_session` has semantics distinct from termination and preserves the intended recoverable or idle session state.
- **FR-015**: System MUST validate the `steer_turn` contract, including deterministic failure when unavailable behind a guard and success-path behavior when enabled.
- **FR-016**: System MUST validate restart and reconcile behavior by persisting active records and verifying recovery reattaches supervision or marks missing runtime state degraded.
- **FR-017**: System MUST validate race and idempotency behavior for duplicate controls, stale epochs, updates before handles are attached, and parent/session shutdown races.
- **FR-018**: System MUST validate Continue-As-New carry-forward for locator, epoch, continuity references, and dedupe or request-tracking state.
- **FR-019**: System MUST include replay validation for representative managed session histories as a required gate for workflow-shape changes.
- **FR-020**: Tests for this feature MUST be created before or alongside the runtime behavior they validate, so missing behavior is observable as a failing test during development.
- **FR-021**: System MUST provide metrics, tracing, and log correlation for managed session workflow/runtime operations using bounded task, runtime, session, epoch, status, and degradation identifiers without exposing prompts, transcripts, scrollback, raw logs, credentials, or secrets.

### Key Entities *(include if feature involves data)*

- **Managed Session Visibility Metadata**: Bounded operator-facing session identity and state, including task run ID, runtime ID, session ID, session epoch, session status, degradation flag, and compact continuity references.
- **Managed Session Control Operation**: A lifecycle mutation such as send, steer, interrupt, clear, cancel, or terminate that changes session state or runtime work.
- **Managed Session Reconcile Outcome**: A compact recovery result that records counts, bounded identifiers, and safe reasons for reattached, degraded, terminated, or orphaned sessions.
- **Lifecycle Test Fixture**: A deterministic validation scenario or replay fixture that proves managed session behavior without exposing prompts, transcripts, raw logs, credentials, or secrets.
- **Continue-As-New Carry-Forward State**: The compact session state that must survive long-lived workflow handoff, including locator, epoch, continuity refs, and dedupe/request-tracking data.
- **Managed Session Telemetry Context**: Bounded correlation identifiers and dimensions used by metrics, traces, and structured logs for managed session workflow and runtime operations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every listed major session transition, operators can identify session identity, epoch, phase, degradation state, and latest compact continuity refs from bounded metadata.
- **SC-002**: Safety review of indexed visibility, workflow metadata, schedule metadata, activity summaries, and replay fixtures finds zero prompts, transcripts, scrollback, raw logs, credentials, or secret values.
- **SC-003**: Launch, send, interrupt, clear, cancel, steer, and terminate operations each produce readable bounded history summaries.
- **SC-004**: Runtime/container work is demonstrably routed through the runtime boundary and not through workflow-processing workers.
- **SC-005**: Recurring reconcile can be created or updated idempotently and reports bounded outcomes for stale, degraded, missing, and orphaned managed session state.
- **SC-006**: Lifecycle integration tests cover create, attach handles, send follow-up, clear invariants, interrupt, cancel, terminate cleanup, restart/reconcile, races/idempotency, and Continue-As-New carry-forward.
- **SC-007**: Replay validation covers representative managed session histories and is required for workflow-shape changes.
- **SC-008**: The required runtime verification suite passes with no credentials or external provider access.
- **SC-009**: Metrics, traces, and structured logs for managed session operations can be correlated by bounded task/session/runtime identifiers and pass the same forbidden-value safety review as workflow metadata.

## Assumptions

- Runtime mode is the default intent for this feature.
- Existing complete Phase 4 or Phase 5 behavior should be preserved and verified rather than reimplemented.
- Provider verification tests remain separate from required hermetic validation unless explicitly enabled by an operator.
- Bounded artifact references are safe to expose as continuity metadata; artifact contents remain outside indexed visibility and summaries.
