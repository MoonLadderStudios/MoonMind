# Feature Specification: Mission Control Report Presentation

**Feature Branch**: `229-mission-control-report-presentation`
**Created**: 2026-04-22
**Status**: Draft
**Input**:

```text
Jira issue: MM-462 from MM project
Summary: Mission Control Report Presentation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-462 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-462: Mission Control Report Presentation

Short Name
mission-control-report-presentation

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 11.4 Latest report semantics, 12. Presentation rules, 12.1 Primary UI surfaces, 12.2 Default read behavior, 12.3 Recommended renderer behavior, 12.4 Report-first UX rule, 12.5 Evidence presentation, 18. Suggested API/UI extensions
- Coverage IDs: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020, DESIGN-REQ-022

User Story
As an operator, I can open an execution with a final report and see the canonical report, related evidence, and normal observability surfaces without guessing which generic artifact matters.

Acceptance Criteria
- Given an execution has a canonical `report.primary` artifact, then Mission Control shows a report panel or top-level report card before requiring inspection of the generic artifact list.
- Given linked `report.summary`, `report.structured`, or `report.evidence` artifacts exist, then they are shown as related report content and remain individually openable where access permits.
- Given no `report.primary` artifact exists, then the UI falls back to the normal artifact list without fabricating report status from local heuristics.
- Viewer selection uses `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`, including appropriate markdown, JSON, text, diff, image, PDF, and binary handling.
- Latest report selection comes from server query behavior or a projection field, not browser-side sorting of arbitrary artifacts.

Requirements
- Expose report-first presentation for canonical final reports in execution detail surfaces.
- Display related report summary, structured, and evidence artifacts separately from generic artifacts and observability surfaces.
- Use artifact presentation contract fields for read target and renderer selection.
- Treat optional execution convenience fields and any report projection endpoint as read models over normal artifacts.

Relevant Implementation Notes
- Keep report presentation layered on the existing artifact presentation contract and execution detail surfaces.
- Prioritize canonical final reports linked as `report.primary` before generic artifact list inspection.
- Present `report.summary`, `report.structured`, and `report.evidence` artifacts as related report content while preserving individual artifact access controls and open/read behavior.
- Preserve the normal artifact list and observability surfaces; report presentation must not hide logs, diagnostics, provider snapshots, or other non-report artifacts.
- Use server-provided latest report query behavior or projection fields for latest report selection instead of browser-side sorting over arbitrary artifacts.
- Choose viewers from artifact presentation fields, including `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`.

Non-Goals
- Creating report status from local UI heuristics when no `report.primary` artifact exists.
- Replacing generic artifact list behavior for executions without canonical reports.
- Reworking artifact storage, authorization, lifecycle, or preview contracts.
- Treating observability outputs such as stdout, stderr, diagnostics, provider snapshots, or session continuity artifacts as canonical report content.

Validation
- Verify an execution with a canonical `report.primary` artifact shows report-first presentation in Mission Control before generic artifact list inspection.
- Verify linked `report.summary`, `report.structured`, and `report.evidence` artifacts appear as related report content and remain individually openable where access permits.
- Verify executions without `report.primary` fall back to the normal artifact list without fabricated report status.
- Verify viewer selection honors `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title` for markdown, JSON, text, diff, image, PDF, and binary artifacts.
- Verify latest report selection uses server query behavior or a projection field, not browser-side sorting over arbitrary artifacts.

Needs Clarification
- None
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-462-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-462 from MM project
Summary: Mission Control Report Presentation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-462 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-462: Mission Control Report Presentation

Short Name
mission-control-report-presentation

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 11.4 Latest report semantics, 12. Presentation rules, 12.1 Primary UI surfaces, 12.2 Default read behavior, 12.3 Recommended renderer behavior, 12.4 Report-first UX rule, 12.5 Evidence presentation, 18. Suggested API/UI extensions
- Coverage IDs: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020, DESIGN-REQ-022

User Story
As an operator, I can open an execution with a final report and see the canonical report, related evidence, and normal observability surfaces without guessing which generic artifact matters.

Acceptance Criteria
- Given an execution has a canonical `report.primary` artifact, then Mission Control shows a report panel or top-level report card before requiring inspection of the generic artifact list.
- Given linked `report.summary`, `report.structured`, or `report.evidence` artifacts exist, then they are shown as related report content and remain individually openable where access permits.
- Given no `report.primary` artifact exists, then the UI falls back to the normal artifact list without fabricating report status from local heuristics.
- Viewer selection uses `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`, including appropriate markdown, JSON, text, diff, image, PDF, and binary handling.
- Latest report selection comes from server query behavior or a projection field, not browser-side sorting of arbitrary artifacts.

Requirements
- Expose report-first presentation for canonical final reports in execution detail surfaces.
- Display related report summary, structured, and evidence artifacts separately from generic artifacts and observability surfaces.
- Use artifact presentation contract fields for read target and renderer selection.
- Treat optional execution convenience fields and any report projection endpoint as read models over normal artifacts.

Relevant Implementation Notes
- Keep report presentation layered on the existing artifact presentation contract and execution detail surfaces.
- Prioritize canonical final reports linked as `report.primary` before generic artifact list inspection.
- Present `report.summary`, `report.structured`, and `report.evidence` artifacts as related report content while preserving individual artifact access controls and open/read behavior.
- Preserve the normal artifact list and observability surfaces; report presentation must not hide logs, diagnostics, provider snapshots, or other non-report artifacts.
- Use server-provided latest report query behavior or projection fields for latest report selection instead of browser-side sorting over arbitrary artifacts.
- Choose viewers from artifact presentation fields, including `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`.

Non-Goals
- Creating report status from local UI heuristics when no `report.primary` artifact exists.
- Replacing generic artifact list behavior for executions without canonical reports.
- Reworking artifact storage, authorization, lifecycle, or preview contracts.
- Treating observability outputs such as stdout, stderr, diagnostics, provider snapshots, or session continuity artifacts as canonical report content.

Validation
- Verify an execution with a canonical `report.primary` artifact shows report-first presentation in Mission Control before generic artifact list inspection.
- Verify linked `report.summary`, `report.structured`, and `report.evidence` artifacts appear as related report content and remain individually openable where access permits.
- Verify executions without `report.primary` fall back to the normal artifact list without fabricated report status.
- Verify viewer selection honors `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title` for markdown, JSON, text, diff, image, PDF, and binary artifacts.
- Verify latest report selection uses server query behavior or a projection field, not browser-side sorting over arbitrary artifacts.

Needs Clarification
- None

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable operator story and does not require processing multiple specs.
- Selected mode: Runtime.
- Source design: `docs/Artifacts/ReportArtifacts.md` is treated as runtime source requirements because the Jira brief points at implementation behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-462 were found under `specs/`; specification is the first incomplete stage.

## User Story - Present Canonical Reports In Mission Control

**Summary**: As an operator, I want Mission Control execution detail surfaces to present canonical final reports and related report content before generic artifact inspection, so I can review report deliverables without guessing which artifact matters.

**Goal**: Executions with a canonical final report expose a report-first presentation that opens the correct readable artifact target, shows related report content, and keeps ordinary artifacts and observability surfaces available.

**Independent Test**: Load an execution detail surface backed by report artifacts and verify that a canonical `report.primary` appears as the primary report presentation, related `report.summary`, `report.structured`, and `report.evidence` artifacts remain individually openable, viewer selection follows artifact presentation fields, and executions without `report.primary` retain the normal artifact list without fabricated report state.

**Acceptance Scenarios**:

1. **Given** an execution has a canonical `report.primary` artifact, **When** an operator opens the execution detail surface, **Then** Mission Control shows a report panel or top-level report card before requiring inspection of the generic artifact list.
2. **Given** an execution has linked `report.summary`, `report.structured`, or `report.evidence` artifacts, **When** the operator views the report surface, **Then** those artifacts appear as related report content and remain individually openable where access permits.
3. **Given** an execution has no `report.primary` artifact, **When** the operator opens the execution detail surface, **Then** Mission Control falls back to the normal artifact list without fabricating report status from local heuristics.
4. **Given** an operator opens a report artifact, **When** Mission Control chooses a viewer, **Then** the viewer selection uses `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`.
5. **Given** multiple artifacts exist for an execution, **When** Mission Control identifies the latest report, **Then** it uses server query behavior or an explicit projection field rather than browser-side sorting over arbitrary artifacts.

### Edge Cases

- The report query or projection indicates no canonical final report even though generic artifacts exist.
- Related report artifacts are present without a primary report.
- A report artifact has a `default_read_ref` that differs from its raw artifact ref.
- A report artifact has an unsupported or binary content type.
- Access rules prevent opening a related evidence artifact.

## Assumptions

- The report artifact contract from MM-460 and report bundle publication behavior from MM-461 provide canonical `report.*` links and bounded presentation metadata for this UI story.
- Mission Control already has an execution detail surface with generic artifact and observability sections that can be extended without replacing those surfaces.
- Server-side latest-report query behavior or an execution/report projection is the source of truth for report identity.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-011 | `docs/Artifacts/ReportArtifacts.md` §11.4 | Latest-report selection is server query behavior or an explicit projection; clients must not infer the canonical report by sorting arbitrary artifacts in the browser. | In scope | FR-001, FR-007 |
| DESIGN-REQ-012 | `docs/Artifacts/ReportArtifacts.md` §12.1 | Report-producing executions expose a Report panel or top-level report card, a Related Evidence section, and continued access to artifacts and observability surfaces. | In scope | FR-002, FR-003, FR-004 |
| DESIGN-REQ-013 | `docs/Artifacts/ReportArtifacts.md` §12.2-§12.3 | Primary report rendering uses `default_read_ref`, `render_hint`, `content_type`, and metadata name/title to choose an appropriate viewer and raw access behavior. | In scope | FR-005 |
| DESIGN-REQ-014 | `docs/Artifacts/ReportArtifacts.md` §12.4 | If an execution has a canonical `report.primary`, Mission Control presents that artifact before generic artifact list inspection and shows related report content. | In scope | FR-002, FR-003 |
| DESIGN-REQ-020 | `docs/Artifacts/ReportArtifacts.md` §12.5, §13 | Evidence artifacts remain individually addressable and viewable, and curated reports remain separate from stdout, stderr, diagnostics, provider snapshots, and other observability outputs. | In scope | FR-003, FR-004 |
| DESIGN-REQ-022 | `docs/Artifacts/ReportArtifacts.md` §18 | Optional execution summary fields and report projection endpoints are read models over normal artifacts, not a separate report storage or mutation path. | In scope | FR-001, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control MUST use server-provided latest-report query behavior or an explicit execution/report projection to identify the canonical final report for an execution.
- **FR-002**: Mission Control MUST present a canonical `report.primary` artifact in a report panel or top-level report card before requiring the operator to inspect the generic artifact list.
- **FR-003**: Mission Control MUST show linked `report.summary`, `report.structured`, and `report.evidence` artifacts as related report content while keeping each artifact individually openable where access permits.
- **FR-004**: Mission Control MUST preserve normal access to the generic artifact list, stdout, stderr, diagnostics, provider snapshots, logs, and other observability surfaces when report presentation is shown.
- **FR-005**: Mission Control MUST select report viewers from artifact presentation fields including `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`.
- **FR-006**: Mission Control MUST treat optional execution summary fields and any report projection endpoint as read models over normal artifacts rather than as a separate report storage or mutation surface.
- **FR-007**: Mission Control MUST NOT fabricate report status or canonical report identity through browser-side sorting or local heuristics when no `report.primary` artifact is available.
- **FR-008**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-462.

### Key Entities

- **Report Presentation**: The report-first Mission Control surface for an execution's canonical final report, including summary metadata and an open action.
- **Related Report Content**: Linked `report.summary`, `report.structured`, and `report.evidence` artifacts displayed near the primary report while remaining individually accessible.
- **Artifact Viewer Selection**: The decision that maps artifact presentation fields to a markdown, JSON, text, diff, image, PDF, binary metadata, or download/open surface.
- **Report Projection**: A server-provided read model or convenience fields that summarize canonical report refs over the existing artifact system.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Execution detail tests with `report.primary` verify report-first presentation appears before generic artifact list inspection.
- **SC-002**: Related content tests verify `report.summary`, `report.structured`, and `report.evidence` artifacts are displayed as report-related content and remain individually openable.
- **SC-003**: Fallback tests verify executions without `report.primary` show the normal artifact list and no fabricated report status.
- **SC-004**: Viewer selection tests cover markdown, JSON, text, diff, image, PDF or unknown binary behavior using artifact presentation fields.
- **SC-005**: Latest report selection tests verify Mission Control consumes server query/projection data and does not sort arbitrary artifacts in the browser to infer a canonical report.
- **SC-006**: MM-462 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
