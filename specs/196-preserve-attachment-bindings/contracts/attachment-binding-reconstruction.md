# Contract: Attachment Binding Reconstruction

## Original Task Input Snapshot

Snapshot artifacts for task-shaped executions must preserve attachment refs in the canonical task body:

```json
{
  "snapshotVersion": 1,
  "source": { "kind": "create" },
  "draft": {
    "taskShape": "multi_step",
    "repository": "MoonLadderStudios/MoonMind",
    "targetRuntime": "codex_cli",
    "task": {
      "instructions": "Run the task",
      "inputAttachments": [
        {
          "artifactId": "art-objective",
          "filename": "objective.png",
          "contentType": "image/png",
          "sizeBytes": 1024
        }
      ],
      "steps": [
        {
          "id": "step-1",
          "instructions": "Inspect the image",
          "inputAttachments": [
            {
              "artifactId": "art-step",
              "filename": "step.png",
              "contentType": "image/png",
              "sizeBytes": 2048
            }
          ]
        }
      ]
    }
  },
  "attachmentRefs": [
    {
      "artifactId": "art-objective",
      "filename": "objective.png",
      "contentType": "image/png",
      "sizeBytes": 1024,
      "targetKind": "objective"
    },
    {
      "artifactId": "art-step",
      "filename": "step.png",
      "contentType": "image/png",
      "sizeBytes": 2048,
      "targetKind": "step",
      "stepId": "step-1",
      "stepOrdinal": 0
    }
  ]
}
```

Rules:
- `draft.task.inputAttachments` is the objective-scoped source of truth.
- `draft.task.steps[n].inputAttachments` is the step-scoped source of truth.
- `attachmentRefs` is a compact index and diagnostic aid, not a replacement for the task body.
- Artifact link metadata, filenames, and artifact IDs alone cannot retarget attachments.

## Edit And Rerun Draft Reconstruction

Given a detail response with `taskInputSnapshot.available = true` and the downloaded snapshot artifact above, the Create page must reconstruct:

```json
{
  "inputAttachments": [
    {
      "artifactId": "art-objective",
      "filename": "objective.png",
      "contentType": "image/png",
      "sizeBytes": 1024
    }
  ],
  "steps": [
    {
      "id": "step-1",
      "instructions": "Inspect the image",
      "inputAttachments": [
        {
          "artifactId": "art-step",
          "filename": "step.png",
          "contentType": "image/png",
          "sizeBytes": 2048
        }
      ]
    }
  ]
}
```

Rules:
- Persisted refs are displayed and submitted as existing refs unless the user removes them.
- New local files remain separate until uploaded through the existing attachment flow.
- Edit and rerun submissions include unchanged persisted refs.
- If attachment refs are known to exist but the target binding cannot be reconstructed from the snapshot task body, draft reconstruction fails explicitly.

## Failure Contract

Explicit failures are required when:
- `taskInputSnapshot.available` is false for an edit/rerun action that needs attachment binding reconstruction.
- The snapshot is malformed or missing `draft.task`.
- The snapshot contains attachment refs that cannot be assigned to objective or step targets.
- Reconstruction would require inferring a target from filename, artifact link, or metadata.
