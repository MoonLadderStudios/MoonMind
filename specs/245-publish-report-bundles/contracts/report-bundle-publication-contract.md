# Contract: Report Bundle Publication

## Purpose

Define the runtime-visible contract for MM-493 report bundle publication so report-producing workflows publish immutable artifact-backed reports, expose compact workflow-safe refs, and resolve canonical latest reports through server-defined behavior.

## Publication Boundary

Report bundles are published through an activity or service boundary that receives:
- execution identity: `namespace`, `workflow_id`, `run_id`
- optional bounded step metadata: `step_id`, `attempt`, optional `scope`
- report metadata: `report_type`, `report_scope`, optional `sensitivity`, optional bounded `counts`
- component payloads for primary report and optional summary, structured, and evidence artifacts

Rules:
- Workflow code does not assemble or write report artifacts directly.
- Publication writes artifact-backed report components and returns compact refs only.

## Workflow-Safe Output Shape

```json
{
  "report_bundle_v": 1,
  "primary_report_ref": {"artifact_ref_v": 1, "artifact_id": "art_..."},
  "summary_ref": {"artifact_ref_v": 1, "artifact_id": "art_..."},
  "structured_ref": {"artifact_ref_v": 1, "artifact_id": "art_..."},
  "evidence_refs": [
    {"artifact_ref_v": 1, "artifact_id": "art_..."}
  ],
  "report_type": "unit_test_report",
  "report_scope": "final",
  "sensitivity": "restricted",
  "counts": {"total": 3}
}
```

Rules:
- `report_bundle_v` is exactly `1`.
- Workflow-visible output contains artifact refs and bounded metadata only.
- Inline report bodies, screenshots, findings payloads, logs, transcripts, raw download URLs, and oversized values are invalid.

## Final Report Invariants

- A final scope publishes exactly one canonical `report.primary` artifact.
- The canonical final report carries `metadata.is_final_report = true` and `metadata.report_scope = final`.
- Intermediate and final report artifacts coexist by publishing new immutable artifacts rather than mutating older artifacts.

## Execution And Step Linkage

Each published report artifact preserves:
- `namespace`
- `workflow_id`
- `run_id`
- `link_type`
- optional `label`
- optional bounded `step_id`
- optional bounded `attempt`

Rules:
- Execution and step context remain bounded metadata or link context, not embedded report content.
- Step-level and execution-level reports remain individually addressable through the artifact APIs.

## Latest Report Resolution

Resolution behavior:
- Clients query canonical reports through server-defined latest `report.primary` behavior.
- Browser-side sorting, filename heuristics, or arbitrary ordering are not valid sources of truth.
- The latest canonical report can be resolved while intermediate reports remain preserved and individually addressable.

## Verification Targets

This contract is satisfied when tests prove:
- publication happens through the activity/service boundary
- workflow-visible bundle state remains compact and bounded
- execution and step linkage are preserved
- final scopes expose exactly one canonical final report
- intermediate and final report artifacts coexist without mutation
- latest report resolution is server-defined and link-driven
