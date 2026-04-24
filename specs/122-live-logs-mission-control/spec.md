# Feature Specification: live-logs-mission-control

**Feature Branch**: `122-live-logs-mission-control`
**Created**: 2026-04-01
**Status**: Draft
**Input**: User description: "Finish implementing docs/ManagedAgents/LiveLogs.md Use test-driven development where appropriate. Test like 'long-running streams' should be simulated in the integration tests. When done, the live logs should properly show up in the mission control UI when the updated code is deployed."

## Source Requirements

- **DOC-REQ-001**: Execution detail for a logical workflow must expose `taskRunId` from a durable workflow-to-managed-run binding rather than depending only on a best-effort memo patch. Source: `docs/ManagedAgents/LiveLogs.md` Workstream A.
- **DOC-REQ-002**: Managed-run persistence must store the owning workflow binding on the managed run record and only report the binding after the managed run record has been saved successfully. Source: `docs/ManagedAgents/LiveLogs.md` Workstream A.
- **DOC-REQ-003**: Mission Control task owners and admins must be able to read `/api/task-runs/*` observability endpoints for runs they are already authorized to view, while cross-owner access is forbidden. Source: `docs/ManagedAgents/LiveLogs.md` Workstream C.
- **DOC-REQ-004**: The task detail page must keep polling execution detail while a task is launchable/running and automatically attach the observability panels once `taskRunId` appears. Source: `docs/ManagedAgents/LiveLogs.md` Workstream B.
- **DOC-REQ-005**: The task detail page must distinguish "not launched yet", "launch failed / no managed run created", and "binding missing while execution is already running" instead of showing one generic placeholder. Source: `docs/ManagedAgents/LiveLogs.md` Workstream B.
- **DOC-REQ-006**: Observability UI panels must surface authorization failures distinctly from "no logs yet". Source: `docs/ManagedAgents/LiveLogs.md` Workstream C.
- **DOC-REQ-007**: Add an integration validation path from managed launch to observability APIs using simulated long-running streams. Source: `docs/ManagedAgents/LiveLogs.md` Workstream D and user request.
- **DOC-REQ-008**: Add a browser-facing test path that uses the real execution-detail payload shape and delayed `taskRunId` arrival rather than injecting `taskRunId` directly into the component. Source: `docs/ManagedAgents/LiveLogs.md` Workstream D.

## User Scenarios & Testing

### User Story 1 - Task Detail Attaches To Real Managed Runs (Priority: P1)

An operator opening Mission Control task detail must see live logs attach automatically once the managed runtime exists for that execution.

**Why this priority**: The feature is not complete until real task detail pages can discover the managed run id without manual refreshes or placeholder dead-ends.

**Independent Test**: Launch a managed run for a workflow, fetch `/api/executions/{workflowId}`, and verify `taskRunId` is present even when the execution memo did not already contain it.

**Acceptance Scenarios**:

1. **Given** a managed run launches successfully for a workflow, **When** execution detail is fetched, **Then** the response includes the managed run UUID as `taskRunId`.
2. **Given** a workflow has an older terminal managed run and a newer active managed run, **When** execution detail is fetched, **Then** the active/latest run id is returned.
3. **Given** launch fails before the managed run record is saved, **When** execution detail is fetched, **Then** no dangling `taskRunId` is reported.

### User Story 2 - Observability Access Matches Execution Ownership (Priority: P1)

Task owners need the observability APIs to behave consistently with execution detail authorization.

**Why this priority**: Even with the correct `taskRunId`, owners still cannot use Mission Control live logs if `/api/task-runs/*` remains admin-only.

**Independent Test**: Exercise observability summary and merged-log routes as an admin, as the owning user, and as a different user.

**Acceptance Scenarios**:

1. **Given** the requesting user owns the workflow, **When** they request observability summary or logs for the bound managed run, **Then** the request succeeds.
2. **Given** the requesting user does not own the workflow, **When** they request observability for that run, **Then** the API rejects the request.
3. **Given** Mission Control receives a 403 from observability, **When** it renders the panel, **Then** it shows an authorization-specific message instead of "no logs yet".

### User Story 3 - Mission Control Shows Accurate Launch-State Feedback (Priority: P1)

Operators need the task-detail page to explain whether logs are still waiting for launch, unavailable because launch failed, or missing because binding is broken.

**Why this priority**: One generic placeholder hides the real operational problem and blocks troubleshooting.

**Independent Test**: Drive the task-detail page with execution-detail payloads representing pre-launch, launch failure, and running-without-binding states.

**Acceptance Scenarios**:

1. **Given** a task is still launching and has no `taskRunId` yet, **When** task detail renders, **Then** it shows a waiting-for-launch message and later attaches automatically when `taskRunId` appears.
2. **Given** a task reaches a terminal failure without any managed run binding, **When** task detail renders, **Then** it shows that no managed runtime observability record was created.
3. **Given** a task is already executing but still has no managed run binding after a reasonable window, **When** task detail renders, **Then** it shows a degraded binding-missing message.

### User Story 4 - Long-Running Streams Remain Observable End-To-End (Priority: P2)

Operators need artifact-backed and live observability to remain functional for long-running streaming workloads, not just short mocked outputs.

**Why this priority**: The real product risk is long-running managed CLI tasks whose output arrives over time.

**Independent Test**: Simulate a long-running managed process that emits output in multiple chunks, then verify summary and merged-tail visibility while the run is active.

**Acceptance Scenarios**:

1. **Given** a long-running managed process emits output over time, **When** observability summary is fetched during the run, **Then** it reports live-stream capability for the active run.
2. **Given** that same active run, **When** merged logs are fetched before completion, **Then** visible content is returned from the spool-backed path.
3. **Given** task detail receives the real execution-detail payload without an initial `taskRunId`, **When** polling discovers the later `taskRunId`, **Then** the UI transitions from waiting to attached observability panels without a reload.

## Requirements

### Functional Requirements

- **FR-001**: The managed runtime path MUST persist the owning logical workflow id on `ManagedRunRecord`.
- **FR-002**: Managed-run binding to execution detail MUST be persisted only after the managed run record has been saved successfully.
- **FR-003**: `/api/executions/{workflowId}` MUST derive `taskRunId` from a durable source when execution memo/search attributes do not already contain it.
- **FR-004**: `/api/task-runs/*` observability routes MUST allow admins and owning users, and MUST reject cross-owner access.
- **FR-005**: The task-detail React view MUST stop treating the Temporal run id as a fallback `taskRunId`.
- **FR-006**: The task-detail React view MUST keep polling execution detail until `taskRunId` appears for a launchable/running execution.
- **FR-007**: The task-detail React view MUST distinguish waiting, launch-failed, binding-missing, and authorization-failed observability states.
- **FR-008**: Integration coverage MUST simulate long-running streaming output through the managed runtime observability path.

### Key Entities

- **Managed Run Record**: Durable JSON record for a managed runtime run, now including the owning logical workflow id.
- **Execution Detail View**: `/api/executions/{workflowId}` payload consumed by Mission Control task detail.
- **Observability Routes**: `/api/task-runs/{id}/observability-summary`, `/logs/merged`, `/logs/stream`, and related artifact-backed read endpoints.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Real task detail responses expose `taskRunId` for active managed runs without depending on a preexisting memo patch.
- **SC-002**: Owners can successfully load observability summary and logs for their own runs, while cross-owner requests are rejected.
- **SC-003**: Task detail transitions from a no-binding waiting state to attached live logs automatically when a managed run id appears.
- **SC-004**: Integration coverage simulates long-running streaming output and verifies active-run observability behavior from the artifact/spool-backed path.
