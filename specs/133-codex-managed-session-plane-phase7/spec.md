# Feature Specification: codex-managed-session-plane-phase7

**Feature Branch**: `133-codex-managed-session-plane-phase7`
**Created**: 2026-04-07
**Status**: Draft
**Input**: User description: "Implement Phase 7 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish Durable Reset Artifacts On Clear (Priority: P1)

When an operator or workflow clears a managed Codex session, MoonMind must materialize the reset as durable artifacts instead of treating it as an in-container implementation detail.

**Why this priority**: Phase 7 exists to make reset/clear explicit and durable. Without artifact publication, the epoch boundary is invisible after the container is gone.

**Independent Test**: Clear a durable managed session through the controller and verify MoonMind writes one `session.control_event` artifact and one `session.reset_boundary` artifact, updates the durable session record with their refs, and advances the epoch.

**Acceptance Scenarios**:

1. **Given** a persisted managed session record and a successful remote `clear_session` response, **When** the controller records the reset, **Then** the durable session record stores the new `session_epoch`, `thread_id`, `latest_control_event_ref`, and `latest_checkpoint_ref`.
2. **Given** a clear/reset action is applied, **When** the reset artifacts are published, **Then** MoonMind writes a durable `session.control_event` artifact describing the control action and a durable `session.reset_boundary` artifact describing the new epoch boundary.
3. **Given** a second clear/reset is applied later, **When** MoonMind publishes the next artifacts, **Then** it writes new artifact paths for the new epoch instead of overwriting the previous epoch's reset evidence.

---

### User Story 2 - Reuse Reset Refs Through Session Summary APIs (Priority: P1)

The existing managed-session summary/publication surfaces must expose the latest reset artifacts from durable record state so UI and API callers can build continuity views without reading container-private state.

**Why this priority**: The reset only becomes operator-visible if the same continuity surfaces used elsewhere return the refs.

**Independent Test**: After a clear/reset, fetch session summary and publish session artifacts and verify both responses return the persisted control-event and reset-boundary refs from the durable session record.

**Acceptance Scenarios**:

1. **Given** a durable session record containing latest reset refs, **When** `agent_runtime.fetch_session_summary` is called, **Then** the response returns `latestControlEventRef` and `latestCheckpointRef` from the durable record.
2. **Given** a durable session record containing latest reset refs, **When** `agent_runtime.publish_session_artifacts` is called, **Then** the response includes those refs and does not ask the container to synthesize continuity history.
3. **Given** the reset artifacts were written by a prior clear/reset, **When** summary/publication is requested after a worker restart, **Then** the refs are still served from the durable session record.

---

### User Story 3 - Preserve Container-First Reset Semantics (Priority: P2)

The new reset behavior must remain layered on top of the remote session container control path rather than reintroducing worker-local Codex ownership.

**Why this priority**: Phase 7 must strengthen durability without violating the earlier session-container architecture.

**Independent Test**: Invoke `agent_runtime.clear_session` and verify MoonMind still sends the remote clear action first, then records the resulting epoch boundary durably without routing through `ManagedRuntimeLauncher` or a local Codex subprocess path.

**Acceptance Scenarios**:

1. **Given** a managed session clear request, **When** the controller handles it, **Then** it still issues the control action against the remote session container and only uses the worker to publish durable artifacts and update metadata.
2. **Given** the `MoonMind.AgentSession` workflow receives the clear/reset control action, **When** it updates its snapshot, **Then** the workflow still preserves `session_id`, increments `session_epoch`, rotates `thread_id`, and clears `active_turn_id`.

### Edge Cases

- A clear/reset is requested for a session that has never published any prior continuity artifacts.
- A clear/reset reason is omitted and MoonMind must still publish a valid control artifact.
- A session is cleared multiple times in one task and each epoch boundary must remain distinguishable by artifact name and metadata.
- The controller publishes reset artifacts after the remote container reports success but before any later step runs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `agent_runtime.clear_session` MUST remain a remote session-container control action and MUST NOT execute Codex locally in the worker process.
- **FR-002**: A successful managed-session clear/reset MUST write one durable `session.control_event` artifact describing the clear action.
- **FR-003**: A successful managed-session clear/reset MUST write one durable `session.reset_boundary` artifact describing the new continuity interval.
- **FR-004**: The durable managed session record MUST persist the latest clear/reset artifact refs using `latest_control_event_ref` and `latest_checkpoint_ref`.
- **FR-005**: The durable managed session record MUST persist the new `session_epoch`, `thread_id`, and `updated_at` produced by `clear_session`.
- **FR-006**: Reset artifacts MUST be written with epoch-specific names or metadata so successive resets do not overwrite prior reset evidence.
- **FR-007**: `agent_runtime.fetch_session_summary` and `agent_runtime.publish_session_artifacts` MUST return reset-boundary refs from durable session state after a clear/reset.
- **FR-008**: The Phase 7 implementation MUST preserve the existing `MoonMind.AgentSession` and `mm.activity.agent_runtime` request/response shapes.

### Key Entities

- **Session Control Event Artifact**: Durable JSON artifact describing one explicit control action such as `clear_session`, including session identity, reason, and epoch transition metadata.
- **Session Reset Boundary Artifact**: Durable JSON artifact describing the new logical continuity interval after a reset, including previous and new epoch/thread values.
- **Durable Managed Session Record**: The Phase 6 JSON-backed session record that now carries the latest reset-boundary refs in addition to runtime observability refs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Controller/service tests verify `clear_session` writes reset artifacts and updates durable refs plus epoch metadata.
- **SC-002**: Supervisor/store tests verify repeated clears preserve distinct reset artifact refs instead of overwriting prior epoch evidence.
- **SC-003**: Summary/publication tests verify reset refs are served from the durable session record after clear/reset.
- **SC-004**: Focused activity/workflow boundary tests confirm the existing clear/reset request/response contracts still hold while the epoch boundary remains explicit.
