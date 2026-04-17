# Contract: Target-Aware Vision Context Artifacts

## Service Input

Target-aware generation accepts a workspace root and explicit targets:

```text
workspace_root: path
targets:
  - targetKind: objective
    attachments: [AttachmentContextInput...]
  - targetKind: step
    stepRef: stable-step-ref
    attachments: [AttachmentContextInput...]
```

Rules:

- `targetKind` must be `objective` or `step`.
- `stepRef` is required for step targets.
- Source attachment refs must be passed as metadata, not image bytes.
- Target meaning is supplied by the target object, not inferred from filenames or local paths.

## Generated Files

Objective target:

```text
.moonmind/vision/task/image_context.md
```

Step target:

```text
.moonmind/vision/steps/<stepRef>/image_context.md
```

Index:

```text
.moonmind/vision/image_context_index.json
```

## Index Shape

```json
{
  "version": 1,
  "generated": true,
  "config": {
    "provider": "gemini_cli",
    "model": "models/gemini-2.5-flash",
    "ocrEnabled": true
  },
  "targets": [
    {
      "targetKind": "objective",
      "stepRef": null,
      "status": "ok",
      "contextPath": ".moonmind/vision/task/image_context.md",
      "attachmentRefs": ["artifact-objective"],
      "sourcePaths": [".moonmind/inputs/objective/artifact-objective-image.png"]
    },
    {
      "targetKind": "step",
      "stepRef": "step-1",
      "status": "ok",
      "contextPath": ".moonmind/vision/steps/step-1/image_context.md",
      "attachmentRefs": ["artifact-step"],
      "sourcePaths": [".moonmind/inputs/steps/step-1/artifact-step-image.png"]
    }
  ]
}
```

Rules:

- `generated` is true only when at least one target rendered with `ok` status.
- Disabled or provider-unavailable targets are represented with explicit `status` values.
- `attachmentRefs` and `sourcePaths` preserve traceability to source refs and materialized files.
- Index entry ordering follows target ordering and attachment ordering supplied by the caller.
