# Data Model: Report Artifact Contract

## Entities

### Report Artifact Link Type

Represents the semantic role of an artifact in a report-producing workflow.

Fields:
- `link_type`: stable string enum

Allowed values:
- `report.primary`
- `report.summary`
- `report.structured`
- `report.evidence`
- `report.appendix`
- `report.findings_index`
- `report.export`

Rules:
- `report.*` values are explicit report deliverable semantics.
- Generic output link types such as `output.primary`, `output.summary`, and `output.agent_result` remain non-report outputs unless a producer explicitly publishes `report.*` semantics.

### Report Artifact Metadata

Bounded display and classification metadata attached to a report artifact.

Fields:
- `artifact_type`: stable report artifact family identifier
- `report_type`: stable report family identifier
- `report_scope`: `final | intermediate | step | executive | technical`
- `sensitivity`: bounded display-safety classification
- `title`: human-facing title
- `description`: bounded display description
- `producer`: workflow/tool identity
- `subject`: bounded target description
- `render_hint`: display hint
- `name`: suggested filename/basename
- `is_final_report`: boolean final-report marker
- `finding_counts`: bounded counts map
- `severity_counts`: bounded counts map
- `counts`: bounded counts map
- `step_id`: optional step-aware hint
- `attempt`: optional attempt hint
- `scope`: optional bounded scope hint

Validation rules:
- Only standardized keys are allowed.
- Values must remain bounded in size, depth, and collection length.
- Secret-like keys and values are rejected.
- Large inline bodies, credentials, cookies, session tokens, raw grants, and similar unsafe values are forbidden.

### Compact Artifact Ref

Minimal workflow-safe reference to an artifact.

Fields:
- `artifact_ref_v`: integer contract version
- `artifact_id`: immutable artifact identifier

Validation rules:
- `artifact_ref_v` must be present and valid for the compact ref contract.
- `artifact_id` is required.
- Workflow-facing report bundle data uses refs instead of inline artifact payloads.

### Report Bundle Result

Compact workflow-facing bundle describing the canonical report and related report artifacts for one scope.

Fields:
- `report_bundle_v`: bundle contract version; current value `1`
- `primary_report_ref`: compact ref for the canonical human-facing report
- `summary_ref`: optional compact ref for summary artifact
- `structured_ref`: optional compact ref for machine-readable results
- `evidence_refs`: list of compact refs for evidence artifacts
- `report_type`: stable report family identifier
- `report_scope`: bounded report scope string
- `sensitivity`: bounded sensitivity string
- `counts`: bounded counts map

Validation rules:
- Only allowed keys are accepted.
- Large inline report/evidence/log/transcript bodies are forbidden.
- `evidence_refs` must be a list of compact refs.
- Bundle payloads must remain workflow-safe and bounded.

### Report Workflow Mapping

Deterministic runtime mapping for a report-producing workflow family.

Fields:
- `workflow_family`: normalized workflow family key
- `report_type`: stable report family identifier
- `report_link_types`: tuple of allowed report link types for that family
- `observability_link_types`: tuple of allowed runtime/diagnostic link types for that family
- `recommended_metadata_keys`: tuple of bounded metadata keys expected for that family

Purpose:
- Distinguishes curated report artifacts from observability artifacts.
- Allows report-producing workflow validation and generic-output fallback behavior.

### Canonical Report Resolution

Server-driven resolution of the latest canonical report for an execution scope.

Fields:
- `has_canonical_report`: boolean
- `report_link_types`: current report link types seen for a scope
- `generic_output_link_types`: current generic output link types seen for a scope
- `mode`: `report | generic_fallback | invalid | none`

Rules:
- `report.primary` establishes canonical report mode.
- Generic output without report links is `generic_fallback`.
- Report links without `report.primary` are invalid for report-producing workflows.
- Clients must not guess canonical report identity from filenames or local heuristics.

## Relationships

- A `Report Bundle Result` references one canonical `Report Artifact Link Type` through `primary_report_ref` and may reference related summary, structured, and evidence artifacts.
- `Report Artifact Metadata` is attached to each report artifact and remains bounded for control-plane display.
- A `Report Workflow Mapping` constrains which report and observability link types are valid for one workflow family.
- `Canonical Report Resolution` consumes artifact link types to determine whether a scope has a canonical report, generic fallback, invalid report output, or no report content.

## State and Validation Notes

- Report artifacts remain immutable; newer reports produce new artifact IDs.
- Canonical “latest report” is query behavior over existing artifact linkage, not mutable in-place state.
- Evidence artifacts remain separately addressable and do not collapse into one opaque report body.
- Observability artifacts remain outside the report contract even when they are related to the same workflow.
