# UI Contract: Show Recent Manifest Runs

## Route

- `GET /tasks/manifests`

## Data Boundary

The Manifests page must request recent manifest runs through:

```text
GET {apiBase}/executions?entry=manifest&limit=200
```

The frontend accepts the existing execution-list response shape:

```json
{
  "items": [
    {
      "taskId": "mm:manifest-123",
      "source": "temporal",
      "sourceLabel": "Temporal",
      "title": "Nightly docs",
      "manifestName": "nightly-docs",
      "action": "run",
      "status": "running",
      "currentStage": "fetch",
      "startedAt": "2026-04-21T12:00:00Z",
      "durationSeconds": 42,
      "detailHref": "/tasks/mm:manifest-123?source=temporal"
    }
  ]
}
```

Optional fields may be absent. The UI must render fallback values rather than failing parsing or hiding the row.

## Required UI Surface

- Run Manifest card appears before Recent Runs.
- Recent Runs includes lightweight filters:
  - Status
  - Manifest
  - Search
- Recent Runs displays:
  - Run ID/details link
  - Manifest label
  - Action
  - Status with stage detail when available
  - Started
  - Duration
  - View details action
- Empty state text:
  - `No manifest runs exist yet. Run a registry manifest or submit inline YAML above.`

## Accessibility Contract

- Filters have visible labels.
- Run ID and View details links have accessible names that include the run ID.
- Row action links remain keyboard reachable.
