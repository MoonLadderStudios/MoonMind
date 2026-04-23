# Contract: Report Artifact Contract

## Purpose

Define the runtime-visible contract for report deliverables so workflows, services, and consumers can publish, validate, and resolve reports without introducing a second storage system.

## Report Link Semantics

Supported report link types:
- `report.primary`
- `report.summary`
- `report.structured`
- `report.evidence`
- `report.appendix`
- `report.findings_index`
- `report.export`

Rules:
- `report.primary` is the canonical human-facing report for a scope.
- Other `report.*` link types represent related report content and do not replace the canonical primary report.
- Generic outputs (`output.primary`, `output.summary`, `output.agent_result`) remain non-report outputs unless a producer explicitly publishes report semantics.

## Metadata Contract

Allowed report metadata keys:
- `artifact_type`
- `report_type`
- `report_scope`
- `sensitivity`
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
- `scope`

Validation rules:
- Metadata must remain bounded and display-safe.
- Unknown keys are rejected.
- Secret-like keys or values are rejected.
- Oversized inline values are rejected.

## Report Bundle Result Contract

Required top-level key:
- `report_bundle_v = 1`

Allowed bundle keys:
- `primary_report_ref`
- `summary_ref`
- `structured_ref`
- `evidence_refs`
- `report_type`
- `report_scope`
- `sensitivity`
- `counts`

Compact artifact ref shape:

```json
{
  "artifact_ref_v": 1,
  "artifact_id": "art_..."
}
```

Rules:
- Bundle payloads contain compact refs and bounded metadata only.
- Inline report bodies, screenshots, logs, transcripts, raw URLs, and other large payloads are forbidden.
- Evidence remains separately addressable through `evidence_refs`.

## Canonical Report Resolution Contract

Resolution behavior:
- If `report.primary` exists for the scope, canonical report mode is `report`.
- If no `report.*` links exist but generic outputs exist, mode is `generic_fallback`.
- If `report.*` links exist without `report.primary`, mode is `invalid` for report-producing workflows.
- If neither report nor generic output links exist, mode is `none`.

Consumer rule:
- Clients must use server/link-driven report resolution and must not infer the canonical report from filenames, render hints, or arbitrary ordering.

## Workflow Mapping Contract

A report-producing workflow family declares:
- supported report link types
- supported observability link types
- recommended bounded metadata keys
- stable `report_type`

Rules:
- Curated report artifacts remain distinct from observability artifacts.
- Unsupported report or runtime link types fail fast.
- Generic fallback is allowed only when explicitly selected.

## Verification Targets

This contract is considered satisfied when tests prove:
- explicit `report.*` semantics are recognized
- generic outputs stay generic without explicit report links
- report bundle payloads remain compact and safe
- metadata validation blocks unsafe inputs
- canonical report resolution is server/link-driven
- report, evidence, and observability artifacts remain distinct
