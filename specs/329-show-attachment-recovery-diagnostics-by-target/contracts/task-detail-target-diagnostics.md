# Contract: Task Detail Target Diagnostics

## Surface

The task detail API response and task detail UI expose a bounded target diagnostics summary for `MoonMind.Run` executions when target-aware evidence exists.

## Execution Detail Shape

Add or populate a compact optional object on execution detail responses:

```json
{
  "targetDiagnostics": {
    "targets": [
      {
        "targetKind": "objective",
        "label": "Task objective",
        "attachments": [
          {
            "artifactRef": "artifact://input/objective-1",
            "filename": "wireframe.png",
            "contentType": "image/png",
            "sizeBytes": 12345,
            "previewAvailable": true
          }
        ],
        "refs": [
          {
            "refKind": "attachment_manifest",
            "artifactRef": "artifact://manifest/attachments"
          },
          {
            "refKind": "generated_context",
            "artifactRef": "artifact://vision/context-index"
          }
        ],
        "failures": []
      },
      {
        "targetKind": "step",
        "stepId": "inspect",
        "label": "Inspect screenshot",
        "attachments": [],
        "refs": [],
        "failures": [
          {
            "phase": "materialization",
            "message": "Attachment download failed before step execution.",
            "evidenceRef": "artifact://diagnostics/prepare"
          }
        ]
      }
    ],
    "recovery": {
      "resumed": true,
      "sourceWorkflowId": "mm:source",
      "sourceRunId": "run-source",
      "checkpointRef": "artifact://resume/checkpoint",
      "preservedSteps": [
        {
          "logicalStepId": "prepare",
          "title": "Prepare context",
          "sourceAttempt": 1,
          "sourceWorkflowId": "mm:source",
          "sourceRunId": "run-source"
        }
      ],
      "failedResumePhase": null
    },
    "degradedReason": null
  }
}
```

## UI Behavior

- Render objective target diagnostics separately from step target diagnostics.
- For each target, show attachment metadata, target-owned refs, and failures in the same target group.
- Show an explicit empty state when a known target has no attachments.
- Show generated context and manifest refs as refs, not embedded artifact bodies.
- Show Resume provenance and preserved prior steps near recovery information.
- Show failed Resume phases using bounded labels:
  - checkpoint validation
  - workspace restoration
  - preserved-output injection
  - failed-step execution
- Keep raw diagnostics panels available for deep troubleshooting, but do not require raw diagnostics parsing for the primary target ownership and phase information.

## Failure And Degraded Evidence

- If target evidence is unavailable, expose a bounded `degradedReason`.
- If a failure has no known target, display degraded evidence instead of assigning it to an arbitrary target.
- If a raw event has an unknown phase, preserve a bounded message and mark the phase as degraded rather than adding a new public phase silently.

## Traceability

- This contract supports MM-635.
- It covers FR-001 through FR-012, SC-001 through SC-005, DESIGN-REQ-023, and DESIGN-REQ-024.
