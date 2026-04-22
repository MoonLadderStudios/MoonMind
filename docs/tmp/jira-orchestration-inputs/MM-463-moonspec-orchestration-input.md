# MM-463 MoonSpec Orchestration Input

## Source

- Jira issue: MM-463
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Sensitive Report Access and Retention
- Labels: `moonmind-workflow-mm-ba49b1c2-6312-465a-bf68-0d46b37886cf`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-463 from MM project
Summary: Sensitive Report Access and Retention
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-463 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-463: Sensitive Report Access and Retention

Short Name
sensitive-report-access-retention

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 7. Consumer and producer invariants, 14. Security and access model, 15. Retention guidance
- Coverage IDs: DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022

User Story
As an operator, I can rely on report artifacts to use existing authorization, preview, retention, pinning, and deletion behavior so sensitive reports remain useful without widening raw access.

Acceptance Criteria
- Given a sensitive report has restricted raw access, then Mission Control uses preview/default-read behavior where available and does not assume full download is allowed.
- Given `report.primary` or `report.summary` artifacts are created, then their default retention policy is long unless product policy overrides it.
- Given `report.structured` or `report.evidence` artifacts are created, then their retention follows standard or long policy based on the report family and audit needs.
- Given a final report is important to retain, then it can be pinned or unpinned through existing artifact APIs.
- Deleting a report artifact uses artifact-system-native soft/hard deletion and does not implicitly delete unrelated runtime stdout, stderr, diagnostics, or other observability artifacts.

Requirements
- Reuse the existing artifact authorization model for report artifacts and evidence.
- Support preview artifacts and `default_read_ref` for sensitive report presentation.
- Apply recommended retention mappings for primary, summary, structured, evidence, and related observability artifacts.
- Keep deletion artifact-system-native without undefined cascading into unrelated observability artifacts.

Relevant Implementation Notes
- Keep report artifact access within the existing artifact authorization, preview, and default-read boundaries.
- Do not add report-specific raw download assumptions for sensitive report artifacts.
- Use existing artifact APIs for pinning, unpinning, soft deletion, and hard deletion.
- Preserve separation between report artifact lifecycle behavior and unrelated runtime observability artifacts such as stdout, stderr, diagnostics, and logs.
- Preserve MM-463 and coverage IDs DESIGN-REQ-015, DESIGN-REQ-016, and DESIGN-REQ-022 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- Widening raw artifact access for sensitive reports.
- Creating report-specific authorization, retention, pinning, or deletion systems separate from the existing artifact system.
- Cascading report deletion into unrelated runtime stdout, stderr, diagnostics, logs, or other observability artifacts.

Validation
- Verify sensitive report presentation uses preview/default-read behavior where available and does not require raw download access.
- Verify `report.primary` and `report.summary` artifacts default to long retention unless policy overrides them.
- Verify `report.structured` and `report.evidence` artifacts follow standard or long retention based on report family and audit needs.
- Verify final report artifacts can be pinned and unpinned through existing artifact APIs.
- Verify report artifact deletion uses artifact-system-native soft/hard deletion without implicitly deleting unrelated runtime observability artifacts.

Needs Clarification
- None
