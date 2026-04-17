# MM-389 MoonSpec Orchestration Input

## Source

- Jira issue: MM-389
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document plans overview preset boundary
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-389 from MM project
Summary: Document plans overview preset boundary
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-389 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-389: Document plans overview preset boundary

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 7. docs/Temporal/101-PlansOverview.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-024
  - DESIGN-REQ-001
  - DESIGN-REQ-020
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a documentation reader, I want the plans overview to link authoring-time preset composition to TaskPresetsSystem and runtime plan semantics to SkillAndPlanContracts so the boundary is discoverable.

Acceptance Criteria
- The plans overview or equivalent index includes the requested alignment paragraph near plan overview content.
- The paragraph states preset composition belongs to the control plane and is resolved before PlanDefinition creation.
- The paragraph states plans remain flattened execution graphs of concrete nodes and edges.
- The paragraph links authoring-time composition semantics to TaskPresetsSystem and runtime plan semantics to SkillAndPlanContracts.
- No additional migration checklist is added to canonical docs beyond the requested concise boundary clarification.

Requirements
- Add or update cross-links in the plans overview so the authoring/runtime boundary is obvious.
- Keep the update intentionally minimal.

Relevant Implementation Notes
- The Jira source references `docs/Tasks/PresetComposability.md`; preserve that source reference as Jira traceability even if the source document is unavailable in the current checkout.
- The Jira source references `docs/Temporal/101-PlansOverview.md`; the current checkout exposes the plans overview at `docs/tmp/101-PlansOverview.md`, so use the repository-current equivalent when implementing unless a canonical replacement is identified.
- Link authoring-time preset composition semantics to `docs/Tasks/TaskPresetsSystem.md`.
- Link runtime plan semantics to `docs/Tasks/SkillAndPlanContracts.md`.
- State that preset composition is a control-plane concern resolved before `PlanDefinition` creation.
- State that runtime plans remain flattened execution graphs of concrete nodes and edges.
- Keep canonical documentation desired-state focused. Do not add migration checklists or implementation backlog content outside `docs/tmp/`.
- Preserve MM-389 anywhere downstream artifacts summarize, implement, verify, commit, or open a pull request for this work.

Verification
- Confirm the plans overview or equivalent index includes a concise boundary clarification near plan overview content.
- Confirm the clarification states preset composition belongs to the control plane and is resolved before `PlanDefinition` creation.
- Confirm the clarification states runtime plans remain flattened execution graphs of concrete nodes and edges.
- Confirm the clarification links authoring-time preset composition semantics to `docs/Tasks/TaskPresetsSystem.md`.
- Confirm the clarification links runtime plan semantics to `docs/Tasks/SkillAndPlanContracts.md`.
- Confirm canonical docs do not gain an additional migration checklist for this story.
- Preserve MM-389 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-388 is blocked by this issue.
