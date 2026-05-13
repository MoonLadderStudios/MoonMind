# Contract: Failed-Step Resume Execution

## Resume Submission Boundary

Accepted failed-step Resume submissions create a new `MoonMind.Run` execution with:

```json
{
  "resumeSource": {
    "kind": "resume_from_failed_step",
    "sourceWorkflowId": "mm:source",
    "sourceRunId": "source-run-id",
    "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
    "sourcePlanRef": "artifact://plan/source",
    "sourcePlanDigest": "sha256:plan",
    "failedStepId": "implement",
    "failedStepAttempt": 1,
    "resumeCheckpointRef": "artifact://resume/checkpoint",
    "resumeWorkspace": {
      "checkpointRef": "artifact://workspace/before-implement"
    },
    "preservedSteps": [
      {
        "logicalStepId": "plan",
        "order": 1,
        "status": "succeeded",
        "sourceAttempt": 1,
        "artifacts": {
          "outputSummary": "artifact://completed/plan-summary"
        },
        "stateCheckpointRef": "artifact://workspace/before-plan"
      }
    ]
  },
  "task": {
    "recovery": {
      "kind": "resume_from_failed_step",
      "sourceWorkflowId": "mm:source",
      "sourceRunId": "source-run-id"
    },
    "resume": {
      "kind": "resume_from_failed_step",
      "sourceWorkflowId": "mm:source",
      "sourceRunId": "source-run-id",
      "failedStepId": "implement",
      "failedStepAttempt": 1,
      "resumeCheckpointRef": "artifact://resume/checkpoint",
      "taskInputSnapshotRef": "artifact://snapshot/source",
      "planRef": "artifact://plan/source",
      "planDigest": "sha256:plan"
    }
  }
}
```

Rules:
- `sourceWorkflowId` and `sourceRunId` must match everywhere they appear.
- `task.resume.taskInputSnapshotRef` must match the source run's immutable task input snapshot.
- User-authored task edits are not accepted on failed-step Resume in v1.
- Invalid checkpoint evidence must block execution before a resumed failed step starts.

## MoonMind.Run Initialization Boundary

When `MoonMind.Run` starts with `resumeSource`:

1. Validate compact resume source fields before step execution.
2. Initialize prior completed step rows as preserved rows.
3. Restore or verify `resumeWorkspace` before the failed step starts.
4. Make preserved output refs available through the same step input contract used by continuous runs.
5. Mark the failed step as the first newly executable step.

Failure contract:
- Missing or mismatched source identity, snapshot, plan identity, preserved-step provenance, preserved output refs, or workspace evidence produces an explicit failed Resume outcome.
- The workflow must not silently convert failed-step Resume into exact full rerun.
- Preserved prior steps must not be re-executed unless a future explicit user action introduces that behavior.

## Step Ledger Projection Boundary

Preserved prior step rows expose:

```json
{
  "logicalStepId": "plan",
  "status": "succeeded",
  "attempt": 0,
  "summary": "Preserved from source run.",
  "artifacts": {
    "outputSummary": "artifact://completed/plan-summary"
  },
  "stateCheckpointRef": "artifact://workspace/before-plan",
  "preservedFrom": {
    "workflowId": "mm:source",
    "runId": "source-run-id",
    "logicalStepId": "plan",
    "attempt": 1
  }
}
```

Fresh retried and downstream step rows expose resumed-run evidence:
- New attempt count.
- New artifact refs.
- New checkpoint refs when runtime state changes.
- No `preservedFrom` marker on newly executed steps.
