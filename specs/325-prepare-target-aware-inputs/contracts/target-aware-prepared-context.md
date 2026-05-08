# Contract: Target-Aware Prepared Context

## Purpose

Define the compact runtime boundary between task-shaped attachment refs, prepare output, `MoonMind.Run` step execution, delegated `MoonMind.AgentRun` children, and runtime adapters.

## Prepare Input

```json
{
  "task": {
    "instructions": "Review the supplied diagrams.",
    "inputAttachments": [
      {
        "artifactId": "art_objective",
        "filename": "overview.png",
        "contentType": "image/png",
        "sizeBytes": 1234
      }
    ],
    "steps": [
      {
        "id": "review-step",
        "instructions": "Review the current screen.",
        "inputAttachments": [
          {
            "artifactId": "art_step_review",
            "filename": "review.png",
            "contentType": "image/png",
            "sizeBytes": 5678
          }
        ]
      }
    ]
  }
}
```

Rules:
- `task.inputAttachments` are objective-scoped.
- `task.steps[n].inputAttachments` are scoped only to the owning step.
- Instruction fields remain text.
- Embedded binary, base64, data URLs, and generated markdown are invalid in attachment refs.

## Prepare Result

```json
{
  "manifestRef": "artifact://prepared-input-manifest/MM-631-run-1",
  "attachments": [
    {
      "artifactId": "art_objective",
      "targetKind": "objective",
      "contentType": "image/png",
      "sizeBytes": 1234,
      "workspacePath": ".moonmind/inputs/objective/art_objective-overview.png",
      "derivedContextRef": "artifact://image-context/task"
    },
    {
      "artifactId": "art_step_review",
      "targetKind": "step",
      "stepRef": "review-step",
      "stepOrdinal": 0,
      "contentType": "image/png",
      "sizeBytes": 5678,
      "workspacePath": ".moonmind/inputs/steps/review-step/art_step_review-review.png",
      "derivedContextRef": "artifact://image-context/steps/review-step"
    }
  ],
  "diagnostics": [
    {
      "event": "prepare_download_completed",
      "status": "completed",
      "artifactId": "art_objective",
      "targetKind": "objective"
    }
  ]
}
```

Rules:
- Workflow history carries compact refs and bounded metadata only.
- Raw file bytes and long derived context bodies stay behind refs.
- Failure to prepare a required item returns a failed prepare result or raises a deterministic prepare error before the affected step dispatches.

## Step Prepared Context

```json
{
  "logicalStepId": "review-step",
  "manifestRef": "artifact://prepared-input-manifest/MM-631-run-1",
  "objectiveContextRefs": ["artifact://image-context/task"],
  "stepContextRefs": ["artifact://image-context/steps/review-step"],
  "rawInputRefs": [
    ".moonmind/inputs/objective/art_objective-overview.png",
    ".moonmind/inputs/steps/review-step/art_step_review-review.png"
  ]
}
```

Rules:
- `objectiveContextRefs` may be shared with each step by default.
- `stepContextRefs` and step raw refs include only entries for `logicalStepId`.
- No unrelated step's prepared context may be present.

## Child AgentRun Request Boundary

When `MoonMind.Run` delegates a step to `MoonMind.AgentRun`, the child request must carry only the represented step's prepared context through compact refs or metadata.

Expected properties:
- `inputRefs` contains only relevant objective refs plus represented-step refs.
- `parameters.metadata.moonmind.preparedContext` or equivalent bounded metadata identifies the manifest ref and target filtering decision.
- Child requests do not receive unrelated step refs.
- Runtime adapters may realize refs as text-first or multimodal payloads but must not change target binding.

## Failure Contract

A prepare failure result includes:

```json
{
  "status": "failed",
  "targetKind": "step",
  "stepRef": "review-step",
  "artifactId": "art_step_review",
  "reason": "artifact unavailable",
  "retryable": false
}
```

Rules:
- The affected step is not dispatched after failed required preparation.
- Operator-visible diagnostics identify target kind, step ref when applicable, artifact id, and bounded reason.
- Secrets, raw credentials, and binary payloads are never included in failure metadata.
