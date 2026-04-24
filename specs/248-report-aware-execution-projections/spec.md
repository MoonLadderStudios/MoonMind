# Feature Specification: Report-Aware Execution Projections

**Feature Branch**: `248-report-aware-execution-projections`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-496 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-496-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: existing report-related feature directories in `specs/` map to different Jira stories (`MM-492`, `MM-493`, `MM-494`), so no active Moon Spec feature directory matched MM-496 and `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-496 MoonSpec Orchestration Input

## Source

- Jira issue: MM-496
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose report-aware execution projections
- Labels:
  - `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-496 from MM project
Summary: Expose report-aware execution projections
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-496 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-496: Expose report-aware execution projections

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections:
  - 11.4 Latest report semantics
  - 18 Suggested API/UI extensions
  - 20 Open questions
- Coverage IDs:
  - DESIGN-REQ-013
  - DESIGN-REQ-022
  - DESIGN-REQ-024

User Story
As an API consumer, I want execution detail responses and an optional report projection to expose the latest report refs and bounded counts so clients can build report-first views without duplicating artifact-selection heuristics.

Acceptance Criteria
- Execution detail data can expose `has_report`, `latest_report_ref`, `latest_report_summary_ref`, `report_type`, `report_status`, and bounded finding/severity counts.
- A report projection endpoint, if implemented in this story, returns `execution_ref`, `primary_report_ref`, `summary_ref`, `structured_ref`, `evidence_refs`, `report_type`, and bounded counts over standard artifacts.
- Projection output never becomes a second report storage model; underlying artifacts remain individually addressable.
- Latest report selection is resolved server-side using execution identity and report link types.
- Restricted artifacts still obey artifact authorization and preview/default-read behavior.
- The story records whether the projection endpoint is implemented immediately or deferred in favor of execution-detail summary fields.

Requirements
- Provide report-aware server selection for clients.
- Keep projection data as a convenience read model over artifacts.
- Make unresolved projection timing explicit.

Relevant Implementation Notes
- Preserve MM-496 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Artifacts/ReportArtifacts.md` as the source design reference for latest-report semantics, projection-friendly API/UI extensions, and the open question around when projection behavior should ship.
- Add report-aware execution detail fields that expose the latest canonical report refs and bounded counts without requiring clients to re-run artifact selection heuristics.
- If a projection endpoint is implemented in this story, keep it as a convenience read model over standard artifacts rather than a second report storage model.
- Resolve latest report selection server-side from execution identity and report link types.
- Keep restricted-artifact authorization and preview/default-read behavior intact for any report projection or execution-detail exposure.
- Make the implementation decision explicit if the projection endpoint is deferred in favor of execution-detail summary fields.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-496 is blocked by MM-497, whose embedded status is Selected for Development.
- Trusted Jira link metadata at fetch time shows MM-496 is related to MM-495 via a Blocks link where MM-496 blocks MM-495; MM-495 is not a blocker for this story.

Validation
- Verify execution detail responses expose `has_report`, `latest_report_ref`, `latest_report_summary_ref`, `report_type`, `report_status`, and bounded finding/severity counts when canonical reports exist.
- Verify any projection endpoint added by this story returns `execution_ref`, `primary_report_ref`, `summary_ref`, `structured_ref`, `evidence_refs`, `report_type`, and bounded counts over standard artifacts.
- Verify projection output remains a convenience read model and does not become a second report storage model.
- Verify latest report selection is resolved server-side from execution identity and report link types.
- Verify restricted artifacts continue to obey artifact authorization and preview/default-read behavior.
- Verify the implementation records whether projection behavior ships now or is deferred in favor of execution-detail summary fields.

Needs Clarification
- None
```

## User Story - Expose Report-Aware Execution Detail Fields

**Summary**: As an API consumer, I want execution detail responses to expose canonical report refs and bounded report summary counts so clients can build report-first behavior without artifact-guessing heuristics.

**Goal**: The `/api/executions/{workflowId}` response surfaces a bounded report projection derived server-side from canonical report artifacts, while the dedicated report endpoint remains explicitly deferred unless implementation work demonstrates it is needed now.

**Independent Test**: Request execution detail for a run with canonical report artifacts and verify the response includes a report-aware projection with canonical refs and bounded counts, resolves latest report selection on the server, preserves authorization-safe artifact refs only, and continues to degrade cleanly when no report exists.

**Acceptance Scenarios**:

1. **Given** an execution has a canonical latest report bundle, **When** a client fetches `/api/executions/{workflowId}`, **Then** the execution detail response includes report-aware summary data for `hasReport`, `latestReportRef`, `latestReportSummaryRef`, `reportType`, `reportStatus`, and bounded count fields.
2. **Given** the latest canonical report is derived from report link semantics, **When** execution detail is materialized, **Then** latest report selection is resolved server-side from execution identity and report link types rather than browser-side artifact sorting.
3. **Given** a report projection is exposed through execution detail, **When** it references report content, **Then** it contains compact artifact refs and bounded counts only and does not become a second report storage model.
4. **Given** the latest canonical report or summary artifact is restricted, **When** execution detail is returned, **Then** projection data continues to honor artifact authorization and preview/default-read behavior rather than bypassing access rules.
5. **Given** an execution has no canonical report bundle, **When** execution detail is requested, **Then** the report-aware projection degrades safely without fabricated refs or counts.
6. **Given** the story leaves the dedicated report endpoint deferred, **When** planning and verification artifacts are reviewed, **Then** they make that defer-now decision explicit rather than leaving the projection shape ambiguous.

### Edge Cases

- An execution publishes a canonical primary report but no summary artifact.
- The latest report bundle includes finding counts but no severity counts, or severity counts but no finding counts.
- An execution has report artifacts, but no canonical `report.primary` for the relevant scope.
- Projection metadata includes unsupported keys or oversized values that would violate bounded report metadata rules.
- Clients attempt to infer report recency from artifact ordering instead of the server projection.

- Breakdown decision: `moonspec-breakdown` was not run because the MM-496 Jira preset brief already defines one independently testable runtime story and does not require processing multiple specs.

## Assumptions

- MM-492 established the canonical report artifact contract and MM-493 established immutable report bundle publication, so MM-496 can reuse existing helper logic rather than inventing a new projection model.
- MM-494 already covers Mission Control report-first presentation; MM-496 focuses on the execution detail API read model that can support similar consumers.
- The dedicated report projection endpoint remains optional future work; this story's bounded first slice is execution-detail summary exposure unless implementation evidence proves the endpoint is required immediately.
- MM-497 remains an upstream Jira blocker for implementation sequencing, but it does not prevent preserving MM-496 as a complete MoonSpec artifact set.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-013 | `docs/Artifacts/ReportArtifacts.md` §11.4, §18.1 | Latest report selection and execution detail summary fields must be resolved server-side from canonical report semantics and bounded artifact-backed refs. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-022 | `docs/Artifacts/ReportArtifacts.md` §18.1-§18.2 | Execution detail summary fields are the first recommended convenience surface; any dedicated report endpoint is optional and its status must be explicit. | In scope | FR-001, FR-005 |
| DESIGN-REQ-024 | `docs/Artifacts/ReportArtifacts.md` §18.2, §21 | Projection output remains a convenience read model over standard artifacts and must not become a second report storage system or bypass artifact authorization behavior. | In scope | FR-003, FR-004 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose an execution-detail report projection for `/api/executions/{workflowId}` containing `hasReport`, `latestReportRef`, `latestReportSummaryRef`, `reportType`, `reportStatus`, and bounded `findingCounts` / `severityCounts` when canonical report data exists.
- **FR-002**: The system MUST derive the execution-detail report projection server-side from execution identity and canonical report link semantics rather than client-side artifact ordering or filename heuristics.
- **FR-003**: The system MUST keep the execution-detail report projection bounded to compact artifact refs and bounded counts, and MUST NOT introduce a second report storage model.
- **FR-004**: The system MUST preserve artifact authorization and preview/default-read behavior for any report refs surfaced through execution detail.
- **FR-005**: The system MUST explicitly record whether the dedicated report projection endpoint is implemented now or deferred in favor of execution-detail summary fields; silent ambiguity is not allowed.
- **FR-006**: The system MUST degrade safely when no canonical report exists by returning no fabricated latest-report refs or bounded counts.
- **FR-007**: The system MUST reject or omit unsupported report projection metadata keys or unsafe oversized values instead of surfacing them through execution detail.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this story MUST preserve Jira issue key MM-496.

### Key Entities

- **Execution Detail Report Projection**: The bounded report-aware read model exposed on execution detail responses for one execution.
- **Canonical Latest Report Selection**: The server-defined resolution of the current canonical report for an execution using report link semantics.
- **Bounded Count Summary**: The optional `findingCounts` and `severityCounts` maps derived from validated report metadata.
- **Deferred Report Endpoint Decision**: The explicit feature-local decision that the dedicated report endpoint is either implemented in this story or deferred in favor of execution-detail summary fields.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove execution detail responses surface the bounded report projection when canonical report data exists and omit fabricated refs when it does not.
- **SC-002**: Tests prove latest report selection is resolved server-side from canonical report link semantics rather than browser-side sorting heuristics.
- **SC-003**: Tests prove only compact artifact refs and bounded count metadata appear in the projection and that unsupported metadata is rejected or omitted safely.
- **SC-004**: Tests prove report refs returned by execution detail continue to obey artifact authorization and preview/default-read behavior.
- **SC-005**: Traceability review confirms MM-496 and DESIGN-REQ-013, DESIGN-REQ-022, and DESIGN-REQ-024 remain preserved in MoonSpec artifacts and downstream implementation evidence.
- **SC-006**: Planning and verification artifacts explicitly record that the dedicated report endpoint is deferred in favor of execution-detail summary fields unless implementation work proves otherwise.
