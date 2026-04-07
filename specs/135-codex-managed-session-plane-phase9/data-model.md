# Data Model: Codex Managed Session Plane Phase 9

## ArtifactSessionProjectionModel

Server-side read model returned by `GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`.

| Field | Type | Notes |
| --- | --- | --- |
| `task_run_id` | string | Root task-run identity supplied in the route and verified against the durable session record |
| `session_id` | string | Task-scoped managed-session identity |
| `session_epoch` | integer | Latest durable continuity epoch for the managed session |
| `grouped_artifacts` | list[`ArtifactSessionGroupModel`] | Server-defined artifact groups for continuity panel rendering |
| `latest_summary_ref` | `ArtifactRefModel \| null` | Latest durable `session.summary` artifact ref when resolvable |
| `latest_checkpoint_ref` | `ArtifactRefModel \| null` | Latest durable `session.step_checkpoint` artifact ref when resolvable |
| `latest_control_event_ref` | `ArtifactRefModel \| null` | Latest durable `session.control_event` artifact ref when resolvable |
| `latest_reset_boundary_ref` | `ArtifactRefModel \| null` | Optional latest durable `session.reset_boundary` artifact ref |

## ArtifactSessionGroupModel

Stable group emitted by the projection API.

| Field | Type | Notes |
| --- | --- | --- |
| `group_key` | string | Machine-stable grouping key such as `runtime` or `continuity` |
| `title` | string | Human-readable group label |
| `artifacts` | list[`ArtifactMetadataModel`] | Artifact metadata entries already filtered and ordered by the server |

## Projection Source Inputs

### Durable managed-session record

`CodexManagedSessionRecord` supplies:

- `task_run_id`
- `session_id`
- `session_epoch`
- `stdout_artifact_ref`
- `stderr_artifact_ref`
- `diagnostics_ref`
- `latest_summary_ref`
- `latest_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`

### Artifact metadata

Each stored ref is resolved into:

- `ArtifactMetadataModel`
- execution `links[]`
- safe preview/default-read fields
- artifact-ref payload used by the latest-ref fields

## Grouping Rules

- `runtime` group:
  - stdout
  - stderr
  - diagnostics
- `continuity` group:
  - session summary
  - step checkpoint
- `control` group:
  - control event
  - reset boundary

Groups omit missing or unreadable artifacts rather than returning placeholders.
