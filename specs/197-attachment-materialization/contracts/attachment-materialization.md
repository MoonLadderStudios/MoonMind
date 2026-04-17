# Contract: Attachment Materialization

Source story: MM-370 Jira preset brief for prepare-time attachment manifest and workspace files.

## Inputs

Prepare reads the canonical task payload:

```json
{
  "task": {
    "inputAttachments": [
      {
        "artifactId": "art_objective",
        "filename": "diagram.png",
        "contentType": "image/png",
        "sizeBytes": 123
      }
    ],
    "steps": [
      {
        "id": "review",
        "inputAttachments": [
          {
            "artifactId": "art_step",
            "filename": "screen.png",
            "contentType": "image/png",
            "sizeBytes": 456
          }
        ]
      }
    ]
  }
}
```

## Outputs

Prepare writes local files:

```text
.moonmind/inputs/objective/art_objective-diagram.png
.moonmind/inputs/steps/review/art_step-screen.png
```

Prepare writes `.moonmind/attachments_manifest.json`:

```json
{
  "version": 1,
  "attachments": [
    {
      "artifactId": "art_objective",
      "filename": "diagram.png",
      "contentType": "image/png",
      "sizeBytes": 123,
      "targetKind": "objective",
      "workspacePath": ".moonmind/inputs/objective/art_objective-diagram.png"
    },
    {
      "artifactId": "art_step",
      "filename": "screen.png",
      "contentType": "image/png",
      "sizeBytes": 456,
      "targetKind": "step",
      "stepRef": "review",
      "stepOrdinal": 0,
      "workspacePath": ".moonmind/inputs/steps/review/art_step-screen.png"
    }
  ]
}
```

## Rules

- `targetKind` is derived only from the containing field.
- Objective entries do not include `stepRef`.
- Step entries include `stepRef`; when the source step has no id, prepare uses a stable ordinal fallback.
- `workspacePath` is relative to the repository workspace.
- File names are sanitized and prefixed with `artifactId`.
- Manifest writing is all-or-failure: any undeclared, missing, unreadable, or unwritable attachment fails prepare.
- Raw bytes are written only to local workspace files, never to workflow payloads, task instruction text, or Temporal history.

## Errors

Prepare fails before runtime execution when:
- an attachment ref is not an object,
- a required ref field is missing,
- a filename cannot produce a safe output basename,
- an artifact download fails,
- a workspace file write fails,
- manifest serialization or write fails.
