# Contract: Task Input Attachments

## Endpoint

`POST /api/executions`

## Task-Shaped Request

```json
{
  "type": "task",
  "payload": {
    "repository": "owner/repo",
    "targetRuntime": "codex",
    "task": {
      "instructions": "Use the objective image.",
      "inputAttachments": [
        {
          "artifactId": "art_objective_123",
          "filename": "objective.png",
          "contentType": "image/png",
          "sizeBytes": 48213
        }
      ],
      "steps": [
        {
          "id": "step-1",
          "instructions": "Use the step image.",
          "inputAttachments": [
            {
              "artifactId": "art_step_456",
              "filename": "step.png",
              "contentType": "image/png",
              "sizeBytes": 72109
            }
          ]
        }
      ]
    }
  }
}
```

## Expected Workflow Input

`MoonMind.Run` initial parameters include:

```json
{
  "task": {
    "inputAttachments": [
      {
        "artifactId": "art_objective_123",
        "filename": "objective.png",
        "contentType": "image/png",
        "sizeBytes": 48213
      }
    ],
    "steps": [
      {
        "id": "step-1",
        "instructions": "Use the step image.",
        "inputAttachments": [
          {
            "artifactId": "art_step_456",
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 72109
          }
        ]
      }
    ]
  }
}
```

## Validation Failures

The endpoint returns `422 invalid_execution_request` when:
- `inputAttachments` is not an array.
- Any attachment ref is not an object.
- Required compact metadata is missing or blank.
- `sizeBytes` is not a valid non-negative integer.
- The attachment object contains raw byte/content fields such as `bytes`, `data`, `dataUrl`, `dataURL`, `content`, or `base64`.
- Any attachment string value begins with `data:image/`.

## Non-Canonical Fields

Legacy `attachments`, `attachmentIds`, and `attachment_ids` are not the canonical image input submission contract for task-shaped execution.
