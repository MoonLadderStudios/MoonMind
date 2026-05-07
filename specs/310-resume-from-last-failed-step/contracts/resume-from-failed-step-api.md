# Contract: Resume from Failed Step

## POST `/api/executions/{workflow_id}/resume-from-failed-step`

Creates a linked follow-up execution that resumes a failed `MoonMind.Run` from the last failed step.

### Request

```json
{
  "idempotencyKey": "resume-mm-source-run-1",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source-run/checkpoint-v1",
  "operatorMetadata": {
    "requestedFrom": "task-detail"
  }
}
```

Rules:
- `workflow_id` identifies the source failed execution.
- `idempotencyKey` is required and bounded.
- `resumeCheckpointRef` may be supplied by the caller only when it matches trusted source eligibility data; otherwise the backend resolves the eligible checkpoint.
- Request MUST NOT include edited task instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, model settings, or other task payload overrides.
- Source `runId` must be resolved from the current source execution and pinned before validation.

### Success Response `201 Created`

```json
{
  "accepted": true,
  "applied": "created_resumed_execution",
  "source": {
    "workflowId": "mm:source",
    "runId": "source-run-id"
  },
  "execution": {
    "workflowId": "mm:resumed",
    "runId": "resumed-run-id",
    "detailHref": "/tasks/mm:resumed"
  },
  "relationship": "Resumed from failed step",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source-run/checkpoint-v1"
}
```

### Error Responses

`400 Bad Request`
```json
{
  "detail": {
    "code": "resume_payload_not_allowed",
    "message": "Resume does not accept edited task payload fields. Use Edit task for changes.",
    "fields": ["task", "runtime"]
  }
}
```

`404 Not Found`
```json
{
  "detail": {
    "code": "execution_not_found",
    "message": "Source execution was not found or is not visible."
  }
}
```

`409 Conflict`
```json
{
  "detail": {
    "code": "resume_not_available",
    "message": "Failed-step Resume is not available for this execution.",
    "reason": "resume_checkpoint_missing"
  }
}
```

`422 Unprocessable Content`
```json
{
  "detail": {
    "code": "resume_checkpoint_invalid",
    "message": "Resume checkpoint validation failed before failed-step execution.",
    "reason": "plan_digest_mismatch"
  }
}
```

## GET `/api/executions/{workflow_id}` additions

Execution detail actions include failed-step Resume separately from lifecycle Resume.

```json
{
  "actions": {
    "canResume": false,
    "canResumeFromFailedStep": true,
    "disabledReasons": {}
  },
  "resume": {
    "available": true,
    "checkpointRef": "artifact://resume-checkpoints/source-run/checkpoint-v1",
    "failedStepId": "tpl:jira-orchestrate:1.0.0:09:implement",
    "sourceRunId": "source-run-id"
  },
  "relatedRuns": [
    {
      "workflowId": "mm:resumed",
      "runId": "resumed-run-id",
      "relationship": "Resumed from failed step",
      "status": "executing",
      "href": "/tasks/mm:resumed"
    }
  ]
}
```

Rules:
- `canResume` remains lifecycle Resume.
- `canResumeFromFailedStep` represents failed-step Resume only.
- Disabled reasons use stable machine-readable strings such as `state_not_eligible`, `original_task_input_snapshot_missing`, `resume_checkpoint_missing`, `source_run_missing`, `failed_step_missing`, `plan_mismatch`, `workspace_restore_unavailable`, or `unauthorized_checkpoint`.

## GET `/api/executions/{workflow_id}/steps` preserved-step extension

Step rows for resumed executions may include preserved provenance.

```json
{
  "workflowId": "mm:resumed",
  "runId": "resumed-run-id",
  "steps": [
    {
      "logicalStepId": "prepare",
      "status": "succeeded",
      "summary": "Preserved from source run.",
      "preservedFrom": {
        "workflowId": "mm:source",
        "runId": "source-run-id",
        "attempt": 1
      },
      "artifacts": {
        "outputSummary": "artifact://summary"
      }
    }
  ]
}
```

Rules:
- Preserved rows must carry source provenance.
- UI must not display preserved rows as newly executed by the resumed run.
- The first newly executed row is the failed step from the source run.
