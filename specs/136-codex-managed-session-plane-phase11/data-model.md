# Data Model: codex-managed-session-plane-phase11

## TaskRunSessionControlRequest

- `action`: one of `send_follow_up`, `clear_session`
- `message`: required non-blank string for `send_follow_up`
- `reason`: optional non-blank operator note

## TaskRunSessionControlResult

- `action`: echoed requested control action
- `projection`: refreshed `ArtifactSessionProjectionModel`

## Session Continuity View

- `task_run_id`
- `session_id`
- `session_epoch`
- `grouped_artifacts`
  - runtime artifacts
  - continuity artifacts
  - control artifacts
- `latest_summary_ref`
- `latest_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`
- `control_state`
  - pending action
  - error text
  - follow-up draft

## Behavioral Notes

- The projection remains the durable read model.
- Follow-up and clear/reset mutate the managed session through workflow/activity boundaries and then refresh the projection.
- Cancel remains a task-level action and does not introduce a separate session-level termination UX in this phase.
