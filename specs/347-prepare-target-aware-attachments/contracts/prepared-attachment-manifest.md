# Prepared Attachment Manifest Contract

## Purpose

`MM-648` requires preparation to produce target-aware attachment evidence without silent retargeting and without binary payloads in workflow-visible data.

## Compact Workflow Manifest

```json
{
  "manifestRef": "prepared-context-manifest://task-inputs",
  "entries": [
    {
      "artifactId": "objective-image",
      "filename": "objective.png",
      "contentType": "image/png",
      "sizeBytes": 42,
      "targetKind": "objective",
      "rawInputRef": "artifact://objective-image",
      "derivedContextRef": "prepared-context://objective/objective-image",
      "workspacePath": ".moonmind/inputs/objective/objective-image-objective.png",
      "status": "prepared"
    },
    {
      "artifactId": "step-image",
      "filename": "screen.png",
      "contentType": "image/png",
      "sizeBytes": 17,
      "targetKind": "step",
      "stepRef": "review-step",
      "stepOrdinal": 1,
      "rawInputRef": "artifact://step-image",
      "derivedContextRef": "prepared-context://steps/review-step/step-image",
      "workspacePath": ".moonmind/inputs/steps/review-step/step-image-screen.png",
      "status": "prepared"
    }
  ]
}
```

## Rules

- `stepRef` is required for every `targetKind: step` entry.
- `stepOrdinal` is diagnostic only and must never be used as the binding authority.
- `workspacePath` is a stable workspace-relative path and must not contain binary bytes.
- `status` is required for materialized manifest entries and should be `prepared` after successful materialization.
- Invalid, missing, unauthorized, incomplete, or inline-content attachment inputs fail preparation before affected step execution.
- Reorder, preset apply, and text edits must preserve `stepRef`-based binding; an attachment must not bind to a new step because its array index changed.
