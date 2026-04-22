# Contract: Mission Control Report Presentation

## Latest Primary Report Query

Mission Control identifies a canonical final report through the existing execution artifact endpoint:

```http
GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts?link_type=report.primary&latest_only=true
```

Expected behavior:

- Returns zero or one artifact in `artifacts`.
- Selection is performed server-side by execution identity and `link_type=report.primary`.
- Clients must not sort arbitrary artifact lists to infer the canonical report.

## Related Report Content Query

Mission Control may use the existing execution artifact list:

```http
GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts
```

Expected behavior:

- Artifact rows include `links`, `metadata`, `content_type`, `download_url`, and `default_read_ref`.
- Rows with links `report.summary`, `report.structured`, and `report.evidence` are displayed as related report content.
- The same rows remain available through the generic artifact list.

## Artifact Presentation Fields

Report open behavior uses artifact metadata in this order:

1. `default_read_ref.artifact_id`
2. `download_url`
3. `artifact_id`

Viewer labeling uses:

- `metadata.render_hint`
- `content_type`
- `metadata.name`
- `metadata.title`

Required content behavior:

- markdown content may be labeled as markdown
- JSON content may be labeled as JSON
- plain text may be labeled as text
- diff content may be labeled as diff
- images may be labeled as image
- PDF and unknown binary content may be labeled as download/open

## Fallback Contract

When the latest primary report query returns no artifact:

- Mission Control does not render a report status.
- Mission Control does not infer a report from generic artifacts.
- The normal artifact list and observability surfaces remain visible.
