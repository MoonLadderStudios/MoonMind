# Feature Specification: Live Task Handoff

**Feature Branch**: `024-live-task-handoff`  
**Created**: 2026-02-18  
**Status**: Draft  
**Input**: User description: "Implement the live task handoff system as described in docs/LiveTaskHandoff.md"

## Source Document Requirements

| ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/LiveTaskHandoff.md` sections "Goals", "Concept Overview", "Live Session State Machine" | Task runs must support live terminal observation with a deterministic lifecycle (`DISABLED -> STARTING -> READY -> REVOKED/ENDED/ERROR`). |
| DOC-REQ-002 | `docs/LiveTaskHandoff.md` section "Persistence Model" (`task_run_live_sessions`) | System must persist per-run live-session state including provider/status, lifecycle timestamps, attach metadata, heartbeat, TTL, and error details. |
| DOC-REQ-003 | `docs/LiveTaskHandoff.md` section "Persistence Model" (`task_run_control_events`) | System must persist operator control/audit events with actor identity, action, timestamp, and structured metadata. |
| DOC-REQ-004 | `docs/LiveTaskHandoff.md` section "API Surface" (Session Lifecycle) | API must expose live-session lifecycle endpoints for create/get/grant-write/revoke flows. |
| DOC-REQ-005 | `docs/LiveTaskHandoff.md` section "API Surface" (Control Actions) | API must expose task control actions for `pause`, `resume`, and `takeover`. |
| DOC-REQ-006 | `docs/LiveTaskHandoff.md` section "API Surface" (Operator Messages) | API must expose operator message submission and route messages into run context/inbox flow. |
| DOC-REQ-007 | `docs/LiveTaskHandoff.md` sections "Worker Lifecycle" and "Session Bootstrap" | Worker must bootstrap a per-run tmate session, configure panes/session options, and report ready/error state plus endpoints. |
| DOC-REQ-008 | `docs/LiveTaskHandoff.md` sections "Heartbeat & TTL" and "Teardown" | Worker must heartbeat live-session state and perform teardown reporting on completion/revoke paths. |
| DOC-REQ-009 | `docs/LiveTaskHandoff.md` section "Pause/Resume and Unstick Flow" | Worker runtime must honor pause/resume control at safe checkpoints and support explicit operator takeover flow. |
| DOC-REQ-010 | `docs/LiveTaskHandoff.md` section "Operator Messages" | Operator messages must be visible to live observers and available for agent-side reaction during execution. |
| DOC-REQ-011 | `docs/LiveTaskHandoff.md` section "Security Model" | Security defaults must be RO-first, with time-bound RW grants, revocation support, and auditable actions. |
| DOC-REQ-012 | `docs/LiveTaskHandoff.md` section "Configuration" | Runtime/config must support documented live-session environment variables (provider, TTL, RW grant TTL, web toggle, relay host, concurrency limit). |
| DOC-REQ-013 | `docs/LiveTaskHandoff.md` sections "Failure Modes" and "Security Model" | If tmate cannot start/connect, run must continue headless while live-session state is marked `ERROR` for diagnostics. |
| DOC-REQ-014 | `docs/LiveTaskHandoff.md` sections "Architecture" and "Minimal Implementation Plan" | Dashboard must surface a live-session UI for status, attach info, pause/resume, RW grant/revoke, and operator message actions. |
| DOC-REQ-015 | `docs/LiveTaskHandoff.md` section "Worker Lifecycle > Image Requirements" | Runtime image must include required terminal/transport packages (`tmate`, `tmux`, `openssh-client`, `ca-certificates`). |
| DOC-REQ-016 | `docs/LiveTaskHandoff.md` sections "Goals" and "Minimal Implementation Plan" | Delivery must include validation coverage proving live-session lifecycle/control flows work and are traceable in queue/task behavior. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Observe Active Task Sessions Live (Priority: P1)

An operator can enable and view a live session for an active queue task run and obtain read-only attach information without interrupting execution.

**Why this priority**: Live observation is the primary value proposition and baseline for all further intervention flows.

**Independent Test**: Start a running task, enable live session, and verify API/dashboard show `STARTING -> READY` lifecycle with RO attach details and heartbeat updates.

**Acceptance Scenarios**:

1. **Given** a running task run with live-session enablement requested, **When** the worker starts tmate successfully, **Then** the session status becomes `ready` and RO attach details are visible.
2. **Given** a task run with an active live session, **When** the worker heartbeats, **Then** session heartbeat metadata is refreshed without changing task execution semantics.

---

### User Story 2 - Perform Controlled Operator Intervention (Priority: P2)

An operator can pause/resume execution, request temporary RW access, and send operator messages with auditability.

**Why this priority**: Intervention capabilities are necessary to unblock runs safely once live observation is available.

**Independent Test**: Trigger pause, send operator message, grant RW with TTL, then resume; verify queue control state, audit events, and lifecycle visibility.

**Acceptance Scenarios**:

1. **Given** a running task run, **When** operator applies `pause`, **Then** worker enters paused checkpoint behavior and task remains observable.
2. **Given** a ready live session, **When** operator grants write access, **Then** RW attach details are revealed temporarily with a bounded expiration time and audit record.
3. **Given** an operator message is submitted, **When** message is accepted, **Then** it is persisted and exposed to run observers/agent context.

---

### User Story 3 - Maintain Secure, Reliable Fallback Behavior (Priority: P3)

The system enforces secure defaults and handles live-session failures without breaking task completion.

**Why this priority**: Operational safety and graceful degradation are mandatory for production reliability.

**Independent Test**: Run with missing or failing tmate transport and verify task continues headless while live session transitions to error/revoked/ended appropriately.

**Acceptance Scenarios**:

1. **Given** live-session startup cannot provision tmate, **When** prepare stage runs, **Then** live session is marked `error` and task execution continues.
2. **Given** live-session revoke is requested, **When** worker processes teardown, **Then** session is terminated and reflected as terminal lifecycle state with audit trail.

### Edge Cases

- Live-session enable is requested repeatedly for the same run; creation must be idempotent and status must remain coherent.
- RW grant is requested while session is not `ready`; system must reject with clear validation feedback.
- Pause and cancel signals may overlap; cancellation must still terminate run flow correctly after pause checkpoints.
- Worker restarts during a live session; stale session state must resolve to a terminal/error state without orphaned control assumptions.
- Web attach links must follow the allow-web policy and not leak RW details by default.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose task-run live-session lifecycle API endpoints for create, fetch, grant-write, and revoke operations. (DOC-REQ-004)
- **FR-002**: System MUST persist task-run live-session records with provider, status, lifecycle timestamps, attach metadata, heartbeat, TTL, and error diagnostics. (DOC-REQ-001, DOC-REQ-002)
- **FR-003**: System MUST persist task-run control/audit events for operator and worker control actions with actor and metadata context. (DOC-REQ-003)
- **FR-004**: Worker MUST provision per-run tmate-backed sessions and report `starting`, `ready`, and `error` transitions with attach metadata. (DOC-REQ-001, DOC-REQ-007)
- **FR-005**: System MUST keep RO attach information directly retrievable while protecting RW attach information behind explicit grant/reveal semantics. (DOC-REQ-007, DOC-REQ-011)
- **FR-006**: System MUST expose control actions (`pause`, `resume`, `takeover`) and worker runtime MUST honor pause checkpoints for safe intervention. (DOC-REQ-005, DOC-REQ-009)
- **FR-007**: System MUST support operator message submission and propagate those messages into run event/context channels. (DOC-REQ-006, DOC-REQ-010)
- **FR-008**: Worker MUST heartbeat live-session state during active runs and report terminal teardown states on completion or revocation. (DOC-REQ-008)
- **FR-009**: Dashboard MUST provide live-session UX for status, attach visibility, control actions, RW grant/revoke, and operator messaging. (DOC-REQ-014)
- **FR-010**: System MUST enforce RO-first security defaults with time-bound RW grants and revocation behavior reflected in audit history. (DOC-REQ-011)
- **FR-011**: Runtime/config surfaces MUST support documented live-session environment settings for enablement, provider, TTLs, web exposure, relay host, and concurrency controls. (DOC-REQ-012)
- **FR-012**: If live-session transport setup fails, system MUST mark live-session state as `error` while allowing the task run to proceed headless. (DOC-REQ-013)
- **FR-013**: Worker/runtime image and deployment configuration MUST include required live-session transport dependencies. (DOC-REQ-015)
- **FR-014**: Automated validation MUST cover API/service/worker/dashboard/config behavior for live-session lifecycle, control, and failure paths. (DOC-REQ-016)

### Key Entities *(include if feature involves data)*

- **TaskRunLiveSession**: Per-run live-session state record containing status lifecycle, provider, attach metadata, heartbeat, TTL, and diagnostics.
- **TaskRunControlEvent**: Append-only audit event capturing control and operator actions with actor identity and structured metadata.
- **Live Control Payload**: Run-scoped control flags consumed by worker heartbeat/checkpoints to coordinate pause/resume/takeover behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least one end-to-end task run demonstrates `starting -> ready -> ended` live-session lifecycle visibility without manual DB inspection.
- **SC-002**: 100% of RW grant actions expose expiration metadata and corresponding control/audit event records.
- **SC-003**: Pause/resume/takeover API actions are observable in queue/task telemetry and honored by worker control checkpoints in automated tests.
- **SC-004**: When tmate bootstrap fails in controlled test conditions, task execution still reaches terminal run status while live-session status is reported as `error`.
- **SC-005**: Unit test suite (`./tools/test_unit.sh`) passes with live-session API/service/worker/dashboard/config coverage included.
