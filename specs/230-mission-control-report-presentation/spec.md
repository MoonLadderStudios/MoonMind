# Feature Specification: Surface Canonical Reports in Mission Control

**Feature Branch**: `230-mission-control-report-presentation`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-494 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-494-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: existing feature directory `specs/230-mission-control-report-presentation` already matched the MM-494 story scope, so this run resumed from the completed artifact set and aligned source traceability instead of regenerating later-stage artifacts.

## Original Preset Brief

```text
# MM-494 MoonSpec Orchestration Input

## Source

- Jira issue: MM-494
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Surface canonical reports in Mission Control
- Labels:
  - `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-494 from MM project
Summary: Surface canonical reports in Mission Control
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-494 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-494: Surface canonical reports in Mission Control

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections:
  - 12 Presentation rules
  - 13 Relationship to observability and diagnostics
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-016

User Story
As a Mission Control user, I want executions with canonical reports to show a report-first surface with related evidence so I can inspect the deliverable without hunting through raw artifacts or logs.

Acceptance Criteria
- Executions with a canonical `report.primary` show a Report panel or top-level report card before the generic artifact list.
- The report surface shows summary metadata and an open action for the default read target.
- Linked `report.summary`, `report.structured`, and `report.evidence` artifacts appear as related report content.
- Artifacts, stdout, stderr, diagnostics, and other observability surfaces remain accessible outside the report panel.
- Viewer choice uses `default_read_ref`, `render_hint`, `content_type`, and `metadata.name` or `metadata.title`.
- Markdown, JSON, plain text, diff, image, PDF, and unknown binary report artifacts follow the documented renderer behavior.
- Evidence artifacts remain individually addressable and viewable rather than being collapsed into the primary report by default.
- When no `report.primary` exists, Mission Control falls back to the existing artifact list behavior.

Requirements
- Add report-first execution detail presentation.
- Use existing artifact presentation contracts for default reads and viewer selection.
- Present related evidence separately from operational logs and diagnostics.

Relevant Implementation Notes
- Preserve MM-494 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Artifacts/ReportArtifacts.md` as the design reference for report presentation rules and the relationship between reports, observability, and diagnostics.
- Prioritize a report-first execution detail surface when canonical report artifacts are present.
- Keep the generic artifact list and observability surfaces accessible outside the report panel rather than replacing them.
- Use existing artifact presentation contracts to choose the default read target and renderer behavior.
- Show related `report.summary`, `report.structured`, and `report.evidence` artifacts as associated report content without collapsing evidence into the primary report by default.
- Support documented renderer behavior across markdown, JSON, plain text, diff, image, PDF, and unknown binary report artifacts.
- Fall back cleanly to the existing artifact-list behavior when no canonical `report.primary` artifact exists.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-494 is blocked by MM-495, whose embedded status is Backlog.
- Trusted Jira link metadata at fetch time shows MM-494 is related to MM-493 via a Blocks link where MM-494 blocks MM-493; MM-493 is not a blocker for this story.

Validation
- Verify executions with canonical `report.primary` artifacts surface a report panel or top-level report card before the generic artifact list.
- Verify the report surface shows summary metadata and an open action for the default read target.
- Verify linked `report.summary`, `report.structured`, and `report.evidence` artifacts appear as related report content.
- Verify artifacts, stdout, stderr, diagnostics, and other observability surfaces remain accessible outside the report panel.
- Verify viewer choice follows `default_read_ref`, `render_hint`, `content_type`, and `metadata.name` or `metadata.title`.
- Verify markdown, JSON, plain text, diff, image, PDF, and unknown binary report artifacts follow the documented renderer behavior.
- Verify evidence artifacts remain individually addressable and are not collapsed into the primary report by default.
- Verify execution detail falls back to the existing artifact list behavior when no canonical `report.primary` exists.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-494 Jira preset brief defines one independently testable Mission Control runtime story.
- Selected mode: Runtime.
- Source design: `docs/Artifacts/ReportArtifacts.md` is treated as runtime source requirements because the Jira brief points at implementation behavior, not documentation-only work.
- Resume decision: Existing Moon Spec artifacts in `specs/230-mission-control-report-presentation` already cover this report-presentation story, and the implementation plus verification evidence were already complete.
- Alignment decision: This run updated the preserved source brief, Jira traceability, and active feature pointer to MM-494 while reusing the existing completed runtime story artifacts.

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

- The narrower MM-494 Jira brief targets the same already-implemented runtime story already captured by this feature directory, so the existing report-presentation implementation and tests remain the concrete execution evidence for this aligned spec.
- Mission Control already has an execution detail surface with generic artifact and observability sections that can be extended without replacing those surfaces.
- Server-side latest-report query behavior or an execution/report projection is the source of truth for report identity.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-005 | `docs/tmp/jira-orchestration-inputs/MM-494-moonspec-orchestration-input.md` | Canonical report presentation MUST remain server-driven through existing `report.primary` artifact selection and must not rely on browser-side inference. | In scope | FR-001, FR-007 |
| DESIGN-REQ-014 | `docs/tmp/jira-orchestration-inputs/MM-494-moonspec-orchestration-input.md` | Executions with a canonical report MUST surface a report-first panel with related report content before generic artifact inspection. | In scope | FR-002, FR-003 |
| DESIGN-REQ-015 | `docs/tmp/jira-orchestration-inputs/MM-494-moonspec-orchestration-input.md` | Viewer selection MUST honor `default_read_ref`, `render_hint`, `content_type`, and `metadata.name` or `metadata.title` for the report surface. | In scope | FR-005 |
| DESIGN-REQ-016 | `docs/tmp/jira-orchestration-inputs/MM-494-moonspec-orchestration-input.md` | Related evidence MUST remain individually addressable, observability surfaces MUST remain accessible outside the report panel, and the implementation MUST stay on the existing artifact read model. | In scope | FR-003, FR-004, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control MUST use server-provided latest-report query behavior or an explicit execution/report projection to identify the canonical final report for an execution.
- **FR-002**: Mission Control MUST present a canonical `report.primary` artifact in a report panel or top-level report card before requiring the operator to inspect the generic artifact list.
- **FR-003**: Mission Control MUST show linked `report.summary`, `report.structured`, and `report.evidence` artifacts as related report content while keeping each artifact individually openable where access permits.
- **FR-004**: Mission Control MUST preserve normal access to the generic artifact list, stdout, stderr, diagnostics, provider snapshots, logs, and other observability surfaces when report presentation is shown.
- **FR-005**: Mission Control MUST select report viewers from artifact presentation fields including `default_read_ref`, `render_hint`, `content_type`, `metadata.name`, and `metadata.title`.
- **FR-006**: Mission Control MUST treat optional execution summary fields and any report projection endpoint as read models over normal artifacts rather than as a separate report storage or mutation surface.
- **FR-007**: Mission Control MUST NOT fabricate report status or canonical report identity through browser-side sorting or local heuristics when no `report.primary` artifact is available.
- **FR-008**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-494.

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
- **SC-006**: MM-494 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
