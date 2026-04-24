# MM-495 MoonSpec Orchestration Input

## Source

- Jira issue: MM-495
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Apply report access and lifecycle policy
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-495`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-495 from MM project
Summary: Apply report access and lifecycle policy
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-495 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-495: Apply report access and lifecycle policy

Source Reference
Source Document: docs/Artifacts/ReportArtifacts.md
Source Title: Report Artifacts
Source Sections:
- 10 Metadata model for report artifacts
- 14 Security and access model
- 15 Retention guidance
Coverage IDs:
- DESIGN-REQ-011
- DESIGN-REQ-017
- DESIGN-REQ-018
As an operator, I want sensitive reports to reuse artifact authorization, preview, retention, pinning, and deletion behavior so report delivery is useful without widening access or lifecycle risk.

## Normalized Jira Detail

Acceptance criteria:
- Report artifacts use the existing artifact authorization model for preview and raw reads.
- Restricted report.primary, report.structured, and report.evidence artifacts can point default_read_ref to preview artifacts when raw access is disallowed.
- Report metadata does not expose secrets, raw access grants, cookies, session tokens, or large inline payloads.
- Default retention policy can map report.primary and report.summary to long retention, report.structured to long or standard, and report.evidence to standard or long by product policy.
- Final reports can be explicitly pinned and unpinned through the existing artifact API.
- Deleting a report artifact uses existing soft-delete/hard-delete behavior and does not implicitly delete unrelated observability artifacts.

Requirements:
- Reuse artifact authorization for sensitive reports.
- Support preview-safe report presentation.
- Apply report-specific retention recommendations through existing lifecycle controls.
- Avoid unsafe cascading deletion semantics.

Recommended step instructions:

Complete Jira issue MM-495: Apply report access and lifecycle policy

Description
Source Reference
Source Document: docs/Artifacts/ReportArtifacts.md
Source Title: Report Artifacts
Source Sections:
- 10 Metadata model for report artifacts
- 14 Security and access model
- 15 Retention guidance
Coverage IDs:
- DESIGN-REQ-011
- DESIGN-REQ-017
- DESIGN-REQ-018
As an operator, I want sensitive reports to reuse artifact authorization, preview, retention, pinning, and deletion behavior so report delivery is useful without widening access or lifecycle risk.

Acceptance criteria
- Report artifacts use the existing artifact authorization model for preview and raw reads.
- Restricted report.primary, report.structured, and report.evidence artifacts can point default_read_ref to preview artifacts when raw access is disallowed.
- Report metadata does not expose secrets, raw access grants, cookies, session tokens, or large inline payloads.
- Default retention policy can map report.primary and report.summary to long retention, report.structured to long or standard, and report.evidence to standard or long by product policy.
- Final reports can be explicitly pinned and unpinned through the existing artifact API.
- Deleting a report artifact uses existing soft-delete/hard-delete behavior and does not implicitly delete unrelated observability artifacts.
Requirements
- Reuse artifact authorization for sensitive reports.
- Support preview-safe report presentation.
- Apply report-specific retention recommendations through existing lifecycle controls.
- Avoid unsafe cascading deletion semantics.
