# Data Model: Report Artifact Contract

## Report Link Type

Stable string values used in existing `TemporalArtifactLink.link_type` records:

- `report.primary`
- `report.summary`
- `report.structured`
- `report.evidence`
- `report.appendix`
- `report.findings_index`
- `report.export`

Validation rules:

- Values beginning with `report.` must be one of the supported values.
- Non-report link types remain governed by the existing artifact system.
- `output.primary`, `output.summary`, and `output.agent_result` remain valid generic output link types and are not report aliases.

## Report Metadata

Report metadata is stored in the existing artifact `metadata_json` payload.

Allowed report metadata keys:

- `artifact_type`
- `report_type`
- `report_scope`
- `title`
- `description`
- `producer`
- `subject`
- `render_hint`
- `name`
- `is_final_report`
- `finding_counts`
- `severity_counts`
- `counts`
- `step_id`
- `attempt`

Validation rules:

- Unknown report metadata keys are rejected.
- Secret-like keys and values are rejected.
- Raw access grant, cookie, session token, and credential fields are rejected.
- Large inline string values are rejected.
- Nested lists and objects are accepted only when they remain compact and contain safe scalar values.

## State Transitions

Report artifact lifecycle uses existing artifact states:

1. Create pending artifact with optional report link and bounded metadata.
2. Complete upload through existing artifact completion.
3. Link an existing artifact with a report link type after validating its current metadata.
4. List or fetch latest report artifact through existing execution link queries.

No new report-specific persistence state is introduced.
