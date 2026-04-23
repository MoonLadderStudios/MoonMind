# Data Model: Publish Report Bundles

## Entities

### Immutable Report Bundle Publication

Represents one activity-owned publication event for a report-producing workflow scope.

Fields:
- `namespace`
- `workflow_id`
- `run_id`
- optional `step_id`
- optional `attempt`
- `report_scope`
- `report_type`
- optional `sensitivity`
- optional bounded `counts`

Rules:
- Publication happens through activity or service boundaries, not workflow code.
- Execution identity is attached through artifact links and bounded metadata.
- Later publications create new artifacts instead of mutating previously published ones.

### Workflow-Safe Report Bundle State

Compact workflow-visible result returned after report publication.

Fields:
- `report_bundle_v`
- `primary_report_ref`
- optional `summary_ref`
- optional `structured_ref`
- `evidence_refs`
- `report_type`
- `report_scope`
- optional `sensitivity`
- optional bounded `counts`

Validation rules:
- `report_bundle_v` is exactly `1`.
- Only compact artifact refs and bounded metadata are allowed.
- Inline report bodies, screenshots, findings, logs, transcripts, raw URLs, and oversized payloads are forbidden.
- Workflow-visible bundle state remains compact enough for workflow history and persisted workflow state.

### Canonical Final Report

The single primary report artifact designated as final for one scope.

Fields:
- `link_type = report.primary`
- `metadata.is_final_report = true`
- `metadata.report_scope = final`
- artifact ref / artifact ID

Rules:
- A final scope has exactly one canonical final report.
- Final report identification is explicit, not inferred from names or ordering.
- Final report artifacts remain immutable after publication.

### Step-Scoped Report Linkage

Bounded linkage metadata that identifies step-level or attempt-level reports.

Fields:
- `step_id`
- `attempt`
- optional bounded `scope`
- execution identity fields used for artifact links

Rules:
- Step metadata is bounded and stored as metadata or link context, not as report content.
- Step-scoped reports remain separately addressable through artifact refs.

### Latest Report Resolution

Server-side resolution of the current report for an execution or step scope.

Fields:
- `link_type`
- `latest_only`
- resolved latest primary report artifact ref
- optional related summary or evidence artifact refs

Rules:
- Resolution is link-driven and server-defined.
- Clients must not infer latest-report identity through browser-side sorting, filenames, or local heuristics.
- Intermediate reports may coexist with final reports while the server still resolves the current canonical report deterministically.

## Relationships

- One `Immutable Report Bundle Publication` creates one `Workflow-Safe Report Bundle State` and one or more report artifacts.
- `Workflow-Safe Report Bundle State` references the `Canonical Final Report` when the scope is final.
- `Step-Scoped Report Linkage` attaches bounded step metadata to published report artifacts when applicable.
- `Latest Report Resolution` queries artifact linkage for the current canonical report without mutating prior artifacts.

## Validation Notes

- Artifact IDs are immutable and remain individually addressable.
- Evidence, screenshots, logs, transcripts, and structured findings remain artifact-backed rather than embedded in bundle state.
- The same publication contract can be used by multiple workflow families as long as each produces compact refs and bounded metadata.
