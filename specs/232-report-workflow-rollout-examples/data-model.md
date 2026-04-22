# Data Model: Report Workflow Rollout and Examples

## ReportWorkflowMapping

- `workflow_family`: stable identifier such as `unit_test`, `coverage`, `security_pentest`, or `benchmark`.
- `report_type`: bounded report type metadata value producers can attach to report artifacts.
- `report_link_types`: curated report classes expected for the workflow family.
- `observability_link_types`: runtime or diagnostic artifact classes expected to remain separate from curated reports.
- `recommended_metadata_keys`: bounded metadata keys producers should use for the workflow family.

Validation rules:
- `report.primary` is required for every supported report-producing workflow family.
- Every `report_link_types` value must be a supported report artifact link type.
- Observability classes must not start with `report.`.
- Metadata keys must be allowed report metadata keys.

## ReportRolloutClassification

- `has_canonical_report`: true when `report.primary` is present.
- `mode`: `report`, `generic_fallback`, or `invalid`.
- `generic_output_link_types`: generic output links present in the artifact set.
- `report_link_types`: report links present in the artifact set.

Validation rules:
- Generic output-only artifacts classify as `generic_fallback`.
- Report-producing validation fails when report links exist without `report.primary` and generic fallback is not explicitly allowed.
- `output.primary`, `output.summary`, and `output.agent_result` remain generic outputs.

## ReportProjectionSummary

- `has_report`: whether a canonical report ref exists.
- `latest_report_ref`: compact primary report artifact ref when present.
- `latest_report_summary_ref`: compact summary artifact ref when present.
- `report_type`: bounded metadata value.
- `report_status`: bounded report scope/status value.
- `finding_counts` / `severity_counts`: bounded counts copied from validated metadata.

Validation rules:
- Projection summaries are derived only from compact refs and bounded scalar/count metadata.
- Inline report bodies, evidence payloads, logs, screenshots, transcripts, raw URLs, and unknown storage identifiers are rejected.
- Projection summaries are read models over normal artifacts and do not represent separate storage.
