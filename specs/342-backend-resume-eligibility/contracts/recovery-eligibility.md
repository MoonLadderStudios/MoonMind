# Contract: Backend-Computed Recovery Eligibility

Traceability: MM-643, `spec.md` FR-001 through FR-010.

## Execution Detail Contract

`GET /api/executions/{workflow_id}` exposes backend-computed recovery actions.

Example response fragment:

```json
{
  "actions": {
    "canEditForRerun": true,
    "canRerun": true,
    "canResumeFromFailedStep": true,
    "disabledReasons": {}
  },
  "resume": {
    "available": true,
    "checkpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    "failedStepId": "implement",
    "sourceRunId": "run-source",
    "disabledReason": null
  }
}
```

Rules:
- `canResumeFromFailedStep=true` only when backend evidence validation has passed.
- `canResumeFromFailedStep=false` includes a bounded disabled reason when the user can act on the explanation.
- Mission Control must not infer Resume availability from status, labels, or rerun availability.
- Edit task, Rerun, and Resume are independent actions.

Bounded disabled reasons include:
- `state_not_eligible`
- `unsupported_workflow_type`
- `temporal_task_editing_disabled`
- `original_task_input_snapshot_missing`
- `resume_checkpoint_missing`
- `failed_step_identity_missing`
- `completed_step_refs_missing`
- `workspace_checkpoint_missing`
- `plan_identity_missing`
- `checkpoint_unauthorized`
- `checkpoint_corrupted`
- `checkpoint_inconsistent`
- `stale_resume_evidence`

## Resume Submission Contract

`POST /api/executions/{workflow_id}/resume-from-failed-step`

Allowed request body:

```json
{
  "idempotencyKey": "resume-mm-643-1",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
  "operatorMetadata": {
    "requestedFrom": "task-detail"
  }
}
```

Forbidden categories:
- task instructions or steps
- attachments or input attachments
- runtime, model, or effort changes
- publish mode
- branch, starting branch, or target branch changes
- presets or dependencies
- arbitrary parameter patches
- plan, manifest, or input artifact overrides

Success response fragment:

```json
{
  "accepted": true,
  "applied": "created_resumed_execution",
  "source": {
    "workflowId": "mm:source",
    "runId": "run-source"
  },
  "execution": {
    "workflowId": "mm:resumed",
    "runId": "run-resumed",
    "detailHref": "/tasks/mm:resumed"
  },
  "relationship": "Resumed from failed step",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1"
}
```

Failure response fragment:

```json
{
  "detail": {
    "code": "resume_not_available",
    "message": "Failed-step Resume is not available for this execution.",
    "reason": "plan_identity_missing"
  }
}
```

Rules:
- Invalid evidence must fail before a resumed execution is created.
- Resume submission must not fall back to full rerun.
- Task edits must be rejected with a response that directs the operator to Edit task.

## Canonical Recovery Payload Contract

Accepted recovery intent must be representable as:

```json
{
  "recovery": {
    "kind": "resume_from_failed_step",
    "sourceWorkflowId": "mm:source",
    "sourceRunId": "run-source"
  },
  "resume": {
    "kind": "resume_from_failed_step",
    "sourceWorkflowId": "mm:source",
    "sourceRunId": "run-source",
    "failedStepId": "implement",
    "failedStepAttempt": 1,
    "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
    "taskInputSnapshotRef": "artifact://task-input/source",
    "planRef": "artifact://plan/source",
    "planDigest": "sha256:plan"
  }
}
```

Rules:
- If the implementation keeps execution-level `resumeSource`, it must be tested as equivalent to this canonical recovery intent at the adapter/workflow boundary.
- Generic rerun and edited full retry must not populate Resume reference fields.
