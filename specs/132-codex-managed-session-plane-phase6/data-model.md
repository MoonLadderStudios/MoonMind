# Data Model: Codex Managed Session Plane Phase 6

## Entity: CodexManagedSessionRecord

- Purpose: durable session-level supervision state for one task-scoped Codex managed session.
- Fields:
  - `session_id`
  - `session_epoch`
  - `task_run_id`
  - `container_id`
  - `thread_id`
  - `runtime_id`
  - `image_ref`
  - `control_url`
  - `status`
  - `workspace_path`
  - `session_workspace_path`
  - `artifact_spool_path`
  - `stdout_artifact_ref`
  - `stderr_artifact_ref`
  - `diagnostics_ref`
  - `last_log_offset`
  - `last_log_at`
  - `error_message`

## Entity: CodexManagedSessionStatus

- Allowed values:
  - `launching`
  - `ready`
  - `busy`
  - `terminating`
  - `terminated`
  - `degraded`
  - `failed`

## Relationships

- One `CodexManagedSessionRecord` belongs to one `task_run_id`.
- One `CodexManagedSessionRecord` owns zero or more artifact refs over time, with the latest refs persisted directly on the record.
- One `CodexManagedSessionRecord` is supervised by at most one active `CodexManagedSessionSupervisor` instance per worker process.
