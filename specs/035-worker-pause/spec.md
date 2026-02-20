# Feature Specification: Worker Pause System

**Feature Branch**: `035-worker-pause`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "Implement the Worker Pause System described in docs/WorkerPauseSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Production runtime code changes and companion validation tests are mandatory deliverables for this feature.  
**Source Document**: docs/WorkerPauseSystem.md (last updated 2026-02-19)

## Problem Statement & Goals
MoonMind operators need a single, auditable control that pauses every queue worker (Codex, Gemini, Claude, Manifest, Universal) without disturbing queued or running jobs so maintenance windows can occur safely. Pausing must block new claims, preserve queue state, expose a clear banner/control surface, and optionally request running jobs to quiesce at checkpoints. The system must follow the Pause → Drain → Upgrade → Resume workflow, surface progress, and ensure workers behave predictably while paused.

Primary goals:
- Stop new work from starting across all worker runtimes immediately once paused.
- Keep existing queue data untouched; no implicit retries or dead-lettering may occur during a pause.
- Provide operators with a single control point (API + dashboard) that requires reason entry, tracks the operator identity, and emits an audit record for every action.
- Give operators real-time visibility of drain progress, queued/running counts, and stale leases so they know when the system is safe to upgrade.
- Offer an optional Quiesce mode so running jobs can pause at checkpoints while continuing to heartbeat, minimizing restart disruption.

## Source Document Requirements
| ID | Source Reference | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | §2 Goals, §4 | Provide an operator-driven pause/resume control that blocks new work across all worker types while keeping queued jobs untouched and supporting the Pause → Drain → Upgrade → Resume workflow. |
| DOC-REQ-002 | §5.1, §6.1 | Persist global pause state in a singleton `system_worker_pause_state` record with paused flag, mode (`drain`/`quiesce`), reason, requesting user, timestamps, and monotonically increasing version for change detection. |
| DOC-REQ-003 | §6.2 | Capture every pause/resume action in an append-only `system_control_events` audit log including action, mode, reason, actor, and timestamp. |
| DOC-REQ-004 | §5.1.2, §9.1 | Guard the queue claim path so that when paused it returns metadata immediately and never calls repository claim logic (including `_requeue_expired_jobs`) to avoid mutating queue state. |
| DOC-REQ-005 | §7.2 | Extend queue claim and heartbeat responses with a `system` object that conveys pause status, mode, reason, version, and timestamps to workers without breaking existing payload contracts. |
| DOC-REQ-006 | §7.1 | Provide operator-only `GET` and `POST /api/system/worker-pause` endpoints exposing current pause state, queued/running metrics, `isDrained`, and allowing pause/resume requests with reason and mode validation. |
| DOC-REQ-007 | §8 | Define worker runtime behavior: treat paused claim responses as idle loops with pause-specific backoff, log status once per `system.version`, and in Quiesce mode pause work at safe checkpoints while continuing heartbeats. |
| DOC-REQ-008 | §10 | Display a dashboard banner/control that surfaces worker status (Running vs Paused with mode), provides Pause/Resume buttons with mode + reason entry, and shows drain progress indicators. |
| DOC-REQ-009 | §4.1, §8.2, §11 | Offer both Drain (default) and Quiesce modes so operators can let running jobs finish or pause at checkpoints, with clear guidance on when upgrades are safe. |
| DOC-REQ-010 | §7.1, §9.2 | Report queued, running, and stale-running counts while paused; stale leases remain visible until workers resume, helping operators detect stragglers without triggering normalization. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pause Workers for Upgrades (Priority: P1)
An authenticated operator needs to halt all new work before rolling out image rebuilds or schema migrations. They open the dashboard, choose Drain mode, supply a reason, and the system immediately blocks new claims while current jobs finish.

**Why this priority**: Without a reliable pause, upgrades can corrupt job state or trigger unexpected retries, making this the most critical capability.

**Independent Test**: Trigger a pause via API/dashboard, verify that the queue claim endpoint returns `job=null` with pause metadata, and confirm no new jobs start while running jobs complete normally.

**Acceptance Scenarios**:
1. **Given** active workers, **when** the operator submits `Pause (Drain)` with a reason, **then** `/api/system/worker-pause` records the request, emits an audit event, and `POST /api/queue/jobs/claim` returns `{ job: null, system: {...workersPaused:true...} }` without repository calls.
2. **Given** the system is paused in Drain, **when** currently running jobs finish, **then** queued jobs remain untouched and the running count drops to zero without surprise retries.
3. **Given** the operator resumes the system, **when** the request is accepted, **then** claim responses immediately return normal jobs and an audit entry records the resume action.

---

### User Story 2 - Monitor Drain Progress and Resume Safely (Priority: P2)
Operators need confidence that it is safe to restart workers. They rely on the dashboard/API metrics to observe running, queued, and stale-running counts plus an `isDrained` indicator before restarting services.

**Why this priority**: Drain visibility determines when maintenance can proceed without disrupting jobs.

**Independent Test**: Pause the system, watch drain metrics until `isDrained=true`, perform worker restarts, and verify job queues remain stable.

**Acceptance Scenarios**:
1. **Given** a pause is active, **when** the operator polls `/api/system/worker-pause`, **then** the response includes queued, running, stale-running, and `isDrained` fields updated at least every polling interval.
2. **Given** stale-running jobs exist because a worker crashed, **when** the operator views the dashboard, **then** a clear indicator shows `staleRunning>0` so they can intervene before resuming.
3. **Given** `isDrained=true`, **when** the operator resumes workers after maintenance, **then** the state transitions to Running and the metrics banner reflects the change within one refresh cycle.

---

### User Story 3 - Pause Running Jobs at Checkpoints (Quiesce Mode) (Priority: P3)
For short maintenance windows, operators request Quiesce mode so running jobs pause at safe checkpoints while retaining leases. Workers learn the quiesce instruction through heartbeat responses and stop launching new steps until resumed.

**Why this priority**: Quiesce reduces disruption during brief maintenance without forcing jobs to restart from scratch.

**Independent Test**: Initiate a Quiesce pause, run a long-lived job, verify worker logs show "paused" once the next checkpoint is reached, and confirm the job resumes automatically once the system is resumed.

**Acceptance Scenarios**:
1. **Given** the operator requests `Pause (Quiesce)`, **when** workers heartbeat, **then** they receive `system.mode="quiesce"` and pause at the next wrapper-stage or task-step boundary while continuing to heartbeat.
2. **Given** a worker has paused at a checkpoint, **when** the operator resumes workers, **then** the worker logs the new `system.version` and continues executing from the next checkpoint without losing its lease.
3. **Given** a worker ignores quiesce instructions, **when** monitoring detects the same `system.version` persisting beyond the allowed interval, **then** alerts fire so operators can intervene.

### Edge Cases
- Simultaneous pause requests from multiple operators must serialize so only the latest accepted request updates the singleton state; later conflicting requests fail with a descriptive message.
- A pause request while already paused updates the reason/mode only when it increases operator clarity; otherwise it is rejected to avoid surprise transitions.
- Resume commands issued while jobs are still running prompt a confirmation that `isDrained=false` so operators knowingly accept the risk.
- Workers that are offline during a pause read the `system.version` on their next claim/heartbeat and immediately enter paused behavior without needing a restart.
- Network partitions or DB failures default to "fail safe" by keeping workers paused until a healthy state record can be read, preventing unintended job starts.

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: The platform MUST persist a singleton `system_worker_pause_state` record (id=1) containing paused flag, mode (`drain` default, `quiesce` optional), reason, requesting operator identity (when available), requested_at, updated_at, and a monotonically increasing version so workers can detect changes. *(Maps: DOC-REQ-002)*
- **FR-002**: `POST /api/system/worker-pause` MUST accept `{action: pause|resume, mode: drain|quiesce, reason}` requests from authorized operators, validate that reason is non-empty on pause, and update the singleton state plus emit an immediate audit entry. *(Maps: DOC-REQ-001, DOC-REQ-006)*
- **FR-003**: `GET /api/system/worker-pause` MUST return the current state plus `queuedCount`, `runningCount`, `staleRunningCount`, and boolean `isDrained=(runningCount==0 && staleRunningCount==0)` so operators have real-time situational awareness. *(Maps: DOC-REQ-006, DOC-REQ-010)*
- **FR-004**: `POST /api/queue/jobs/claim` MUST check the pause state first and, when paused, immediately return `{job:null, system:{workersPaused:true, mode, reason, version, updatedAt}}` without invoking repository claim logic or `_requeue_expired_jobs`, ensuring the queue remains undisturbed. *(Maps: DOC-REQ-004, DOC-REQ-010)*
- **FR-005**: Worker claim loops MUST treat the paused response as a non-error idle state, respect a configurable `pause_poll_interval_ms` (default 3–10s) before the next claim, and log the pause status once per `system.version` to avoid log spam. *(Maps: DOC-REQ-005, DOC-REQ-007)*
- **FR-006**: `POST /api/queue/jobs/{jobId}/heartbeat` MUST attach an optional `system` payload mirroring pause metadata so running workers learn about Drain/Quiesce transitions, pause at wrapper-stage or step checkpoints in Quiesce, yet continue heartbeating to preserve leases. *(Maps: DOC-REQ-005, DOC-REQ-007, DOC-REQ-009)*
- **FR-007**: Every pause/resume action MUST append a row to `system_control_events` capturing control=`"worker_pause"`, action, mode, reason, actor, and timestamp so audits and incident timelines remain trustworthy. *(Maps: DOC-REQ-003)*
- **FR-008**: The dashboard MUST display a persistent global banner showing `Workers: Running` vs `Workers: Paused (Drain/Quiesce)`, include Pause/Resume controls with mode selection and required reason input, and immediately reflect the latest `system.version`. *(Maps: DOC-REQ-008, DOC-REQ-009)*
- **FR-009**: The dashboard MUST surface drain progress via running/queued charts, `isDrained` indicator, and `staleRunning` callouts so operators know when it is safe to restart services; warnings appear if resume is attempted while `isDrained=false`. *(Maps: DOC-REQ-006, DOC-REQ-010)*
- **FR-010**: Quiesce mode MUST keep new claims blocked, send checkpoint pause instructions to running jobs, and guarantee that once `resume` is issued workers automatically continue where they stopped without duplicating steps. *(Maps: DOC-REQ-007, DOC-REQ-009)*
- **FR-011**: MCP tooling (`queue.claim`, `queue.heartbeat`) MUST return the same `system` metadata as the HTTP API so IDE/CLI integrations respect pauses identically to worker services. *(Maps: DOC-REQ-005)*
- **FR-012**: The system MUST expose telemetry (metrics/logs/events) for pause/resume actions, claim guard hits, stale-running counts, and quiesce checkpoint compliance so automated alerts can detect misbehavior. *(Maps: DOC-REQ-001, DOC-REQ-007, DOC-REQ-010)*

### Key Entities *(include if feature involves data)*
- **SystemWorkerPauseState**: Singleton data model storing pause flag, mode, reason, requesting operator, timestamps, and version; drives guards for API and workers.
- **SystemControlEvent**: Append-only audit entry capturing every pause/resume action with metadata for compliance reviews.
- **WorkerPauseViewModel**: Aggregation returned by GET endpoint and dashboard, combining pause state with queue metrics (queued, running, stale, isDrained, reason, updatedAt).
- **QueueSystemMetadata Envelope**: Optional `system` object included in claim and heartbeat responses so workers know the current pause status without altering existing job payload structures.

### Assumptions & Dependencies
- Operator authentication/authorization already exists; this feature reuses existing admin scopes to protect POST operations.
- Queue repositories continue to manage job lifecycle (queued → running → terminal); this feature only guards entry points and surface telemetry.
- Worker runtimes already support periodic claim + heartbeat loops and per-step checkpoint hooks that can honor quiesce instructions without re-architecting job execution.
- Metrics for queued/running counts already exist or can be derived from the task repository with ≤5s freshness.

### Non-Goals
- Forcefully terminating worker subprocesses or snapshotting their state mid-command (per v1 non-goals).
- Guaranteeing durable checkpoint/resume across worker restarts (future phase).
- Providing identical pause semantics for every MoonMind subsystem beyond the Task Queue contract.

## Success Criteria *(mandatory)*

### Measurable Outcomes
- **SC-001**: Within 5 seconds of submitting a pause request, 100% of queue claim attempts observe `workersPaused=true`, ensuring no new jobs start during maintenance windows.
- **SC-002**: While paused, zero queued job state transitions are triggered by `_requeue_expired_jobs` or similar normalization routines, preserving queue integrity.
- **SC-003**: Dashboard/API metrics for queued, running, and stale-running counts refresh at least every 10 seconds and maintain ±1 job accuracy compared to the source of truth so operators can make decisions confidently.
- **SC-004**: 100% of pause/resume actions produce corresponding entries in `system_control_events` with populated reason, mode, actor (when available), and timestamps, enabling full audit coverage.
- **SC-005**: In Quiesce mode, at least 95% of running jobs acknowledge the pause and reach a safe checkpoint within 60 seconds, as evidenced by worker telemetry, preventing long-lived tasks from progressing while maintenance is underway.

### Validation Approach
- Unit tests cover API handlers (pause/resume, claim guard, metrics projection) and worker runtime behaviors (paused claim handling, quiesce heartbeats).
- Integration smoke tests simulate Pause → Drain → Upgrade → Resume and Quiesce workflows end-to-end via Docker Compose environment.
- Manual dashboard verification ensures banner state, controls, and drain indicators render accurately against a seeded queue.

## Risks & Mitigations
- **Risk**: Workers ignoring pause instructions could continue mutating resources. *Mitigation*: versioned metadata + alerts on claim attempts during pause and mandatory checkpoint logging.
- **Risk**: Operators may forget to resume workers. *Mitigation*: dashboard banner + optional alert when paused duration exceeds configurable threshold.
- **Risk**: Database unavailability could block pause reads/writes. *Mitigation*: keep last-known pause state in worker memory and default to "paused" when state cannot be read.
