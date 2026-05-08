# Contract: Failed-Step Resume Evidence Gate

Traceability: MM-633, `spec.md` FR-001 through FR-013.

## Execution Detail Contract

`GET /api/executions/{workflow_id}` must expose failed-step Resume availability from backend evidence.

Response fields:

```json
{
  "actions": {
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
- `canResumeFromFailedStep=true` only when the evidence bundle is complete and valid.
- When unavailable, `disabledReasons.canResumeFromFailedStep` and `resume.disabledReason` should expose a bounded reason.
- The UI must render availability from these backend fields and must not infer Resume eligibility from local text or status alone.

## Resume Submission Contract

`POST /api/executions/{workflow_id}/resume-from-failed-step`

Request body:

```json
{
  "idempotencyKey": "resume-operator-action-1",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1"
}
```

Rules:
- `idempotencyKey` is required.
- `resumeCheckpointRef` may be omitted only when the source execution has one canonical validated checkpoint ref.
- Task mutation fields remain forbidden.
- The route must hydrate checkpoint evidence, validate it against the source execution, and fail before creating a resumed execution when evidence is missing, stale, unauthorized, corrupted, or inconsistent.

Success response:

```json
{
  "accepted": true,
  "applied": "created_resumed_execution",
  "source": {"workflowId": "mm:source", "runId": "run-source"},
  "execution": {"workflowId": "mm:resumed", "runId": "run-resumed", "detailHref": "/tasks/mm:resumed"},
  "relationship": "Resumed from failed step",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1"
}
```

Failure response shape:

```json
{
  "detail": {
    "code": "resume_not_available",
    "message": "Failed-step Resume is not available for this execution.",
    "reason": "workspace_checkpoint_missing"
  }
}
```

Required bounded reasons include:
- `original_task_input_snapshot_missing`
- `resume_checkpoint_missing`
- `failed_step_identity_missing`
- `completed_step_refs_missing`
- `workspace_checkpoint_missing`
- `plan_identity_missing`
- `checkpoint_unauthorized`
- `checkpoint_corrupted`
- `checkpoint_inconsistent`
- `state_not_eligible`

## Checkpoint Evidence Contract

Compact checkpoint payload shape:

```json
{
  "schemaVersion": "v1",
  "source": {"workflowId": "mm:source", "runId": "run-source"},
  "taskInputSnapshotRef": "artifact://snapshot/source",
  "planRef": "artifact://plan/source",
  "planDigest": "sha256:plan",
  "failedStep": {"logicalStepId": "implement", "order": 2, "attempt": 1, "title": "Implement"},
  "preservedSteps": [
    {
      "logicalStepId": "prepare",
      "order": 1,
      "status": "succeeded",
      "sourceAttempt": 1,
      "artifacts": {"outputSummary": "artifact://prepare-summary"},
      "stateCheckpointRef": "artifact://workspace/before-implement"
    }
  ],
  "preparedArtifactRefs": ["artifact://prepared/input"],
  "resumeWorkspace": {"kind": "workspace_checkpoint", "ref": "artifact://workspace/before-implement"}
}
```

Rules:
- Plan identity must be present as `planRef`, `planDigest`, or both according to the final implementation contract.
- `resumeWorkspace` must point to recoverable state and must not be empty for eligible Resume.
- Large or binary payload bodies must be behind refs.
- Completed prior steps without recoverable refs or state checkpoint evidence must block Resume eligibility.
- Repeated checkpoint writes for the same source failed-step evidence must be idempotent.
