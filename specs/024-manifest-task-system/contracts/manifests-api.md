# API Contract: Manifest Registry & Runs

## Base Path
`/api/manifests`

## GET /api/manifests
- **Description**: List manifest records.
- **Response 200**:
```json
{
  "items": [
    {
      "name": "repo-docs",
      "version": "v0",
      "updatedAt": "2026-02-18T00:00:00Z",
      "lastRunJobId": "uuid",
      "lastRunStatus": "succeeded"
    }
  ]
}
```

## GET /api/manifests/{name}
- **Description**: Retrieve manifest YAML and metadata.
- **Response 200**:
```json
{
  "name": "repo-docs",
  "version": "v0",
  "yaml": "version: \"v0\"\nmetadata:\n  name: repo-docs\n...",
  "hash": "sha256:...",
  "updatedAt": "2026-02-18T00:00:00Z",
  "lastRunJobId": "uuid",
  "lastRunStatus": "failed"
}
```

## PUT /api/manifests/{name}
- **Description**: Upsert manifest definition (validates + stores hash).
- **Request Body**:
```json
{
  "version": "v0",
  "yaml": "version: \"v0\"\nmetadata:\n  name: repo-docs\n...",
  "allowOverwrite": false
}
```
- **Response 200**:
```json
{ "name": "repo-docs", "hash": "sha256:...", "updatedAt": "2026-02-18T00:00:00Z" }
```

## POST /api/manifests/{name}/runs
- **Description**: Submit a manifest ingestion run via Agent Queue.
- **Request Body**:
```json
{
  "action": "run",
  "options": { "dryRun": false, "forceFull": false, "maxDocs": null },
  "priority": "normal"
}
```
- **Response 202**:
```json
{ "jobId": "uuid", "type": "manifest" }
```

## POST /api/queue/jobs (manifest submission shortcut)
- **Request Body**:
```json
{
  "type": "manifest",
  "requiredCapabilities": ["manifest", "qdrant", "embeddings", "github"],
  "payload": {
    "manifest": {
      "name": "repo-docs",
      "action": "run",
      "source": { "kind": "inline", "content": "version: \"v0\" ..." },
      "options": { "dryRun": false, "forceFull": false, "maxDocs": null }
    }
  }
}
```
- **Response 202**:
```json
{ "jobId": "uuid" }
```

## SSE /api/queue/jobs/{jobId}/events
- **Description**: Streams `moonmind.manifest.*` stage events for manifest runs (validate → finalize) with counts/timings.
