# Data Model: Codex Managed Session Plane Phase 8

## Entity: CodexManagedSessionRecord

- Purpose: durable session-level artifact and continuity state for one task-scoped Codex managed session.
- Fields:
  - existing Phase 6 fields, including runtime observability refs
  - `latest_summary_ref`
  - `latest_checkpoint_ref`
  - `latest_control_event_ref`
  - `latest_reset_boundary_ref`

## Entity: Managed Session Step Publication

- Purpose: the step-scoped artifact-backed result envelope for one managed Codex session step.
- Fields:
  - `instruction_ref`
  - `resolved_skillset_ref` when present
  - `output.summary` artifact ref
  - `output.agent_result` artifact ref
  - runtime stdout/stderr/diagnostics refs
  - continuity refs returned from session publication

## Entity: Reset Boundary Artifact

- Purpose: durable epoch-boundary record emitted when `clear_session` succeeds.
- Fields:
  - `session_id`
  - `container_id`
  - `old_session_epoch`
  - `new_session_epoch`
  - `old_thread_id`
  - `new_thread_id`
  - `reason`
  - `cleared_at`

## Relationships

- One `CodexManagedSessionRecord` belongs to one `task_run_id`.
- One managed session can emit many step publications over time, but only the latest continuity refs live directly on the durable session record.
- One reset emits exactly one `session.control_event` artifact and one `session.reset_boundary` artifact for the new epoch boundary.
