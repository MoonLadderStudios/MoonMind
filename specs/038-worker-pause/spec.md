# Feature Specification: Worker Pause System (Temporal Era)

**Feature Branch**: `038-worker-pause`  
**Created**: 2026-02-20  
**Updated**: 2026-03-17  
**Status**: Draft  
**Input**: User description: "Update 038-worker-pause spec and fully implement docs/Temporal/WorkerPauseSystem.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Production runtime code changes and companion validation tests are mandatory deliverables for this feature.  
**Source Document**: docs/Temporal/WorkerPauseSystem.md (last updated 2026-03-17)

MoonMind operators need a single, auditable control that pauses all workflow execution so maintenance windows (image rebuilds, schema migrations, credential rotations) can occur safely. With the migration to Temporal, we rely on Temporal's native primitives — **graceful worker shutdown** for Drain and **Batch Signals** for Quiesce — rather than legacy REST API claim blocking.

## Source Document Requirements

| ID | Source Reference | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | §2 Goals | Provide an operator-driven pause/resume control that blocks new work across all Managed Agents and Orchestrator processes while keeping queued Temporal workflows untouched. |
| DOC-REQ-002 | §3.1 Drain Mode | Support Drain mode via `worker.shutdown()` which blocks new Activity claims immediately but lets currently executing Activities finish or hit their heartbeat timeout. |
| DOC-REQ-003 | §3.2 Quiesce Mode | Support Quiesce mode via Temporal Batch Signals sent to all running Workflows. Workflows register a signal handler and block on `workflow.wait_condition()` until resumed. |
| DOC-REQ-004 | §4.1 System Pause State | Persist a singleton DB record that controls whether Mission Control UI accepts new workflow submissions via the `POST /api/workflows` boundary. This does NOT govern Temporal workers directly. |
| DOC-REQ-005 | §4.1 Mission Control API Guard | `POST /api/workflows` returns "system paused" metadata and does not trigger new Temporal Workflows while the DB singleton is paused. |
| DOC-REQ-006 | §4.1 Dashboard UX | Dashboard shows a global banner, Pause/Resume controls, and drain progress indicator using Temporal Visibility APIs (`ExecutionStatus="Running"`). |
| DOC-REQ-007 | §5.1 API Surface | Expose `GET /api/system/worker-pause` (state + drain metrics from Temporal) and `POST /api/system/worker-pause` (action, mode, reason). |
| DOC-REQ-008 | §6.1 Quiesce Workflow Impl | Workflows register `pause_signal_handler(paused: bool)`, maintain `self.is_paused`, and call `await workflow.wait_condition(lambda: not self.is_paused)` before each Activity/Agent Step. |
| DOC-REQ-009 | §6.1 Activity Checkpoints | For long-running LLM activities, inject a Heartbeat interceptor that yields checkpoint data back to Temporal so progress isn't lost. |
| DOC-REQ-010 | §8 Security | Only authenticated operators/admins can call pause endpoints. All actions audited in `system_control_events`. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Pause for Infrastructure Upgrades (Priority: P1)

An operator needs to halt all new Temporal Workflow execution before rolling out image rebuilds or schema migrations. They use the Dashboard or API to set Drain mode, then gracefully shut down worker containers.

**Why this priority**: Without a reliable pause, upgrades can corrupt workflow state or trigger unexpected retries.

**Independent Test**: Call `POST /api/system/worker-pause` with `action: "pause", mode: "drain"`, then verify that `POST /api/workflows` returns a "system paused" response and Dashboard banner flips to "Paused (Drain)". Gracefully stop workers with `docker compose stop temporal-worker-sandbox` and confirm Temporal Visibility shows 0 running workflows.

**Acceptance Scenarios**:

1. **Given** active workers processing workflows, **When** the operator pauses in Drain mode, **Then** the API guard blocks new submissions, workers gracefully shut down via `worker.shutdown()`, and inflight Activities complete naturally.
2. **Given** the system is paused, **When** Temporal Visibility reports 0 running workflows on the target Task Queues, **Then** `GET /api/system/worker-pause` reports `isDrained=true`, signalling it is safe to restart workers.
3. **Given** the operator resumes the system, **When** workers reconnect to Temporal, **Then** the Task Queue resumes processing and the Dashboard banner restores to "Running".

---

### User Story 2 — Monitor Drain Progress and Resume Safely (Priority: P2)

Operators need confidence that maintenance can proceed without disrupting workflows. They monitor running/queued counts from Temporal Visibility and an `isDrained` indicator.

**Why this priority**: Drain visibility determines when maintenance can proceed.

**Independent Test**: Pause the system, poll `GET /api/system/worker-pause` until `isDrained=true`, perform worker restarts, and verify workflows resume cleanly.

**Acceptance Scenarios**:

1. **Given** a pause is active, **When** the operator polls the pause endpoint, **Then** the response includes `queuedCount`, `runningCount`, and `isDrained` derived from Temporal Visibility queries.
2. **Given** `isDrained=true`, **When** the operator resumes and restarts workers, **Then** queued workflows are picked up by the new workers as expected.
3. **Given** multiple pause/resume cycles occur, **When** the operator reviews `system_control_events`, **Then** every action is logged with timestamp, actor, reason, and mode.

---

### User Story 3 — Quiesce Running Workflows at Safe Boundaries (Priority: P3)

During a short maintenance window, operators request Quiesce mode so running workflows pause at safe Activity boundaries using Temporal Signals, without losing long-running context.

**Why this priority**: Quiesce reduces disruption during brief maintenance without forcing workflows to restart from scratch.

**Independent Test**: Send a Quiesce pause, confirm that running workflows receive the Signal, block at `workflow.wait_condition()`, and resume execution after the system is unpaused.

**Acceptance Scenarios**:

1. **Given** the operator requests `Pause (Quiesce)`, **When** the system uses the Temporal Batch Operations API to Signal all running workflows, **Then** each workflow's `pause_signal_handler` sets `is_paused=True` and the workflow blocks before the next Activity.
2. **Given** a workflow has paused at its `wait_condition`, **When** the operator resumes the system and a resume Signal is sent, **Then** the workflow's `is_paused` clears and execution continues from the blocked point without duplicating work.
3. **Given** a long-running LLM Activity receives a quiesce Signal, **When** the Activity supports heartbeat checkpoints, **Then** checkpoint data is yielded to Temporal history before the Activity returns early, allowing the workflow to suspend safely.

### Edge Cases

- Pause requested while the system is already paused should update reason/mode only if it represents a meaningful change and must still append an audit entry.
- Resume requested when not paused should be rejected with a clear error.
- Workers that reconnect after a pause read the current state from Temporal and the API and immediately enter the correct behavior.
- Temporal Batch Signal failures should be retried; partial signal delivery must be detectable via Visibility queries.
- Network partitions during pause must default workers to safe idle behavior. Temporal's durable execution model inherently handles this for Quiesced workflows since the `wait_condition` is persisted in workflow history.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-004)**: Persist a singleton `system_worker_pause_state` DB record (id=1) storing `paused`, `mode`, `reason`, operator metadata, timestamps, and a monotonic `version`. This record controls whether the Mission Control API accepts new workflow submissions.
- **FR-002 (DOC-REQ-010)**: Insert an audit `system_control_events` row for every pause/resume request capturing action, mode, reason, actor (when available), and timestamps.
- **FR-003 (DOC-REQ-007)**: Implement `GET /api/system/worker-pause` returning pause state plus drain metrics derived from Temporal Visibility queries (`queuedCount`, `runningCount`, `isDrained`).
- **FR-004 (DOC-REQ-007)**: Implement `POST /api/system/worker-pause` that validates `action`, `mode`, and `reason`, flips the DB singleton, records audit events, and rejects illegal transitions.
- **FR-005 (DOC-REQ-005)**: Guard `POST /api/workflows` (and any other task ingestion endpoints) so that when `paused=true`, no new Temporal Workflows are started and a "system paused" response is returned.
- **FR-006 (DOC-REQ-002)**: Document and support Drain mode via Temporal's native `worker.shutdown()`. The API pause action sets the DB flag; operators send graceful shutdown signals (SIGINT/SIGTERM) to Temporal worker containers.
- **FR-007 (DOC-REQ-003, DOC-REQ-008)**: Implement Quiesce mode using Temporal Batch Signals. The API sends a `pause` Signal to all running Workflows. Each workflow registers a `pause_signal_handler` that sets an internal `is_paused` flag. Before each Activity, workflows call `await workflow.wait_condition(lambda: not self.is_paused)`.
- **FR-008 (DOC-REQ-009)**: For long-running LLM Activities, support heartbeat-based checkpointing so that progress is yielded back to Temporal history when an Activity is asked to return early during Quiesce.
- **FR-009 (DOC-REQ-006)**: Dashboard displays a global "Workers" banner with Pause/Resume controls (mode selector + reason), drain progress from Temporal Visibility, and a "Safe to upgrade" indicator when `isDrained=true`.
- **FR-010 (DOC-REQ-003)**: On resume, the API sends a `resume` Signal (via Temporal Batch Operations) to all paused workflows so they clear `is_paused` and continue execution.

### Key Entities *(include if feature involves data)*

- **SystemWorkerPauseState**: Singleton DB record; fields `paused`, `mode`, `reason`, `requested_by_user_id`, `requested_at`, `updated_at`, `version`. Guards Mission Control API submission endpoints.
- **SystemControlEvent**: Append-only audit row capturing every pause/resume action with `control`, `action`, `mode`, `reason`, `actor_user_id`, `created_at`.

### Assumptions & Dependencies

- Temporal Server is running and accessible to all worker instances.
- Temporal Visibility API (`ListWorkflowExecutions`) is available for drain progress queries.
- Temporal Batch Operations API is available for sending Signals to multiple workflows.
- Workers use Temporal's `worker.shutdown()` for graceful exit; no custom drain polling is needed.
- Quiesce workflow signal handler pattern will be added to `MoonMind.Run` and `MoonMind.AgentRun` workflow definitions.
- `AUTH_PROVIDER=disabled` allows open access to pause endpoints for local development.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Within 5 seconds of submitting a pause request, `POST /api/workflows` returns "system paused" responses, ensuring no new workflows start during maintenance windows.
- **SC-002**: Drain mode correctly reports `isDrained=true` when Temporal Visibility shows 0 running workflows on the target Task Queues.
- **SC-003**: In Quiesce mode, 95% of running workflows receive the Batch Signal and reach their `wait_condition` within 60 seconds.
- **SC-004**: 100% of pause/resume actions produce corresponding entries in `system_control_events` with populated reason, mode, actor, and timestamps.
- **SC-005**: Dashboard drain panel reaches and displays `isDrained=true` before any documented upgrade restarts proceed.
