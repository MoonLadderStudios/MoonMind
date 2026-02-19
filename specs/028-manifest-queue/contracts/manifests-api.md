# API Contract: Manifest Registry + Runs (Phase 0)

All endpoints live under the FastAPI service namespace `/api`. Responses use JSON and standard error envelopes already defined in the Agent Queue API (400 for validation errors, 404 for missing resources, 409 for conflicts).

## GET /api/manifests

Returns a paginated list of manifest registry entries.

- **Query Params** (optional): `limit` (default 50), `cursor` (opaque string), `search` (prefix match on name)
- **Response 200**:
```json
{
  "items": [
    {
      "name": "confluence-eng",
      "version": "v0",
      "contentHash": "sha256:...",
      "updatedAt": "2026-02-18T22:15:00Z",
      "lastRunJobId": "9d1e...",
      "lastRunStatus": "succeeded"
    }
  ],
  "nextCursor": null
}
```

## GET /api/manifests/{name}

Fetches a single manifest entry by name.

- **Path Params**: `name` (string, case-sensitive)
- **Response 200**:
```json
{
  "name": "confluence-eng",
  "version": "v0",
  "content": "version: \"v0\"\nmetadata:\n  name: confluence-eng\n...",
  "contentHash": "sha256:...",
  "state": {
    "stateJson": {"dataSources": {}},
    "stateUpdatedAt": null
  },
  "lastRun": {
    "jobId": "9d1e...",
    "status": "succeeded",
    "startedAt": "2026-02-18T22:15:00Z",
    "finishedAt": "2026-02-18T22:20:00Z"
  }
}
```
- **Errors**: 404 when name unknown.

## PUT /api/manifests/{name}

Creates or updates a manifest entry.

- **Request Body**:
```json
{
  "content": "version: \"v0\"\nmetadata:\n  name: confluence-eng\n...",
  "version": "v0"
}
```
  - `version` optional; server defaults to `v0` if omitted.
  - Server computes `contentHash`, persists YAML, updates `updated_at`.
- **Response 200**: returns same payload as GET detail with updated metadata.
- **Validation**: rejects when `metadata.name` inside YAML differs from `{name}` or when YAML fails schema validation.

## POST /api/manifests/{name}/runs

Submits a manifest ingestion job referencing a stored manifest.

- **Request Body** (optional overrides):
```json
{
  "action": "run",          // default "run"; allow "plan"
  "options": {
    "dryRun": false,
    "forceFull": false,
    "maxDocs": null
  }
}
```
- **Behavior**:
  1. Load manifest YAML from registry and validate via manifest contract module.
  2. Derive `requiredCapabilities`, `manifestHash`, and `manifestVersion`.
  3. Call `AgentQueueService.create_job(type="manifest", payload=...)`.
- **Response 201**:
```json
{
  "jobId": "9d1e...",
  "queue": {
    "type": "manifest",
    "requiredCapabilities": ["manifest", "qdrant", "google", "confluence"],
    "manifestHash": "sha256:..."
  }
}
```
- **Errors**: 400 when manifest content invalid, 404 when name missing, 409 when manifest is already being run and duplicates are disallowed (Phase 0 may allow duplicates but should warn via `alreadyRunning` flag if we later add locking).

## POST /api/queue/jobs (`type="manifest"`)

Inline submissions continue to use the existing queue endpoint. Manifest-specific validation occurs inside `AgentQueueService.create_job`; the payload schema mirrors the registry-run payload but accepts `manifest.source.kind == "inline"` with embedded YAML.
