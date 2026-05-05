# Contract: Executions List and Facets

## List Request

`GET /api/executions?source=temporal&pageSize=<n>&scope=tasks&sort=<field>&sortDir=<asc|desc>`

Supported canonical filter parameters:
- `stateIn`, `stateNotIn`
- `targetRuntimeIn`, `targetRuntimeNotIn`
- `targetSkillIn`, `targetSkillNotIn`
- `repoIn`, `repoNotIn`, `repoExact`, `repoContains`
- `taskId`, `taskIdContains`, `titleContains`
- `scheduledFrom`, `scheduledTo`, `scheduledBlank`
- `createdFrom`, `createdTo`
- `finishedFrom`, `finishedTo`, `finishedBlank`

Response shape extends the existing execution list response:

```json
{
  "items": [],
  "nextPageToken": null,
  "count": 0,
  "countMode": "exact",
  "uiQueryModel": "compatibility_adapter",
  "staleState": false,
  "degradedCount": false,
  "refreshedAt": "2026-05-05T00:00:00Z"
}
```

Validation failures return HTTP 422:

```json
{
  "detail": {
    "code": "invalid_execution_query",
    "message": "Cannot combine stateIn and stateNotIn."
  }
}
```

## Facet Request

`GET /api/executions/facets?source=temporal&facet=targetRuntime&pageSize=50&stateIn=executing,completed`

Supported facets:
- `status`
- `targetRuntime`
- `targetSkill`
- `repository`
- `integration`

The facet request accepts the same active filter parameters as the list request. The requested facet's own filter is excluded from the facet universe by default; all other filters remain active.

Response:

```json
{
  "facet": "targetRuntime",
  "items": [
    { "value": "codex_cli", "label": "Codex CLI", "count": 18 }
  ],
  "blankCount": 2,
  "countMode": "exact",
  "truncated": false,
  "nextPageToken": null,
  "source": "authoritative"
}
```

Validation failures return HTTP 422 with `invalid_execution_query` and a safe message. Temporal outages return HTTP 503 with `temporal_unavailable`.

## Security Requirements

- Normal list and facet queries are always task-run scoped for non-admin users.
- Non-admin users cannot request another owner or non-user owner type.
- Facet values and counts are derived from the same authorization-constrained universe as rows.
- Values are treated as text and never trusted HTML by the client.
