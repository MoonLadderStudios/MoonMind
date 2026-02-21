# Feature Specification: Worker Pause System

**Feature Branch**: `034-worker-pause`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "Implement the Worker Pause System described in docs/WorkerPauseSystem.md"

MoonMind operators need a deterministic, auditable switch that halts new queue work without disturbing queued jobs so infrastructure upgrades can run safely. The Worker Pause System introduces a persistent pause state, API guardrails, worker runtime changes, and dashboard controls aligned with the reference design in `docs/WorkerPauseSystem.md`.

## Source Document Requirements

| ID | Source | Requirement |
| --- | --- | --- |
| DOC-REQ-001 | docs/WorkerPauseSystem.md §5.1-§6 | Provide a singleton `system_worker_pause_state` record (id=1) storing `paused`, `mode`, `reason`, operator metadata, timestamps, and a monotonic `version` that serves as the source of truth for whether claims are allowed. |
| DOC-REQ-002 | §6.2, §12 | Append every pause/resume action to `system_control_events` with `control="worker_pause"`, action, mode, reason, actor, and timestamp for auditing. |
| DOC-REQ-003 | §2, §7.1 | Expose operator endpoints `GET /api/system/worker-pause` and `POST /api/system/worker-pause` that reveal the pause state, require a reason, and compute drain metrics (`queued`, `running`, `staleRunning`, `isDrained`). |
| DOC-REQ-004 | §2, §5.1-§5.3, §7.2, §9 | When paused, `POST /api/queue/jobs/claim` must return `{job:null, system:{...}}` and **skip** repository claim logic (no `_requeue_expired_jobs`) so queue contents stay untouched. |
| DOC-REQ-005 | §7.2 | `POST /api/queue/jobs/{jobId}/heartbeat` responses must remain backward compatible but add a `system` object telling running jobs about pause/quiesce status. |
| DOC-REQ-006 | §4-§8.1 | All worker runtimes must treat `workersPaused=true` claim responses as an idle loop with pause-aware backoff and per-version logging (no crashes or job failures). |
| DOC-REQ-007 | §4.1, §8.2 | Quiesce mode instructs workers to pause at safe checkpoints (stage boundaries, task steps, or tool invocations) while continuing to heartbeat so leases remain valid. |
| DOC-REQ-008 | §5, §10 | The dashboard must show a global Workers badge, allow Pause/Resume with mode + reason input, and display drain progress (running vs queued counts, safe-to-upgrade indicator). |
| DOC-REQ-009 | §7.3 | MCP tools (`queue.claim`, `queue.heartbeat`) must propagate the same pause metadata as REST APIs to keep CLI integrations consistent. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pause for upgrades (Priority: P1)

MoonMind operators pause all workers in Drain mode to perform an orchestrator upgrade without new jobs starting.

**Why this priority**: The pause/drain workflow prevents in-flight state corruption during deployments and is the primary reason the system exists.

**Independent Test**: Call `POST /api/system/worker-pause` (Drain) and verify that subsequent claim attempts immediately return `job=null` with pause metadata while running jobs complete naturally and audit entries are recorded.

**Acceptance Scenarios**:

1. **Given** workers are running jobs, **When** an operator pauses in Drain mode, **Then** the pause state is persisted, claim endpoints return `workersPaused=true`, and no additional jobs begin.
2. **Given** the system is paused, **When** the running job count reaches zero, **Then** `GET /api/system/worker-pause` reports `isDrained=true`, signalling it is safe to restart workers.

---

### User Story 2 - Resume after maintenance (Priority: P2)

Operators need to resume normal throughput quickly after maintenance, with assurance that the queue remained intact.

**Why this priority**: Fast recovery keeps SLAs stable and proves the pause workflow does not lose or reorder queued work.

**Independent Test**: Resume via `POST /api/system/worker-pause` with `action="resume"` and confirm claims/repository flows reactivate, audit events include the resume, and dashboard badge swaps back to Running.

**Acceptance Scenarios**:

1. **Given** workers are paused with queued jobs, **When** an operator resumes, **Then** claims immediately start handing out queued jobs without dead-lettering side effects.
2. **Given** multiple pauses occur in one day, **When** the operator checks system control events, **Then** every pause/resume pair is logged with timestamp, actor, reason, and mode for compliance.

---

### User Story 3 - Quiesce running jobs (Priority: P3)

During a short maintenance window, operators pause in Quiesce mode so workers checkpoint and wait without terminating processes.

**Why this priority**: Quiesce protects long-running or stateful tool executions when restarts are not desired, fulfilling the optional goal outlined in the design.

**Independent Test**: Enable Quiesce, monitor heartbeat responses returning `system.mode="quiesce"`, and assert that workers pause between stage or step boundaries while heartbeating to prevent lease expiry.

**Acceptance Scenarios**:

1. **Given** Quiesce is active, **When** a worker completes a stage boundary, **Then** it pauses before starting the next stage yet continues heartbeating.
2. **Given** an operator resumes from Quiesce, **When** workers receive the updated version via heartbeat, **Then** they resume progression within one poll interval.

### Edge Cases

- Pause requested while the system is already paused should update reason/mode only if it represents a meaningful change and must still append an audit entry so intent is traceable.
- Resume requested when not paused should be rejected with a clear error to avoid accidental toggles.
- Network partitions during pause must default workers to safe idle behavior once they eventually receive pause metadata, and stale running jobs must surface in `staleRunning` counts to highlight risk.
- Heartbeat failures in Quiesce must not drop leases silently; workers should alert operators if checkpointing exceeds a configurable timeout.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001)**: Persist a singleton `system_worker_pause_state` row with columns defined in the document, enforce `id=1`, increment `version` on every change, and expose helper queries so API handlers can atomically read and update the record.
- **FR-002 (DOC-REQ-002)**: Insert an audit `system_control_events` row for every pause/resume request capturing action, mode, reason, actor (if available), and timestamps to satisfy compliance reviews.
- **FR-003 (DOC-REQ-003, DOC-REQ-009)**: Implement `GET /api/system/worker-pause` returning pause metadata plus queue metrics (`queued`, `running`, `staleRunning`, `isDrained`) and ensure both REST and MCP transports expose the same schema.
- **FR-004 (DOC-REQ-003)**: Implement `POST /api/system/worker-pause` that validates `action`, `mode`, and `reason`, flips pause state, records requester metadata, and rejects illegal transitions (e.g., resume while already running).
- **FR-005 (DOC-REQ-004)**: Short-circuit `POST /api/queue/jobs/claim` when `paused=true` so repository claim logic and `_requeue_expired_jobs` are skipped, returning `{job:null, system:{workersPaused,...}}` immediately.
- **FR-006 (DOC-REQ-005, DOC-REQ-009)**: Extend claim and heartbeat responses (including MCP equivalents) with a `system` object containing `workersPaused`, `mode`, `reason`, `version`, and timestamps without breaking existing consumers.
- **FR-007 (DOC-REQ-006)**: Update worker runtimes to treat paused responses as non-fatal idle loops by backing off with `pause_poll_interval_ms` (default to existing poll interval), logging once per version, and keeping telemetry noise low.
- **FR-008 (DOC-REQ-007)**: Implement Quiesce checkpoint handlers so workers pause between wrapper stages, task steps, or tool invocations while heartbeating to hold leases, and expose configuration for safe checkpoint detection.
- **FR-009 (DOC-REQ-008)**: Update the dashboard to show a global Workers badge, Pause/Resume controls (mode selector + reason), and a drain progress card with queued/running counts plus “Safe to upgrade” when running count hits zero.
- **FR-010 (DOC-REQ-003, DOC-REQ-004)**: Compute drain status server-side, surface `isDrained` and `staleRunning` counts, and document the recommended Pause → Drain → Upgrade → Resume playbook so operators know when to restart workers.

### Non-Functional Requirements

- **NFR-001 (DOC-REQ-004, DOC-REQ-005)**: Claim and heartbeat endpoints must continue responding in <200 ms while paused by short-circuiting the repository path and caching the singleton pause row in memory so workers never assume the queue is unavailable.
- **NFR-002 (DOC-REQ-001, DOC-REQ-002)**: Pause state transitions are atomic and monotonic—the API must perform `SELECT ... FOR UPDATE` writes, bump `version` exactly once per change, and emit structured audit/StatsD events so operators can correlate dashboard badges with backend changes.
- **NFR-003 (DOC-REQ-006, DOC-REQ-007)**: Worker runtimes must log a single structured line per pause `version`, respect `pause_poll_interval_ms`, and surface a warning if Quiesce checkpoints exceed the configured timeout to avoid noisy logs during long pauses.
- **NFR-004 (DOC-REQ-008, DOC-REQ-009)**: Dashboard and MCP clients treat the pause metadata as an additive schema—existing consumers that ignore `system` blocks keep working, and UI components show degraded banners instead of crashing when the pause API times out.

### Key Entities *(include if feature involves data)*

- **SystemWorkerPauseState**: Singleton record cached in API layer; fields `paused`, `mode`, `reason`, `requested_by_user_id`, `requested_at`, `updated_at`, and `version` drive claim guards and UI badges.
- **SystemControlEvent**: Append-only audit row keyed by UUID capturing `control`, `action`, `mode`, `reason`, `actor_user_id`, and `created_at` for every pause/resume.
- **SystemPauseMetrics**: Aggregated counts `queued`, `running`, `staleRunning`, plus derived `isDrained` computed from repository queries and exposed via API/UI for operator visibility.

### Assumptions & Dependencies

- In external auth modes, existing authentication already distinguishes operators/admins and this feature relies on that gating to protect the pause endpoint.
- With `AUTH_PROVIDER=disabled`, pause control is intentionally available without auth gates for local development, attributed to the local default user identity.
- Worker Pause controls are rendered in the `/tasks` dashboard shell as a global banner and should remain visible even when the queue is idle.
- Queue repository APIs expose efficient metrics for queued/running/stale counts so `GET /api/system/worker-pause` can compute drain progress without degrading performance.
- Worker runtimes already implement checkpoint semantics for per-run pause controls; Quiesce will leverage the same hooks.

## Operational Workflow & Observability

### Pause → Drain → Upgrade → Resume

1. **Pause (Drain or Quiesce)**: Operators issue `POST /api/system/worker-pause` with mode + reason. The API validates action/mode, records `SystemControlEvent`, bumps `version`, and returns the latest state so dashboards can refresh immediately.
2. **Drain Monitoring**: `GET /api/system/worker-pause` aggregates `queued`, `running`, `staleRunning`, and derives `isDrained`. `staleRunning` specifically surfaces expired leases that still require operator intervention before restarting workers.
3. **Upgrade Window**: Once `isDrained=true`, operators proceed with image rebuilds, migrations, or credential rotations. Claims remain blocked because the API short-circuits before repository normalization, ensuring the queue contents stay untouched during maintenance.
4. **Resume**: Operators send `action="resume"`, which clears `paused`, nulls `mode`, records the audit entry, and unlocks claim behavior without losing queued work. Workers resume polling on their next interval after observing a new `system.version` in either claim or heartbeat responses.

### Observability Expectations

- **Metrics & Logging**: Every pause/resume emits an audit record and StatsD counters (e.g., `worker_pause.state` gauge, `worker_pause.resume` counter) so operations dashboards can alert when the system remains paused longer than a configurable SLA.
- **Dashboard UX Contract**: The dashboard shows `Workers: Paused (Drain|Quiesce)` or `Workers: Running`, includes the operator-provided reason, and highlights when `running=0` to declare “Safe to upgrade.”
- **MCP & Worker Feedback Loop**: MCP tools and worker clients propagate the full `system` block (including `version`, `updatedAt`, and `reason`) so third-party workflows align with the dashboard status and do not start new work unexpectedly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of claim requests made while paused return `job=null` with `system.workersPaused=true` in under 200 ms, proving no new work starts.
- **SC-002**: `GET /api/system/worker-pause` reflects the latest pause toggle (state + reason + version) within 2 seconds of request completion, ensuring operators trust the control plane.
- **SC-003**: During Quiesce, 95% of running jobs pause at their next safe checkpoint without lease expiry, shown by heartbeats remaining healthy throughout the pause window.
- **SC-004**: Dashboard drain panel reaches and displays `isDrained=true` before any documented upgrade restarts proceed, and audit logs capture every pause/resume with zero missing events in regression tests.
