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
