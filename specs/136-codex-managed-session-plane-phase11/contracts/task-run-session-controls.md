# Contract: Task-Run Session Controls

## Existing Read Contract

`GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`

Returns the current `ArtifactSessionProjectionModel` for one task-scoped managed session.

## New Control Contract

`POST /api/task-runs/{task_run_id}/artifact-sessions/{session_id}/control`

### Request

```json
{
  "action": "send_follow_up",
  "message": "Continue using the existing task-scoped session.",
  "reason": "Operator clarification"
}
```

or

```json
{
  "action": "clear_session",
  "reason": "Reset stale context before the next step."
}
```

### Validation Rules

- `action` must be one of `send_follow_up` or `clear_session`
- `message` is required for `send_follow_up`
- blank `message` or `reason` values are rejected
- the referenced session must belong to the requested `task_run_id`
- the caller must own the task run unless they are a superuser

### Response

```json
{
  "action": "send_follow_up",
  "projection": {
    "task_run_id": "wf-task-1",
    "session_id": "sess:wf-task-1:codex_cli",
    "session_epoch": 2,
    "grouped_artifacts": [],
    "latest_summary_ref": null,
    "latest_checkpoint_ref": null,
    "latest_control_event_ref": null,
    "latest_reset_boundary_ref": null
  }
}
```

### Behavioral Rules

- The server targets the task-scoped `MoonMind.AgentSession` workflow for the session identified by `{task_run_id, session_id}`.
- `send_follow_up` must execute through the managed session activity surface and then return the refreshed projection.
- `clear_session` must execute through the managed session clear activity surface and then return the refreshed projection.
- The control route must not call a worker-local Codex CLI path directly.
