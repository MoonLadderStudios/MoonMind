# Contract: Image Attachment Policy

## Task Submission Contract

Task-shaped execution payloads may include:

```json
{
  "payload": {
    "task": {
      "inputAttachments": [
        {
          "artifactId": "art_...",
          "filename": "diagram.png",
          "contentType": "image/png",
          "sizeBytes": 1234
        }
      ],
      "steps": [
        {
          "inputAttachments": [
            {
              "artifactId": "art_...",
              "filename": "step.webp",
              "contentType": "image/webp",
              "sizeBytes": 2048
            }
          ]
        }
      ]
    }
  }
}
```

Validation errors:
- Attachment policy disabled: reject with `invalid_execution_request`.
- Unknown attachment ref field: reject with `invalid_execution_request`.
- Incomplete, missing, deleted, or unreadable artifact: reject with `invalid_execution_request`.
- Unsupported content type or `image/svg+xml`: reject with `invalid_execution_request`.
- Max count, per-file size, or total-size violation: reject with `invalid_execution_request`.

## Artifact Completion Contract

Artifacts created by the Create-page attachment flow are identified by metadata:

```json
{
  "metadata": {
    "source": "task-dashboard-step-attachment"
  }
}
```

Completion rules:
- Completed bytes must satisfy declared size/hash.
- Declared and actual content type must be allowed by policy.
- PNG bytes must start with the PNG signature.
- JPEG bytes must start with the JPEG SOI marker.
- WebP bytes must use RIFF/WEBP framing.
- SVG and unknown image types are rejected.

## Snapshot Contract

Original task input snapshots include:

```json
{
  "attachmentRefs": [
    {
      "artifactId": "art_...",
      "filename": "diagram.png",
      "contentType": "image/png",
      "sizeBytes": 1234,
      "targetKind": "objective"
    }
  ]
}
```

Rules:
- The snapshot task body preserves canonical `inputAttachments` fields.
- `attachmentRefs` is a compact index for reconstruction and visibility.
- Artifact metadata cannot override target kind.
