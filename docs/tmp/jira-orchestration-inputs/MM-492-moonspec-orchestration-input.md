# MM-492 MoonSpec Orchestration Input

## Source

- Jira issue: MM-492
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Define the report artifact contract
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-492 from MM project
Summary: Define the report artifact contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-492 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-492: Define the report artifact contract

User Story
As a workflow producer, I want a canonical report artifact contract so report deliverables use explicit artifact semantics without introducing a second storage system.

Source Document
`docs/Artifacts/ReportArtifacts.md`

Source Title
Report Artifacts

Source Sections
- 1 Purpose
- 1.1 Related docs and ownership boundaries
- 3 Core decision
- 6 Definitions
- 8 Recommended report artifact classes
- 9 Report bundle model
- 10 Metadata model for report artifacts

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-011

Acceptance Criteria
- Report-specific link types are defined for `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export` where applicable.
- Report artifacts persist through the existing artifact store and index with immutable artifact IDs.
- Generic outputs continue to use `output.primary`, `output.summary`, and `output.agent_result` when they are not explicit report deliverables.
- The compact report bundle shape carries artifact refs, report type, scope, sensitivity, and bounded counts only.
- Metadata validation accepts standardized report keys and rejects or strips secrets, raw grants, cookies, session tokens, and large inline payloads.
- Definitions for report artifact, report bundle, evidence artifact, final report, and intermediate report are reflected in code contracts or schema documentation.

Requirements
- Model reports as first-class artifact families on existing artifact infrastructure.
- Define stable `report.*` link-type semantics without treating every `output.primary` as a report.
- Represent report bundles with refs and bounded metadata only.
- Constrain report metadata to safe display fields.

Implementation Notes
- Keep report deliverables on the existing artifact infrastructure; do not introduce a second storage system.
- Preserve the distinction between explicit report deliverables and generic outputs.
- Keep the bundle contract compact and reference-based rather than embedding large payloads.
- Enforce metadata sanitization for secret-like values and oversized inline content.
- Reflect the canonical definitions from `docs/Artifacts/ReportArtifacts.md` in code contracts or schema documentation.

Needs Clarification
- None
