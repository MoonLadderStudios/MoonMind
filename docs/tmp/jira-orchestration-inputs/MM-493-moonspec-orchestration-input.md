# MM-493 MoonSpec Orchestration Input

## Source

- Jira issue: MM-493
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish report bundles from workflows
- Labels:
  - `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-493 from MM project
Summary: Publish report bundles from workflows
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-493 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-493: Publish report bundles from workflows

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections:
  - 7 Consumer and producer invariants
  - 11 Storage and linkage rules
  - 16 Workflow integration guidance
  - 17 Example workflow mappings
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-021

User Story
As an operator, I want report-producing workflows to publish immutable report bundles through activities so completed executions expose durable final and step-level reports without workflow-history bloat.

Acceptance Criteria
- Report assembly and artifact creation happen in activities, not workflow code.
- Workflow return values and state include compact artifact refs and bounded metadata only.
- Report artifacts link to namespace, workflow_id, run_id, link_type, label, and bounded step_id/attempt metadata when step-scoped.
- A completed execution with a primary report has one canonical `report.primary` artifact with `metadata.is_final_report=true` and `metadata.report_scope=final`.
- Intermediate reports can coexist with the final report without mutating prior report artifacts.
- Latest report resolution is provided by server query/projection behavior and does not rely on browser-side sorting of arbitrary artifacts.
- Unit-test, coverage, pentest/security, and benchmark-style flows can each publish valid bundles without adopting one universal findings schema.

Requirements
- Create report artifacts through activity/service boundaries.
- Link final and step-level reports to execution identity.
- Keep report bodies, screenshots, findings, logs, and transcripts out of workflow history.
- Support multiple workflow families under one report-bundle contract.

Relevant Implementation Notes
- Preserve MM-493 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Keep report assembly and artifact creation out of workflow code; implement bundle creation in activities or service boundaries.
- Represent reports in workflow state and return values with compact artifact refs and bounded metadata only.
- Link final and step-level report artifacts to namespace, workflow ID, run ID, link type, label, and bounded step-scoped identifiers when present.
- Ensure the final execution report resolves to one canonical `report.primary` artifact marked with `metadata.is_final_report=true` and `metadata.report_scope=final`.
- Allow intermediate report artifacts to coexist without mutating or overwriting previously published artifacts.
- Implement latest-report lookup through server query or projection behavior rather than browser-side artifact sorting.
- Keep the report bundle contract flexible enough for unit-test, coverage, pentest/security, and benchmark-style workflows without forcing a universal findings schema.
- Keep report bodies, screenshots, findings, logs, and transcripts out of workflow history and store them only in artifact-backed report bundles.
- Use `docs/Artifacts/ReportArtifacts.md` as the design reference for report invariants, storage/linkage rules, workflow integration guidance, and example mappings.

Non-Goals
- Moving report assembly logic into workflow code.
- Requiring browser-side sorting heuristics to determine the latest canonical report.
- Forcing all workflow families to adopt one universal findings schema.
- Mutating prior report artifacts when publishing intermediate or final reports.

Validation
- Verify report assembly and artifact creation happen in activities or service boundaries, not workflow code.
- Verify workflow return values and persisted workflow state carry compact artifact refs and bounded metadata only.
- Verify final and step-level report artifacts are linked to execution identity with the required bounded metadata.
- Verify a completed execution with a primary report exposes one canonical `report.primary` artifact marked final with `metadata.report_scope=final`.
- Verify intermediate reports can coexist with the final report without mutating prior artifacts.
- Verify latest-report resolution comes from server query/projection behavior rather than browser-side sorting.
- Verify unit-test, coverage, pentest/security, and benchmark-style flows can each publish valid report bundles under the shared contract.
- Verify report bodies, screenshots, findings, logs, and transcripts stay out of workflow history.

Needs Clarification
- None
