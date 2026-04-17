# Contract: Create Page Policy-Gated Image Upload

## Policy Visibility

When the Create page receives:

```json
{
  "system": {
    "attachmentPolicy": {
      "enabled": false
    }
  }
}
```

Expected behavior:
- Objective attachment entry points are hidden.
- Step attachment entry points are hidden.
- Manual task authoring remains available.
- Submit without attachments remains available when all non-attachment validation passes.

## Image-Specific Labeling

When all allowed content types start with `image/`, visible attachment controls use image-oriented user-facing copy, such as `Images`.

Expected behavior:
- Objective attachment controls use image-specific labeling.
- Step attachment controls use image-specific labeling.
- The accepted file types come from `attachmentPolicy.allowedContentTypes`.

## Browser Validation

The browser validates selected local files against:
- total attachment count
- per-file byte limit
- total byte limit
- allowed content types

Expected behavior:
- Validation errors identify the affected target.
- Invalid selections remain visible until removed.
- Submit is blocked while invalid selections remain.
- Removing an invalid selection does not clear unrelated draft state.

## Upload Before Submit

Create, edit, and rerun submit flows must upload selected local images before sending the execution payload.

Expected payload shape:

```json
{
  "task": {
    "instructions": "Task objective",
    "inputAttachments": [
      {
        "artifactId": "art_objective_1",
        "filename": "objective.png",
        "contentType": "image/png",
        "sizeBytes": 1234
      }
    ],
    "steps": [
      {
        "instructions": "Step instructions",
        "inputAttachments": [
          {
            "artifactId": "art_step_1",
            "filename": "step.webp",
            "contentType": "image/webp",
            "sizeBytes": 2345
          }
        ]
      }
    ]
  }
}
```

Rules:
- Objective-scoped refs appear only in `task.inputAttachments`.
- Step-scoped refs appear only in the owning step's `inputAttachments`.
- Raw binary image content is never included in the execution payload.
- Attachment target meaning is never inferred from filename.

## Failure Handling

Expected behavior:
- Upload failure is shown at the affected target.
- Failed attachments can be retried or removed.
- Preview failure keeps filename, content type, size, and remove action visible.
- Submit is blocked while attachments are failed, incomplete, invalid, or uploading.
- Failure on one target does not clear objective text, step instructions, preset state, runtime fields, repository fields, or unrelated attachments.
