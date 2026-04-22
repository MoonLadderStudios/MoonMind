# Contract: Report Workflow Rollout Helpers

## Supported Workflow Families

The runtime exposes deterministic mappings for:

- `unit_test`
- `coverage`
- `security_pentest`
- `benchmark`

Traceability: FR-001, FR-002, SC-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019.

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

Traceability: FR-003, FR-004, FR-005, SC-002, DESIGN-REQ-020.

## Classification Contract

`classify_report_rollout_artifacts(link_types)`:

- returns `mode = "report"` when `report.primary` is present
- returns `mode = "generic_fallback"` when only generic output links are present
- returns `mode = "invalid"` when report-like links exist without `report.primary`

Traceability: FR-006, SC-003, DESIGN-REQ-020.

## Projection Contract

`build_report_projection_summary(bundle, metadata=None)`:

- accepts compact report bundle refs
- emits optional `has_report`, `latest_report_ref`, `latest_report_summary_ref`, `report_type`, `report_status`, `finding_counts`, and `severity_counts`
- rejects inline content, raw URLs, evidence bodies, screenshots, transcripts, logs, and unknown storage identifiers
- does not read, write, or create artifacts

Traceability: FR-008, SC-004, DESIGN-REQ-022.

## Rollout Phase Contract

`REPORT_WORKFLOW_ROLLOUT_PHASES` preserves the MM-464 rollout order:

1. `metadata_conventions`
2. `report_links_and_ui_surfacing`
3. `compact_report_bundle_contract`
4. `optional_projections_filters_retention_pinning`

Traceability: FR-007, DESIGN-REQ-021.

## Jira Traceability Contract

The feature artifacts, runtime tests, and final verification preserve MM-464 for downstream pull request and verification evidence.

Traceability: FR-009, SC-005.
