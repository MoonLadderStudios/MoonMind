# Contract: Execution Report Projection

## Purpose

Define the bounded MM-496 execution-detail report projection contract so `/api/executions/{workflowId}` can expose canonical report summary data without introducing a dedicated report storage model or bypassing artifact authorization.

## Response Shape

`ExecutionModel` exposes an optional report projection object with the following fields:

```json
{
  "reportProjection": {
    "hasReport": true,
    "latestReportRef": {"artifact_ref_v": 1, "artifact_id": "art_primary"},
    "latestReportSummaryRef": {"artifact_ref_v": 1, "artifact_id": "art_summary"},
    "reportType": "security_pentest_report",
    "reportStatus": "final",
    "findingCounts": {"total": 8},
    "severityCounts": {"critical": 1, "high": 2}
  }
}
```

Rules:
- `reportProjection` is optional and may be absent or null when no canonical report exists.
- `hasReport` is required when `reportProjection` is present.
- `latestReportRef` and `latestReportSummaryRef` are compact artifact refs only.
- `findingCounts` and `severityCounts` are optional bounded maps only.

## Derivation Rules

- The projection is derived server-side from canonical report artifact semantics for the execution.
- Latest report resolution is link-driven and server-defined.
- Projection shaping reuses the validated report projection helper rather than duplicating logic in the API layer.

## Safety Rules

- No raw report bodies, report markdown, screenshots, logs, transcripts, unrestricted URLs, or presigned downloads appear in `reportProjection`.
- Unsupported projection metadata keys are rejected or omitted before surfacing through execution detail.
- Artifact authorization and preview/default-read behavior remain owned by the artifact APIs.
- `reportProjection` is a convenience read model, not a second storage system.

## Deferred Scope

The dedicated `/report` endpoint is explicitly deferred in MM-496.

Rules:
- This story adds execution-detail summary exposure only.
- Any future `/report` endpoint must be delivered by a separate story with its own contract and tests.

## Verification Targets

This contract is satisfied when tests prove:
- execution detail surfaces bounded report projection data when canonical report artifacts exist
- execution detail omits fabricated refs when no canonical report exists
- latest report resolution remains server-defined
- only compact artifact refs and bounded counts are exposed
- the dedicated `/report` endpoint remains deferred and unimplemented in this story
