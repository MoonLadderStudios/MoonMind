# MM-497 MoonSpec Orchestration Input

## Source

- Jira issue: MM-497
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Roll out report semantics without flag-day migration
- Labels: `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-497`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-497 from MM project
Summary: Roll out report semantics without flag-day migration
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-497 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-497: Roll out report semantics without flag-day migration

Source Reference
Source Document: docs/Artifacts/ReportArtifacts.md
Source Title: Report Artifacts
Source Sections:
- 2 Scope / Non-goals
- 5 Non-goals
- 17 Example workflow mappings
- 19 Migration guidance
- 20 Open questions
- 21 Bottom line
Coverage IDs:
- DESIGN-REQ-021
- DESIGN-REQ-023
- DESIGN-REQ-024
As a MoonMind maintainer, I want report artifact semantics to roll out incrementally with documented non-goals and producer examples so existing generic outputs continue working while new report workflows adopt report.* conventions.

## Normalized Jira Detail

Acceptance criteria:
- Existing generic output.primary workflows continue to function without being reclassified as reports.
- New report workflows prefer report.* link types and report metadata conventions.
- Migration can proceed through metadata conventions, explicit link types/UI surfacing, compact bundle results, and later projections/filters/retention/pinning.
- PDF rendering engines, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing remain out of scope unless separately specified.
- Examples document unit-test, coverage, pentest/security, and benchmark report mappings.
- Unresolved product choices around report_type enums, auto-pinning, projection endpoint timing, export semantics, evidence grouping, and multi-step task projections are tracked as clarification points for later stories.

Requirements:
- Support incremental report rollout with generic-output compatibility.
- Document representative workflow mappings and explicit non-goals.
- Keep migration notes under docs/tmp when implementation tracking is needed.

Recommended step instructions:

Complete Jira issue MM-497: Roll out report semantics without flag-day migration

Description
Source Reference
Source Document: docs/Artifacts/ReportArtifacts.md
Source Title: Report Artifacts
Source Sections:
- 2 Scope / Non-goals
- 5 Non-goals
- 17 Example workflow mappings
- 19 Migration guidance
- 20 Open questions
- 21 Bottom line
Coverage IDs:
- DESIGN-REQ-021
- DESIGN-REQ-023
- DESIGN-REQ-024
As a MoonMind maintainer, I want report artifact semantics to roll out incrementally with documented non-goals and producer examples so existing generic outputs continue working while new report workflows adopt report.* conventions.

Acceptance criteria
- Existing generic output.primary workflows continue to function without being reclassified as reports.
- New report workflows prefer report.* link types and report metadata conventions.
- Migration can proceed through metadata conventions, explicit link types/UI surfacing, compact bundle results, and later projections/filters/retention/pinning.
- PDF rendering engines, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing remain out of scope unless separately specified.
- Examples document unit-test, coverage, pentest/security, and benchmark report mappings.
- Unresolved product choices around report_type enums, auto-pinning, projection endpoint timing, export semantics, evidence grouping, and multi-step task projections are tracked as clarification points for later stories.

Requirements
- Support incremental report rollout with generic-output compatibility.
- Document representative workflow mappings and explicit non-goals.
- Keep migration notes under docs/tmp when implementation tracking is needed.
