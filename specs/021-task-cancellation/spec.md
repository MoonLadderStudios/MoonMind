# Feature Specification: Agent Queue Task Cancellation

**Feature Branch**: `021-task-cancellation`  
**Created**: 2026-02-17  
**Status**: Draft  
**Input**: User description: "Implement task cancellation as described in docs/TaskCancellation.md"

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/TaskCancellation.md` "Goals", "Queued cancellation" | Cancelling a queued queue job must transition it to `cancelled` immediately and make it unclaimable. |
| DOC-REQ-002 | `docs/TaskCancellation.md` "Goals", "Running cancellation" | Cancelling a running queue job must set cancellation-request metadata without directly violating worker-owned running-state transitions. |
| DOC-REQ-003 | `docs/TaskCancellation.md` "Data model changes" | Queue job persistence and API models must expose `cancel_requested_at`, `cancel_requested_by_user_id`, and `cancel_reason` fields. |
| DOC-REQ-004 | `docs/TaskCancellation.md` "API surface" (`POST /cancel`) | REST API must expose a cancel request endpoint for authenticated users with idempotent behavior. |
| DOC-REQ-005 | `docs/TaskCancellation.md` "API surface" (`POST /cancel/ack`) | REST API must expose worker cancellation-ack endpoint enforcing ownership/state checks before `running -> cancelled`. |
| DOC-REQ-006 | `docs/TaskCancellation.md` "Repository/service changes" | Retry/requeue paths (manual failure retries and lease-expiry requeue) must not resurrect cancellation-requested jobs. |
| DOC-REQ-007 | `docs/TaskCancellation.md` "Auditability" | Cancellation request and cancellation completion must be visible in queue job events. |
| DOC-REQ-008 | `docs/TaskCancellation.md` "MCP tooling" | MCP registry must expose `queue.cancel` and dispatch to queue cancellation service. |
| DOC-REQ-009 | `docs/TaskCancellation.md` "Task Dashboard Integration" | Dashboard configuration and queue detail UX must expose cancel action and cancellation-requested state. |
| DOC-REQ-010 | `docs/TaskCancellation.md` "Worker behavior" heartbeat section | Worker heartbeat loop must detect cancel-request metadata from API responses and react quickly (capped heartbeat interval). |
| DOC-REQ-011 | `docs/TaskCancellation.md` "Worker behavior" stage boundaries | Worker must stop execution cooperatively when cancellation is requested and acknowledge cancellation instead of completing successfully. |
| DOC-REQ-012 | `docs/TaskCancellation.md` "Subprocess interruption" | Command execution utilities must support cooperative cancellation with best-effort process termination. |
| DOC-REQ-013 | `docs/TaskCancellation.md` "Testing Strategy" | Automated tests must cover repository, service/API, MCP, worker, and dashboard-facing cancellation behavior. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cancel queued jobs immediately (Priority: P1)

An authenticated user can cancel a queued job and see it become terminally cancelled right away.

**Why this priority**: Immediate queued cancellation is the required baseline behavior and prevents work from starting unnecessarily.

**Independent Test**: Submit a queued job, invoke cancellation once or multiple times, and verify status remains `cancelled` with no successful claim possible.

**Acceptance Scenarios**:

1. **Given** a job is `queued`, **When** the user calls cancel, **Then** the job becomes `cancelled`, `finishedAt` is set, and a cancellation event is appended.
2. **Given** a job is already `cancelled`, **When** the user calls cancel again, **Then** the API returns successfully without changing semantics (idempotent no-op).

---

### User Story 2 - Cancel running jobs cooperatively (Priority: P2)

An authenticated user can request cancellation for a running job and the worker cooperatively stops, acknowledges cancellation, and transitions the job to `cancelled`.

**Why this priority**: Running cancellation is required for safe interruption of long-running task execution.

**Independent Test**: Start a running job on a worker, request cancellation, then verify worker heartbeat observes cancellation request and the worker calls cancel-ack, producing terminal `cancelled` state.

**Acceptance Scenarios**:

1. **Given** a job is `running`, **When** user calls cancel, **Then** status remains `running` and cancel-request metadata is stored.
2. **Given** a running job has cancel requested, **When** worker reaches cancellation check/heartbeat, **Then** worker acknowledges cancel and job becomes `cancelled` with ownership cleared.

---

### User Story 3 - Use cancellation consistently across API, MCP, and dashboard (Priority: P3)

Operators can initiate and observe cancellation consistently through REST, MCP tooling, and dashboard UI with clear audit signals.

**Why this priority**: Operational consistency lowers accidental misuse and improves observability.

**Independent Test**: Trigger cancellation via MCP and dashboard, then verify behavior equals REST semantics and queue detail UI reflects cancellation request/completion states.

**Acceptance Scenarios**:

1. **Given** queue tools are discovered via MCP, **When** `queue.cancel` is called, **Then** it returns the same updated job model as REST cancel.
2. **Given** dashboard detail view for a queued/running job, **When** cancel is triggered, **Then** UI reflects action state and cancellation lifecycle fields.

### Edge Cases

- Cancel request races with claim: if claim wins first, cancel stores request metadata; if cancel wins first, claim cannot select the job.
- Retry path after cancel request: retryable failure must not requeue cancellation-requested job.
- Lease expiry path after cancel request: expiry normalization must finalize as cancelled rather than queued retry.
- Late cancel request where work already finished: terminal state remains actual terminal result; cancellation request does not reopen state transitions.
- Worker cancel-ack attempts on non-owned jobs must be rejected with state/ownership conflict.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide `POST /api/queue/jobs/{job_id}/cancel` that supports authenticated cancellation requests for queue jobs. (DOC-REQ-004)
- **FR-002**: System MUST transition `queued -> cancelled` immediately during cancellation request and append cancellation audit events. (DOC-REQ-001, DOC-REQ-007)
- **FR-003**: System MUST, for running jobs, persist cancellation-request metadata while preserving `running` state until worker acknowledgement. (DOC-REQ-002, DOC-REQ-003)
- **FR-004**: System MUST provide `POST /api/queue/jobs/{job_id}/cancel/ack` for worker-owned running jobs to transition `running -> cancelled` with ownership/state enforcement and idempotent handling. (DOC-REQ-005)
- **FR-005**: System MUST ensure retry/requeue logic (failure retries and lease-expiry reclamation) does not requeue jobs with cancellation-request metadata. (DOC-REQ-006)
- **FR-006**: System MUST expose cancellation-request fields in queue API/MCP job serialization as `cancelRequestedAt`, `cancelRequestedByUserId`, and `cancelReason`. (DOC-REQ-003)
- **FR-007**: System MUST expose cancellation through MCP as `queue.cancel` using the same queue service behavior as REST cancellation. (DOC-REQ-008)
- **FR-008**: System MUST expose dashboard queue cancellation affordances and cancellation-requested indicators using queue source configuration endpoints. (DOC-REQ-009)
- **FR-009**: Worker heartbeat processing MUST detect cancellation-request fields with a bounded check interval and set worker-local cancellation state promptly. (DOC-REQ-010)
- **FR-010**: Worker execution flow MUST honor cancellation checks at stage boundaries, stop execution cooperatively, and acknowledge cancellation instead of successful completion. (DOC-REQ-011)
- **FR-011**: Worker command execution helpers MUST support cooperative cancellation with best-effort terminate/kill semantics and log-safe failure handling. (DOC-REQ-012)
- **FR-012**: System MUST include automated tests covering queue repository/service/API, MCP tooling, worker cancellation behavior, and dashboard configuration changes. (DOC-REQ-013)

### Key Entities *(include if feature involves data)*

- **AgentJob cancellation metadata**: additional queue job fields (`cancel_requested_at`, `cancel_requested_by_user_id`, `cancel_reason`) representing cooperative cancellation requests.
- **Cancellation event entries**: append-only queue job events representing request and cancellation acknowledgement outcomes.
- **Cancellation API contracts**: request/response payload contracts for `/cancel` and `/cancel/ack` plus MCP `queue.cancel` input/output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of queued cancellation requests transition queued jobs to `cancelled` in a single API transaction and prevent subsequent claims.
- **SC-002**: 100% of running cancellation requests expose cancellation-request metadata in heartbeat responses before terminal state transition.
- **SC-003**: Worker cancellation flow transitions requested running jobs to `cancelled` without reporting successful completion in at least one automated worker path test.
- **SC-004**: Cancellation regression suite (`./tools/test_unit.sh`) includes passing coverage for repository, API/MCP, worker, and dashboard-related queue configuration behavior.
