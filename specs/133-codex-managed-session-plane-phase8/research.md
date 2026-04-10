# Research: Codex Managed Session Plane Phase 8

## Decisions

### 1. Publish continuity artifacts from the session supervisor

- Reuse `ManagedSessionSupervisor` for `session.summary` and `session.step_checkpoint`.
- Rationale: it already owns durable spool-backed publication and is the closest boundary to session observability state.

### 2. Publish reset artifacts from the session controller clear boundary

- Persist `session.control_event` and `session.reset_boundary` during `clear_session`.
- Rationale: reset is a control-plane action and should be durably recorded at the same boundary that applies the remote clear.

### 3. Use the Temporal artifact activity for step-scoped `input.*` / `output.*`

- Extend `agent_runtime.publish_artifacts` to publish managed-session `input.instructions`, optional `input.skill_snapshot`, `output.summary`, and `output.agent_result`.
- Rationale: the activity already has Temporal execution context and the artifact service, so step-scoped publication stays outside workflow code.

### 4. Keep the durable session record as the latest continuity source

- Store the latest summary/checkpoint/control/reset refs directly on `CodexManagedSessionRecord`.
- Rationale: later session summary/projection reads should not need a live container or ephemeral controller cache.
