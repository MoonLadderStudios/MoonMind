# MM-462 MoonSpec Orchestration Input

## Source

- Jira issue: MM-462
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Mission Control Report Presentation
- Labels: `moonmind-workflow-mm-ba49b1c2-6312-465a-bf68-0d46b37886cf`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

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
