# Data Model: Report-Aware Execution Projections

## Entities

### Execution Detail Report Projection

Represents the bounded report-aware read model exposed on one execution detail response.

Fields:
- `hasReport`
- optional `latestReportRef`
- optional `latestReportSummaryRef`
- optional `reportType`
- optional `reportStatus`
- optional `findingCounts`
- optional `severityCounts`

Rules:
- All fields are derived server-side from canonical report artifacts and bounded report metadata.
- The projection contains compact artifact refs and bounded counts only.
- The projection is omitted or empty when no canonical report exists.
- The projection does not carry report bodies, raw artifact payloads, unrestricted URLs, or unbounded metadata.

### Canonical Latest Report Selection

Represents the server-side resolution of the current report for one execution.

Fields:
- execution identity: `namespace`, `workflowId`, `runId`
- canonical latest report linkage from report artifact semantics
- optional summary artifact linkage
- optional bounded count metadata

Rules:
- Latest selection is link-driven and server-defined.
- Clients must not infer recency from artifact ordering, filenames, or local heuristics.
- Selection reuses canonical report bundle/report artifact semantics already defined by the report contract stories.

### Bounded Count Summary

Represents the optional count maps included in the execution-detail projection.

Fields:
- `findingCounts`
- `severityCounts`

Rules:
- Only bounded mapping shapes are allowed.
- Unsupported keys or oversized nested values are rejected before surfacing through execution detail.
- Counts remain convenience summaries over artifact-backed report data rather than a second source of truth.

### Deferred Report Endpoint Decision

Represents the explicit feature-local choice to defer the dedicated report endpoint.

Fields:
- `implemented_now` = false
- `reason` = execution-detail summary fields are the bounded first slice
- traceability to MM-496 and source design open question

Rules:
- The decision must be recorded in plan/tasks/verification artifacts.
- Deferral does not block future introduction of a dedicated endpoint in a separate story.
- The execution-detail projection remains valid even while the endpoint is deferred.

## Relationships

- One `Canonical Latest Report Selection` may populate one `Execution Detail Report Projection` for a given execution.
- One `Execution Detail Report Projection` may include zero or one bounded `Bounded Count Summary` maps for findings and severities.
- The `Deferred Report Endpoint Decision` constrains this feature to execution detail exposure and prevents scope creep into a second API route.

## Validation Notes

- Artifact refs surfaced through execution detail remain individually addressable only through the existing artifact APIs.
- Execution detail never becomes the durable storage location for report content.
- The projection must degrade safely when a canonical report is absent or when bounded count metadata is not available.
