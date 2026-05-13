# Contract: Step Ledger Checkpoint Evidence

This contract describes the parent-owned evidence that `MoonMind.Run` must expose for failed-step Resume decisions.

## Step Ledger Row Additions

```json
{
  "logicalStepId": "implement",
  "status": "succeeded",
  "attempt": 1,
  "artifacts": {
    "outputSummary": "artifact://summary",
    "outputPrimary": "artifact://primary",
    "runtimeDiagnostics": "artifact://diagnostics"
  },
  "stateCheckpointRef": "artifact://checkpoint/wf-run/implement/1",
  "resumePreservation": {
    "eligible": true,
    "reason": "complete",
    "message": "Step has recoverable output refs and state checkpoint evidence."
  }
}
```

Ineligible completed step example:

```json
{
  "logicalStepId": "plan",
  "status": "succeeded",
  "artifacts": {
    "outputSummary": "artifact://summary"
  },
  "stateCheckpointRef": null,
  "resumePreservation": {
    "eligible": false,
    "reason": "missing_state_checkpoint",
    "message": "Completed step cannot be preserved because no state checkpoint ref was recorded."
  }
}
```

Rules:

- `resumePreservation.reason` is bounded and machine-readable.
- `resumePreservation.message` is bounded and operator-readable.
- `stateCheckpointRef` is a ref only; it does not contain checkpoint payload bytes.
- Parent `MoonMind.Run` owns these fields even when the step delegates to a child workflow.

## Resume Checkpoint Payload

```json
{
  "schemaVersion": "v1",
  "source": {
    "workflowId": "mm:source",
    "runId": "run-source"
  },
  "taskInputSnapshotRef": "artifact://task-input-snapshot",
  "planDigest": "sha256:plan",
  "failedStep": {
    "logicalStepId": "verify",
    "order": 3,
    "attempt": 1,
    "title": "Verify"
  },
  "preservedSteps": [
    {
      "logicalStepId": "implement",
      "order": 2,
      "status": "succeeded",
      "sourceAttempt": 1,
      "artifacts": {
        "outputSummary": "artifact://summary",
        "outputPrimary": "artifact://primary"
      },
      "stateCheckpointRef": "artifact://checkpoint/wf-run/implement/1"
    }
  ],
  "preparedArtifactRefs": [
    "prepared-context://objective/input-1",
    "artifact://raw-input-1"
  ],
  "resumeWorkspace": {
    "branch": "feature/mm-646",
    "commit": "abc123"
  }
}
```

Rules:

- `preservedSteps` contains only steps with complete preservation evidence.
- Steps missing output refs or state checkpoint refs are excluded from preservation and remain visible in source ledger rows as ineligible.
- `preparedArtifactRefs` contains refs only.
- `resumeWorkspace` is compact metadata; large/binary checkpoint data is stored behind refs.
- Repeated checkpoint writes for the same source workflow, source run, logical step id, and attempt resolve idempotently.
