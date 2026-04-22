# Contract: Report Workflow Rollout Helpers

## Supported Workflow Families

The runtime exposes deterministic mappings for:

- `unit_test`
- `coverage`
- `security_pentest`
- `benchmark`

Each mapping includes:

- `workflow_family`
- `report_type`
- `report_link_types`
- `observability_link_types`
- `recommended_metadata_keys`

## Validation Contract

`validate_report_workflow_artifact_classes(workflow_family, link_types, allow_generic_fallback=False)`:

- accepts supported report-family link sets containing `report.primary`
- rejects unknown workflow families
- rejects report-producing link sets missing `report.primary`
- permits generic fallback only when explicitly requested and only for generic output classes
- never treats `output.primary` as a canonical report

## Classification Contract

`classify_report_rollout_artifacts(link_types)`:

- returns `mode = "report"` when `report.primary` is present
- returns `mode = "generic_fallback"` when only generic output links are present
- returns `mode = "invalid"` when report-like links exist without `report.primary`

## Projection Contract

`build_report_projection_summary(bundle, metadata=None)`:

- accepts compact report bundle refs
- emits optional `has_report`, `latest_report_ref`, `latest_report_summary_ref`, `report_type`, `report_status`, `finding_counts`, and `severity_counts`
- rejects inline content, raw URLs, evidence bodies, screenshots, transcripts, logs, and unknown storage identifiers
- does not read, write, or create artifacts
