# Feature Specification: codex-managed-session-plane-phase8

**Feature Branch**: `133-codex-managed-session-plane-phase8`
**Created**: 2026-04-07
**Status**: Draft
**Input**: User description: "Implement Phase 8 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Persist step-scoped managed-session artifacts (Priority: P1)

Operators need each Codex-managed step to leave behind a durable, step-scoped artifact record so the step can be understood after the session container is gone.

**Why this priority**: Phase 8 exists to make artifact-backed presentation authoritative rather than depending on container continuity.

**Independent Test**: Execute one Codex managed-session step and verify the persisted result and session publication include step-scoped output, runtime, and continuity artifact refs.

**Acceptance Scenarios**:

1. **Given** a managed Codex step completes through the session adapter, **When** MoonMind publishes the step result, **Then** it persists `output.summary` and `output.agent_result` artifacts and preserves runtime/session artifact refs in the published result.
2. **Given** a managed Codex session publishes session artifacts for a completed step, **When** publication completes, **Then** the durable session record stores `session.summary` and `session.step_checkpoint` refs alongside stdout, stderr, and diagnostics refs.
3. **Given** the step request includes an instruction ref and an optional resolved skill snapshot ref, **When** artifact publication runs, **Then** MoonMind persists step-scoped `input.instructions` and `input.skill_snapshot` reference artifacts when those inputs are present.

---

### User Story 2 - Make reset boundaries durable and visible (Priority: P1)

Operators need clear/reset actions to leave behind explicit durable artifacts so a session epoch change is visible without inspecting runtime-private state.

**Why this priority**: Phase 8 depends on reset artifacts being first-class so later projections and UI surfaces can expose epoch boundaries.

**Independent Test**: Clear a persisted managed session and verify the durable session record advances epoch and stores control/reset-boundary refs that survive after the container stops.

**Acceptance Scenarios**:

1. **Given** `agent_runtime.clear_session` succeeds for a durable managed session record, **When** MoonMind updates the record, **Then** it persists a `session.control_event` artifact describing the reset request and outcome.
2. **Given** a clear/reset creates a new epoch, **When** MoonMind finalizes the reset bookkeeping, **Then** it persists a `session.reset_boundary` artifact describing the old epoch, new epoch, and new logical thread id.
3. **Given** a later session summary or publication is requested, **When** MoonMind serves that response, **Then** the latest control/reset refs are returned from durable record state instead of being inferred from container-local history.

---

### User Story 3 - Preserve continuity and result metadata across worker boundaries (Priority: P2)

The managed-session path needs a compact durable record of the latest continuity refs so later projections and workflow results can rebuild the session story without replaying the container.

**Why this priority**: Phase 8 is the artifact discipline slice that feeds later continuity projections and session UI work.

**Independent Test**: Publish session artifacts, restart the worker-facing controller in a test seam, and verify continuity refs are still served from the durable record without requiring a live container query.

**Acceptance Scenarios**:

1. **Given** a durable session record already contains session summary/checkpoint/control/reset refs, **When** `fetch_session_summary` is called, **Then** MoonMind returns those refs from the durable record.
2. **Given** `publish_session_artifacts` is called after prior publication, **When** no new container interaction is needed, **Then** MoonMind returns the durable artifact refs without clearing prior continuity metadata.
3. **Given** `agent_runtime.publish_artifacts` runs for a managed Codex session result, **When** it publishes the final step envelope, **Then** the returned result metadata includes the durable refs needed to rebuild the step from artifacts.

### Edge Cases

- A managed step produces no primary output refs but still needs `output.summary`, `output.agent_result`, runtime logs, diagnostics, and continuity artifacts.
- A reset happens before any prior session summary/checkpoint artifacts were published.
- `resolvedSkillsetRef` is absent; MoonMind must skip `input.skill_snapshot` without fabricating a placeholder artifact.
- Session stdout or stderr spool files are empty when summary/checkpoint publication runs.
- Repeated publication for the same session should update latest refs without losing previously published runtime refs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every Codex-managed session step MUST persist a durable `output.summary` artifact.
- **FR-002**: Every Codex-managed session step MUST persist a durable `output.agent_result` artifact containing the structured run-result envelope.
- **FR-003**: When an instruction ref is present, the managed-session publish path MUST persist a step-scoped `input.instructions` reference artifact for the step.
- **FR-004**: When a resolved skill snapshot ref is present, the managed-session publish path MUST persist a step-scoped `input.skill_snapshot` reference artifact for the step.
- **FR-005**: Managed-session publication MUST persist or surface durable refs for `runtime.stdout`, `runtime.stderr`, and `runtime.diagnostics`.
- **FR-006**: Managed-session publication MUST persist `session.summary` and `session.step_checkpoint` artifacts and store their latest refs on the durable session record.
- **FR-007**: `agent_runtime.clear_session` MUST persist `session.control_event` and `session.reset_boundary` artifacts and store their latest refs on the durable session record.
- **FR-008**: `fetch_session_summary` and `publish_session_artifacts` MUST source continuity refs from the durable session record, including the latest control/reset refs, rather than from container-local caches.
- **FR-009**: The Phase 8 implementation MUST preserve the container-first session-control model and MUST NOT move the Codex execution loop back into the main worker process.

### Key Entities

- **Codex Managed Session Record**: Durable session-level record holding the latest runtime and continuity artifact refs for one task-scoped Codex session.
- **Managed Session Step Publication**: The artifact-backed envelope for one Codex-managed step, including input reference artifacts, result artifacts, runtime artifacts, and continuity refs.
- **Reset Boundary Artifact**: Durable artifact describing a session clear/reset epoch transition.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests verify `output.summary` and `output.agent_result` artifacts are created for managed Codex session results.
- **SC-002**: Tests verify session publication persists `session.summary` and `session.step_checkpoint` refs in the durable session record.
- **SC-003**: Tests verify clear/reset persists `session.control_event` and `session.reset_boundary` refs and advances durable epoch state.
- **SC-004**: Tests verify later summary/publication calls return continuity refs from durable record state without requiring container-local state.
