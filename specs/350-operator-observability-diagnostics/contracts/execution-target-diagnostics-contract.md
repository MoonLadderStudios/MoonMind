# Contract: Execution Target Diagnostics

## Surface

Execution detail responses expose `targetDiagnostics` as a compact, optional block for task detail and diagnostics UI.

Consumer surfaces:
- API execution detail response for a MoonMind.Run execution.
- Mission Control task detail target diagnostics panel.
- Generated OpenAPI/TypeScript models used by the frontend.

## Response Shape

```json
{
  "targetDiagnostics": {
    "targets": [
      {
        "targetKind": "objective",
        "stepId": null,
        "label": "Task objective",
        "attachments": [
          {
            "artifactRef": "artifact://input/objective-image",
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 12345,
            "previewAvailable": true
          }
        ],
        "refs": [
          {
            "refKind": "attachment_manifest",
            "artifactRef": "artifact://diagnostics/input-manifest",
            "path": null
          }
        ],
        "failures": []
      },
      {
        "targetKind": "step",
        "stepId": "inspect",
        "label": "Inspect screenshot",
        "attachments": [],
        "refs": [
          {
            "refKind": "generated_context",
            "artifactRef": "artifact://context/inspect",
            "path": null
          }
        ],
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

## Bounded Values

Attachment failure phases:
- `upload`
- `validation`
- `materialization`
- `context_generation`
- `degraded`

Failed Resume phases:
- `checkpoint_validation`
- `workspace_restoration`
- `preserved_output_injection`
- `failed_step_execution`

Target kinds:
- `objective`
- `step`

## Contract Rules

- `targetDiagnostics` may be null or omitted when no target diagnostics, recovery evidence, or degraded reason exists.
- Objective and step targets must not be merged or retargeted by compatibility aliases.
- A step target must retain its step identifier when known.
- The UI must display target diagnostics without replacing raw diagnostics access.
- Unknown attachment failure phases must map to `degraded`.
- Unknown failed Resume phases must not be silently mislabeled as another bounded phase.
- Binary attachment payloads must not appear in the response.
- Artifact refs remain subject to existing authorization, preview, and download policy.

## Test Obligations

- Backend route tests for objective and step target grouping, manifest refs, generated context refs, empty-target distinction, failure phase normalization, Resume provenance, and failed Resume phases.
- Frontend tests proving the task detail panel renders target cards, refs, failures, preserved steps, failed Resume phase, and raw diagnostics independently.
- Integration schema or route-boundary tests proving alias-shaped inputs preserve objective versus step target meaning.
