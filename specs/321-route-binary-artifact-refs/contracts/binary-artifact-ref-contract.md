# Contract: Binary Artifact Refs For Task Submission

## Create Upload Intent

Endpoint: `POST /api/artifacts`

Request:

```json
{
  "content_type": "image/png",
  "size_bytes": 12345,
  "sha256": "optional lowercase sha256",
  "metadata": {
    "filename": "wireframe.png",
    "source": "task-dashboard-objective-attachment",
    "targetKind": "objective"
  }
}
```

Response:

```json
{
  "artifact_ref": {
    "artifact_id": "art_...",
    "content_type": "image/png",
    "size_bytes": 12345,
    "sha256": "optional lowercase sha256",
    "encryption": "none",
    "artifact_ref_v": 1
  },
  "upload": {
    "mode": "single_put",
    "upload_url": "http://testserver/api/artifacts/art_.../content",
    "upload_id": null,
    "expires_at": "2026-05-08T09:00:00Z",
    "max_size_bytes": 10485760,
    "required_headers": {}
  }
}
```

Rules:
- The browser must use this MoonMind endpoint before submitting a task with new binary input bytes.
- The browser must not call object storage or provider file endpoints directly except through returned short-lived upload URLs.

## Complete Upload

Endpoints:
- `PUT /api/artifacts/{artifactId}/content` for single-put uploads.
- `POST /api/artifacts/{artifactId}/complete` for multipart completion.

Rules:
- Completion validates declared size, digest, content type, and image signatures where applicable.
- Failed completion marks the artifact failed and the ref cannot be used for execution submission.

## Submit Task With Structured Attachment Refs

Endpoint: `POST /api/executions`

Request shape:

```json
{
  "type": "task",
  "payload": {
    "task": {
      "instructions": "Review the uploaded diagram.",
      "inputAttachments": [
        {
          "artifactId": "art_...",
          "filename": "diagram.png",
          "contentType": "image/png",
          "sizeBytes": 12345
        }
      ],
      "steps": [
        {
          "id": "step-1",
          "instructions": "Inspect the step-specific image.",
          "inputAttachments": [
            {
              "artifactId": "art_...",
              "filename": "step.png",
              "contentType": "image/png",
              "sizeBytes": 23456
            }
          ]
        }
      ]
    }
  }
}
```

Acceptance rules:
- Every referenced artifact must exist, be `complete`, match declared content type/size, and be authorized for the submitting principal and execution scope.
- Execution submission must reject pending, failed, deleted, missing, duplicate, unsupported, oversized, or unauthorized refs before execution starts.
- Accepted submissions link each input artifact to the execution with `linkType = "input.attachment"`.
- Execution payloads contain structured refs only. They must not contain raw bytes, object-store credentials, presigned download URLs, or provider-specific file payloads.

## Preview And Download

Endpoints:
- `GET /api/artifacts/{artifactId}`
- `POST /api/artifacts/{artifactId}/presign-download`
- `GET /api/artifacts/{artifactId}/download`

Rules:
- Browser preview/download must use MoonMind APIs.
- Raw access requires owner, execution view permission, or service authorization.
- Restricted artifacts return preview/default read refs according to artifact read policy.

## Worker Materialization

Runtime boundary:
- Worker receives task structured refs.
- Worker downloads each artifact through a MoonMind service-authorized path.
- Worker writes files under target-aware workspace paths and writes an attachment manifest.

Rules:
- Worker materialization must fail explicitly when an artifact cannot be read or is not authorized for the execution.
- Worker materialization must not expose storage credentials to the browser or encode raw bytes in task instructions.
