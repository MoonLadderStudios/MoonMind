# Feature Specification: Managed Session Observability and Reconcile

**Feature Branch**: `160-session-observability-reconcile`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 for the Codex managed session plane: add idiomatic Temporal observability and operational separation so operators can see bounded session identity, current phase, latest continuity refs, and degradation state without prompts, transcripts, scrollback, or secrets in indexed visibility fields. Start managed session workflows with static summary/details and update current details on major transitions: session started, active turn running, interrupted, cleared to new epoch, degraded, terminating, and terminated. Upsert only the bounded Search Attributes TaskRunId, RuntimeId, SessionId, SessionEpoch, SessionStatus, and IsDegraded. Add readable activity summaries for launch, send, interrupt, clear, and terminate. Keep heavy Docker/runtime activities separated from workflow processing onto the agent runtime task queue. Add a recurring Temporal Schedule target that invokes managed session reconciliation and checks for orphaned containers or stale degraded sessions. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve existing security constraints: no prompts, transcripts, scrollback, credentials, raw logs, or secrets in Search Attributes, workflow metadata, or schedule metadata."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inspect Active Session State (Priority: P1)

An operator monitoring a managed Codex session needs to identify the session, runtime, epoch, phase, degradation state, and latest continuity references from the durable operator surface without opening raw logs or provider output.

**Why this priority**: Operators need a compact, safe status view before they can troubleshoot or decide whether to intervene.

**Independent Test**: Start a managed Codex session and verify the operator-visible metadata contains only bounded identity, status, degradation, and continuity reference values.

**Acceptance Scenarios**:

1. **Given** a managed Codex session starts, **When** an operator views the workflow in the operational UI, **Then** the session has a static summary and static details containing bounded task/session identity.
2. **Given** a managed Codex session changes phase, **When** the phase becomes started, running, interrupted, cleared, degraded, terminating, or terminated, **Then** current details reflect the latest phase and compact continuity references.
3. **Given** a managed Codex session has prompts, transcripts, scrollback, raw logs, or credentials available elsewhere in the system, **When** visibility metadata is produced, **Then** none of those sensitive or unbounded values appear in the indexed or display metadata.

---

### User Story 2 - Read Control Activity History (Priority: P2)

An operator reviewing session history needs control activities to be understandable from the timeline without opening every activity payload.

**Why this priority**: Readable activity history shortens incident review and reduces the need to inspect payloads that may contain lower-level runtime details.

**Independent Test**: Execute launch and common control operations for a managed session and verify their history entries include readable, bounded summaries.

**Acceptance Scenarios**:

1. **Given** a managed session launch is scheduled, **When** the activity appears in history, **Then** the activity summary identifies it as a managed Codex session launch without exposing instructions or secrets.
2. **Given** send, interrupt, clear, or terminate operations are scheduled, **When** each activity appears in history, **Then** the activity summary identifies the control operation and bounded session identifiers.

---

### User Story 3 - Recover Stale Sessions Recurringly (Priority: P3)

An operator needs managed session reconciliation to run durably on a recurring basis so orphaned containers and stale degraded records are detected without manual polling.

**Why this priority**: Recurring recovery reduces operational leaks and keeps the supervision record aligned with the actual runtime environment.

**Independent Test**: Configure the recurring reconcile target and verify it invokes managed session reconciliation through the runtime worker boundary and reports a bounded summary.

**Acceptance Scenarios**:

1. **Given** managed sessions may become stale or orphaned, **When** the recurring reconcile trigger fires, **Then** the system invokes the managed session reconciliation path and records a bounded reconciliation outcome.
2. **Given** reconciliation checks runtime/container state, **When** that work is dispatched, **Then** it runs on the runtime activity worker boundary rather than the workflow-processing worker boundary.

### Edge Cases

- A session update arrives before runtime handles are attached; metadata must remain bounded and must not fabricate container or thread identity.
- A control operation fails and leaves the session degraded; visibility must mark degradation without exposing error bodies or logs.
- A clear operation advances the epoch; visibility must show the new epoch and new continuity references.
- A terminate operation completes; visibility must show the terminal state and must not imply the container remains recoverable.
- A recurring reconcile run finds many stale records; the reported outcome must remain bounded.
- Schedule metadata, workflow metadata, and indexed fields must remain safe even when session instructions or logs contain secret-like strings.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST start each managed Codex session with a static operator summary and static operator details containing only bounded task, runtime, session, and epoch identity.
- **FR-002**: System MUST update current operator details for the major managed-session transitions: session started, active turn running, interrupted, cleared to new epoch, degraded, terminating, and terminated.
- **FR-003**: System MUST expose exactly the bounded indexed visibility fields `TaskRunId`, `RuntimeId`, `SessionId`, `SessionEpoch`, `SessionStatus`, and `IsDegraded` for managed session workflow visibility.
- **FR-004**: System MUST NOT place prompts, transcripts, scrollback, raw logs, credentials, secret values, or unbounded provider output in indexed visibility fields, static details, current details, schedule metadata, or activity summaries.
- **FR-005**: System MUST include readable, bounded activity summaries for managed session launch, send, interrupt, clear, and terminate operations.
- **FR-006**: System MUST keep heavy Docker/runtime managed-session work separated from workflow processing by dispatching that work to the runtime activity boundary.
- **FR-007**: System MUST provide a durable recurring operational trigger for managed session reconciliation.
- **FR-008**: System MUST have managed session reconciliation check for stale degraded sessions and orphaned runtime containers and return a bounded operator-readable outcome.
- **FR-009**: Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.
- **FR-010**: Validation tests MUST cover bounded visibility metadata, transition detail updates, activity summaries, runtime worker separation, and recurring reconcile wiring.
- **FR-011**: The recurring managed-session reconcile trigger MUST use Temporal client helper arguments as its configuration source, stable schedule ID `mm-operational:managed-session-reconcile`, workflow ID template `mm-operational:managed-session-reconcile:{{.ScheduleTime}}`, default cadence `*/10 * * * *` in `UTC`, and a safe disabled state that leaves the schedule present but paused until re-enabled.

### Key Entities *(include if feature involves data)*

- **Managed Session Visibility Metadata**: Bounded operator-facing session identity and state, including task run ID, runtime ID, session ID, session epoch, session status, degradation flag, and compact continuity references.
- **Managed Session Transition**: A lifecycle or control boundary that changes what an operator should see for the session, such as start, active turn, interruption, clear/reset, degradation, termination in progress, and terminated.
- **Managed Session Reconcile Outcome**: A bounded summary of recurring recovery work, including counts and compact identifiers for reconciled or degraded session records.
- **Recurring Reconcile Trigger**: Durable operational trigger that starts managed session reconciliation on a configured cadence without relying on ad hoc manual polling. The trigger is configured through Temporal client helper arguments, identified by schedule ID `mm-operational:managed-session-reconcile`, starts workflow ID template `mm-operational:managed-session-reconcile:{{.ScheduleTime}}`, defaults to cron cadence `*/10 * * * *` in `UTC`, and treats disabled configuration as a paused schedule rather than deleting the schedule.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every managed session start and listed major transition, an operator can identify session identity, epoch, phase, degradation state, and latest compact continuity references from the operator-visible metadata.
- **SC-002**: A metadata safety review of indexed fields, static details, current details, schedule metadata, and activity summaries finds zero prompts, transcripts, scrollback, raw logs, credentials, or secret values.
- **SC-003**: Launch, send, interrupt, clear, and terminate activities each produce a readable bounded summary that identifies the control operation without requiring payload inspection.
- **SC-004**: Runtime/container reconciliation work is routed through the runtime activity boundary and not through workflow-processing workers.
- **SC-005**: A recurring reconcile trigger can be created or updated idempotently and invokes managed session reconciliation with a bounded outcome.
- **SC-006**: Automated validation covers the required runtime behavior and passes in the required unit test runner.
- **SC-007**: Schedule validation confirms the stable schedule ID, workflow ID template, default cadence, timezone, and disabled paused-state behavior.
