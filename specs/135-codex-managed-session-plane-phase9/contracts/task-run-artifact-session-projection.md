# Task-Run Artifact Session Projection Contract

## Endpoint

`GET /api/task-runs/{task_run_id}/artifact-sessions/{session_id}`

## Success Response

`200 OK`

```json
{
  "task_run_id": "wf-task-1",
  "session_id": "sess:wf-task-1:codex_cli",
  "session_epoch": 2,
  "grouped_artifacts": [
    {
      "group_key": "runtime",
      "title": "Runtime",
      "artifacts": ["ArtifactMetadataModel", "..."]
    },
    {
      "group_key": "continuity",
      "title": "Continuity",
      "artifacts": ["ArtifactMetadataModel", "..."]
    },
    {
      "group_key": "control",
      "title": "Control",
      "artifacts": ["ArtifactMetadataModel", "..."]
    }
  ],
  "latest_summary_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_summary",
    "sha256": null,
    "size_bytes": 512,
    "content_type": "application/json",
    "encryption": "none"
  },
  "latest_checkpoint_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_checkpoint",
    "sha256": null,
    "size_bytes": 640,
    "content_type": "application/json",
    "encryption": "none"
  },
  "latest_control_event_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_control",
    "sha256": null,
    "size_bytes": 256,
    "content_type": "application/json",
    "encryption": "none"
  },
  "latest_reset_boundary_ref": {
    "artifact_ref_v": 1,
    "artifact_id": "art_reset",
    "sha256": null,
    "size_bytes": 256,
    "content_type": "application/json",
    "encryption": "none"
  }
}
```

## Error Responses

### Missing or mismatched session

`404 Not Found`

```json
{
  "detail": {
    "code": "session_projection_not_found",
    "message": "Managed session projection was not found for the requested task run."
  }
}
```

### Forbidden

`403 Forbidden`

The route follows the existing task-run ownership rules and must not leak session metadata to non-owners.

## Contract Rules

- The response is a server-side read model over persisted artifacts and the durable managed-session record.
- The response must not require a live session container, live session-controller query, or container-local history.
- Missing artifact refs may be omitted from both the latest-ref fields and the grouped-artifact lists.
- Every returned artifact must still carry its normal execution `links[]`.
