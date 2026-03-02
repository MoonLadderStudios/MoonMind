# API Contract: Manifest Registry and Run Submission

All endpoints are served under `/api/manifests`.

## GET /api/manifests

List manifest registry entries.

- **Query Params**:
  - `limit` (default `50`, min `1`, max `200`)
  - `search` (optional prefix search)
- **Response 200**:

```json
{
  "items": [
    {
      "name": "confluence-eng",
      "version": "v0",
      "contentHash": "sha256:...",
      "updatedAt": "2026-03-02T00:00:00Z",
      "lastRunJobId": "9d1e...",
      "lastRunStatus": "queued",
      "stateUpdatedAt": null
    }
  ]
}
```

## GET /api/manifests/{name}

Fetch one manifest registry entry.

- **Response 200**:

```json
{
  "name": "confluence-eng",
  "version": "v0",
  "content": "version: \"v0\"\nmetadata:\n  name: confluence-eng\n...",
  "contentHash": "sha256:...",
  "updatedAt": "2026-03-02T00:00:00Z",
  "lastRun": {
    "jobId": "9d1e...",
    "status": "queued",
    "startedAt": "2026-03-02T00:00:00Z",
    "finishedAt": null
  },
  "state": {
    "stateJson": {},
    "stateUpdatedAt": null
  }
}
```

- **Response 404**:

```json
{
  "detail": {
    "code": "manifest_not_found",
    "message": "Manifest 'confluence-eng' not found"
  }
}
```

## PUT /api/manifests/{name}

Create or update manifest content.

- **Request Body**:

```json
{
  "content": "version: \"v0\"\nmetadata:\n  name: confluence-eng\n..."
}
```

- **Response 200**: Same shape as `GET /api/manifests/{name}`.
- **Response 422** (invalid manifest payload):

```json
{
  "detail": {
    "code": "invalid_manifest",
    "message": "Invalid manifest payload"
  }
}
```

## POST /api/manifests/{name}/runs

Submit a queue job for a registry-backed manifest.

- **Request Body**:

```json
{
  "action": "run",
  "options": {
    "dryRun": false,
    "forceFull": false,
    "maxDocs": null
  }
}
```

- **Action Rules**:
  - Accepted values: `run`, `plan`
  - Default: `run`
  - Unsupported values fail request validation (HTTP 422)

- **Response 422** (request schema validation: unsupported action example):

```json
{
  "detail": [
    {
      "loc": ["body", "action"],
      "msg": "action must be one of: plan, run",
      "type": "value_error"
    }
  ]
}
```

Non-string and `null` action payloads return the same FastAPI 422 envelope with action-specific validation messages.

- **Response 201**:

```json
{
  "jobId": "9d1e...",
  "queue": {
    "type": "manifest",
    "requiredCapabilities": ["manifest", "embeddings", "openai", "qdrant", "github"],
    "manifestHash": "sha256:..."
  }
}
```

- **Response 404** (manifest missing):

```json
{
  "detail": {
    "code": "manifest_not_found",
    "message": "Manifest 'confluence-eng' not found"
  }
}
```

- **Response 422** (queue-level validation error):

```json
{
  "detail": {
    "code": "invalid_manifest_job",
    "message": "..."
  }
}
```

Queue-level `invalid_manifest_job` errors are distinct from request-schema validation errors and occur after payload parsing when queue/manifest contract validation fails.

## POST /api/queue/jobs (`type="manifest"`)

Inline manifest submissions remain supported through queue APIs and continue using manifest contract normalization.
