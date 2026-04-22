# Data Model: Report Bundle Workflow Publishing

## ReportBundleResult

Compact workflow-facing result returned by report-producing activities.

Fields:
- `report_bundle_v`: integer version, currently `1`.
- `primary_report_ref`: artifact ref for the canonical primary report when present.
- `summary_ref`: artifact ref for the summary artifact when present.
- `structured_ref`: artifact ref for structured results when present.
- `evidence_refs`: ordered artifact refs for separately addressable evidence.
- `report_type`: bounded report family identifier.
- `report_scope`: bounded scope such as `final`, `step`, or `intermediate`.
- `sensitivity`: bounded sensitivity label when supplied.
- `counts`: bounded counts for display and filtering.

Validation:
- Result contains refs and bounded metadata only.
- Result never contains report body text, evidence blobs, logs, screenshots, transcripts, raw download URLs, or large finding details.
- Final bundles identify exactly one primary final report.

## ReportBundleComponent

Activity input describing one artifact-backed report component to write.

Fields:
- `role`: primary, summary, structured, evidence, appendix, findings_index, or export.
- `payload`: bytes or string written to artifact storage.
- `content_type`: artifact content type.
- `label`: optional execution link label.
- `metadata`: bounded report metadata.

Relationships:
- Each component produces one existing temporal artifact.
- Each artifact is linked to the producing execution with a `report.*` link type.
- Evidence components populate `evidence_refs`; primary/summary/structured components populate their dedicated refs.

## ReportBundleExecutionContext

Bounded execution identity used for artifact links.

Fields:
- `namespace`
- `workflow_id`
- `run_id`
- optional `step_id`
- optional `attempt`
- optional `scope`

Validation:
- Namespace, workflow_id, and run_id are required for publication.
- Step metadata is copied only as bounded metadata, never as report content.

## State Transitions

1. Activity receives report component payloads and execution context.
2. Activity creates each report artifact with report metadata and execution link.
3. Activity completes each artifact write.
4. Activity returns `ReportBundleResult` to workflow code.
5. Workflow persists or returns only the compact bundle result.
