# Feature Specification: Document Plans Overview Preset Boundary

**Feature Branch**: `203-document-plans-preset-boundary`
**Created**: 2026-04-17
**Status**: Draft
**Input**:

```text
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
- The Jira source references `docs/Temporal/101-PlansOverview.md`; the current checkout exposes the plans overview at `docs/MoonMindRoadmap.md`, so use the repository-current equivalent when implementing unless a canonical replacement is identified.
- Link authoring-time preset composition semantics to `docs/Tasks/TaskPresetsSystem.md`.
- Link runtime plan semantics to `docs/Tasks/SkillAndPlanContracts.md`.
- State that preset composition is a control-plane concern resolved before `PlanDefinition` creation.
- State that runtime plans remain flattened execution graphs of concrete nodes and edges.
- Keep canonical documentation desired-state focused. Do not add migration checklists or implementation backlog content outside `local-only handoffs`.
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
```

## User Story - Plans Overview Preset Boundary

**Summary**: As a documentation reader, I want the plans overview to make the preset authoring/runtime boundary discoverable through concise cross-links.

**Goal**: Readers can distinguish preset composition as a control-plane authoring concern from runtime plan execution semantics without hunting across Task Presets and Skill/Plan contract documents.

**Independent Test**: Review the plans overview or repository-current equivalent and confirm it contains one concise boundary clarification near the task, skills, presets, and plans content, with links to authoring-time preset composition semantics and runtime plan semantics.

**Acceptance Scenarios**:

1. **Given** a reader opens the plans overview, **When** they review the task, skills, presets, and plans section, **Then** they can find a concise paragraph that explains the authoring/runtime preset boundary.
2. **Given** the paragraph describes preset composition, **When** it names the control-plane boundary, **Then** it states composition is resolved before `PlanDefinition` creation.
3. **Given** the paragraph describes runtime plans, **When** it names execution semantics, **Then** it states plans remain flattened execution graphs of concrete nodes and edges.
4. **Given** a reader needs deeper detail, **When** they follow links from the paragraph, **Then** authoring-time semantics point to `TaskPresetsSystem` and runtime plan semantics point to `SkillAndPlanContracts`.
5. **Given** canonical documentation is reviewed, **When** this story is complete, **Then** it has not added a new migration checklist to canonical docs beyond the concise boundary clarification.
6. **Given** downstream MoonSpec, implementation notes, verification, commit text, or pull request metadata are generated, **When** traceability is reviewed, **Then** the Jira issue key MM-389 remains present.

### Edge Cases

- The Jira brief references `docs/Tasks/PresetComposability.md`, but that file is absent in the current checkout; the preserved MM-389 brief, `docs/Tasks/TaskPresetsSystem.md`, and `docs/Tasks/SkillAndPlanContracts.md` are the active sources.
- The Jira brief references `docs/Temporal/101-PlansOverview.md`, while the repository-current overview is `docs/MoonMindRoadmap.md`; the current file is treated as the equivalent target.
- The plans overview is itself under `local-only handoffs`; the change must stay concise and must not move migration backlog into canonical docs.
- Existing table entries already link the two target documents separately; the new clarification must explain the boundary between them instead of duplicating table rows.

## Assumptions

- The selected runtime mode means the documentation target is treated as runtime source requirements for product behavior, even though the implementation change is a documentation contract update.
- `docs/MoonMindRoadmap.md` is the repository-current equivalent of the Jira-referenced plans overview.
- This story does not require executable code changes unless planning discovers implementation drift from the documented runtime contract.

## Source Design Requirements

- **DESIGN-REQ-001**: Source "Plans overview alignment" requires the plans overview or equivalent index to include the requested alignment paragraph near plan overview content. Scope: in scope. Maps to FR-001, FR-002.
- **DESIGN-REQ-020**: Source "Preset composition boundary" requires preset composition to belong to the control plane and be resolved before `PlanDefinition` creation. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-024**: Source "Runtime plan shape" requires plans to remain flattened execution graphs of concrete nodes and edges. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-025**: Source "Cross-document links" requires authoring-time composition semantics to link to `TaskPresetsSystem` and runtime plan semantics to link to `SkillAndPlanContracts`. Scope: in scope. Maps to FR-006, FR-007.
- **DESIGN-REQ-026**: Source "Canonical documentation boundary" requires no additional migration checklist in canonical docs beyond the concise boundary clarification. Scope: in scope. Maps to FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The plans overview or repository-current equivalent MUST include one concise boundary clarification near the tasks, skills, presets, and plans content.
- **FR-002**: The clarification MUST be discoverable from the existing plan overview context without creating a replacement index or new documentation hierarchy.
- **FR-003**: The clarification MUST state that preset composition belongs to the control plane.
- **FR-004**: The clarification MUST state that preset composition is resolved before `PlanDefinition` creation.
- **FR-005**: The clarification MUST state that runtime plans remain flattened execution graphs of concrete nodes and edges.
- **FR-006**: The clarification MUST link authoring-time preset composition semantics to `docs/Tasks/TaskPresetsSystem.md`.
- **FR-007**: The clarification MUST link runtime plan semantics to `docs/Tasks/SkillAndPlanContracts.md`.
- **FR-008**: Canonical documentation MUST NOT gain an additional migration checklist for this story.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-389` and the original Jira preset brief.

### Key Entities

- **Plans Overview**: The repository index that helps readers discover plan-shaped documentation and related task, skill, preset, and plan contracts.
- **Control-Plane Preset Composition**: Authoring-time preset selection and expansion work that is resolved before a runtime `PlanDefinition` exists.
- **Runtime Plan Semantics**: The execution-facing contract where plans are flattened graphs of concrete nodes and edges.
- **Boundary Clarification**: The concise paragraph that links preset authoring semantics to runtime plan semantics without adding migration backlog.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Review of the plans overview finds one paragraph near the tasks, skills, presets, and plans section that explains the preset authoring/runtime boundary.
- **SC-002**: The paragraph includes the phrases or equivalent semantics for "control plane" and "before `PlanDefinition` creation".
- **SC-003**: The paragraph includes the phrase or equivalent semantics for "flattened execution graphs of concrete nodes and edges".
- **SC-004**: The paragraph links to `docs/Tasks/TaskPresetsSystem.md` for authoring-time preset composition semantics.
- **SC-005**: The paragraph links to `docs/Tasks/SkillAndPlanContracts.md` for runtime plan semantics.
- **SC-006**: Review confirms no new migration checklist was added to canonical docs for this story.
- **SC-007**: All five in-scope source design requirements map to at least one functional requirement, and MM-389 remains present in MoonSpec artifacts and verification evidence.
