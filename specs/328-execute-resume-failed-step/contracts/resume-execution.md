# Contract: Failed-Step Resume Execution

## POST `/api/executions/{workflow_id}/resume-from-failed-step`

Failed-step Resume creates a linked execution from a failed `MoonMind.Run` source.

Request body:

```json
{
  "idempotencyKey": "resume-1",
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1"
}
```

Rules:

- The request must not include edited task instructions, steps, attachments, runtime, publish mode, branch, dependencies, or preset metadata.
- The source execution must be failed and must identify an original task input snapshot.
- The checkpoint ref must match the source execution's canonical checkpoint ref.
- Checkpoint payload validation and restoration preconditions must pass before the new execution is created or before any step executes.

Success response:

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
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1"
}
```

Failure behavior:

- Invalid source identity, snapshot identity, plan identity, checkpoint payload, workspace evidence, or preserved output evidence must return an operator-readable failure.
- Invalid restoration must not create a full rerun.
- Invalid restoration must not re-execute preserved prior steps.

## Resumed Execution `resumeSource`

The resumed execution parameters include compact Resume source metadata:

```json
{
  "kind": "resume_from_failed_step",
  "sourceWorkflowId": "mm:source",
  "sourceRunId": "source-run-id",
  "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
  "sourcePlanRef": "artifact://plan/source",
  "sourcePlanDigest": "sha256:source-plan",
  "failedStepId": "implement",
  "failedStepAttempt": 1,
  "resumeCheckpointRef": "artifact://resume-checkpoints/source/checkpoint-v1",
  "preservedSteps": [
    {
      "logicalStepId": "prepare",
      "order": 1,
      "status": "succeeded",
      "sourceAttempt": 1,
      "artifacts": {
        "outputSummary": "artifact://steps/prepare-summary"
      },
      "stateCheckpointRef": "artifact://workspace/before-prepare"
    }
  ]
}
```

Rules:

- `preservedSteps` are imported as preserved progress only.
- Preserved output refs must be available to the failed and downstream steps as continuous-run context.
- The failed step named by `failedStepId` is the first newly executed step.

## Step Row Preserved Provenance

Step rows for resumed executions expose preserved provenance:

```json
{
  "logicalStepId": "prepare",
  "status": "succeeded",
  "attempt": 0,
  "summary": "Preserved from source run.",
  "stateCheckpointRef": "artifact://workspace/before-prepare",
  "preservedFrom": {
    "workflowId": "mm:source",
    "runId": "source-run-id",
    "logicalStepId": "prepare",
    "attempt": 1
  }
}
```

Rules:

- Preserved rows must not be rendered as newly executed by the resumed run.
- Preserved rows must carry source workflow ID, run ID, logical step ID, and attempt.
- Newly executed failed and downstream steps must not carry `preservedFrom`.
