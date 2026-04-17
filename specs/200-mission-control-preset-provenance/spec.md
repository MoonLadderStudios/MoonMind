# Feature Specification: Mission Control Preset Provenance Surfaces

**Feature Branch**: `200-mission-control-preset-provenance`
**Created**: 2026-04-17
**Status**: Draft
**Input**:

```text
# MM-387 MoonSpec Orchestration Input

## Source

- Jira issue: MM-387
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document Mission Control preset provenance surfaces
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-387 from MM project
Summary: Document Mission Control preset provenance surfaces
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-387 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-387: Document Mission Control preset provenance surfaces

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 5. docs/UI/MissionControlArchitecture.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-022
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a Mission Control operator, I want task lists, detail pages, and create/edit flows to explain preset-derived work without implying nested runtime behavior.

Acceptance Criteria
- MissionControlArchitecture includes preset-composition scope for preview, edit, and detail rendering without making composition a runtime concept.
- Task detail behavior may show provenance summaries and chips for Manual, Preset, and Preset path.
- Steps remain execution-first; preset grouping is explanatory metadata, not the primary ordering model.
- Submit integration allows `/tasks/new` to preview composed presets but forbids unresolved preset includes as runtime work.
- Expansion tree artifacts or summaries are secondary evidence; flat steps, logs, diagnostics, and output artifacts remain canonical execution evidence.
- Vocabulary distinguishes user-facing preset from internal preset binding/provenance and forbids subtask, sub-plan, or separate workflow-run labels for includes.

Requirements
- Document Mission Control preview, detail, edit, and submit behavior for preset-derived work.
- Document detail-page provenance affordances and execution-first ordering.
- Document artifact/evidence hierarchy for expansion summaries versus execution evidence.
- Document compatibility vocabulary for preset includes.

Relevant Implementation Notes
- The canonical active documentation target is `docs/UI/MissionControlArchitecture.md`.
- The issue references `docs/Tasks/PresetComposability.md`; preserve the reference as Jira traceability even if the source document is unavailable in the current checkout.
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `docs/tmp/`.
- Mission Control may explain preset-derived work in previews, task lists, task details, and create/edit flows, but preset composition must not become a runtime execution concept.
- Task detail provenance summaries and chips may expose Manual, Preset, and Preset path metadata as explanatory context while keeping flat steps as the execution-first ordering model.
- Submit integration for `/tasks/new` may preview composed presets, but runtime work must not include unresolved preset includes.
- Expansion tree artifacts or summaries are secondary evidence; flat steps, logs, diagnostics, and output artifacts remain canonical execution evidence.
- User-facing vocabulary should say preset for operator concepts and binding/provenance for internal metadata, while avoiding subtask, sub-plan, or separate workflow-run labels for preset includes.

Verification
- Confirm `docs/UI/MissionControlArchitecture.md` documents preset-composition scope for preview, edit, task list, and task detail rendering without making composition a runtime concept.
- Confirm task detail documentation allows provenance summaries and chips for Manual, Preset, and Preset path while preserving flat execution-first step ordering.
- Confirm submit integration documentation for `/tasks/new` allows composed preset previews and forbids unresolved preset includes as runtime work.
- Confirm artifact and evidence hierarchy treats expansion summaries as secondary evidence behind flat steps, logs, diagnostics, and output artifacts.
- Confirm vocabulary distinguishes user-facing preset concepts from internal binding/provenance metadata and avoids subtask, sub-plan, or separate workflow-run labels for preset includes.
- Preserve MM-387 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-388 blocks this issue.
- MM-386 is blocked by this issue.
```

## User Story - Mission Control Preset Provenance Surfaces

**Summary**: As a Mission Control operator, I want task lists, detail pages, and create/edit flows to explain preset-derived work without implying nested runtime behavior.

**Goal**: Operators can understand which submitted steps came from manual authoring, a preset, or a preset include path while Mission Control keeps flat execution steps, logs, diagnostics, and artifacts as the canonical execution evidence.

**Independent Test**: Can be tested by reviewing the Mission Control UI architecture contract and validating that it defines preset provenance presentation for preview, edit, list/detail, submit, artifact evidence, and vocabulary without turning preset composition into a runtime execution model.

**Acceptance Scenarios**:

1. **Given** a task draft contains composed preset steps, **When** `/tasks/new` previews the draft before submission, **Then** Mission Control may show preset grouping and provenance while ensuring unresolved preset includes are not submitted as runtime work.
2. **Given** a submitted task contains manual and preset-derived steps, **When** an operator views the task detail page, **Then** the detail surface may show provenance summaries and chips for Manual, Preset, and Preset path while keeping flat steps as the primary execution ordering.
3. **Given** task list or edit/rerun flows expose preset-derived work, **When** Mission Control presents that context, **Then** preset metadata is explanatory and must not imply nested subtasks, sub-plans, separate workflow runs, or runtime preset expansion.
4. **Given** expansion tree artifacts or summaries are available, **When** the operator reviews execution evidence, **Then** those summaries remain secondary to flat steps, logs, diagnostics, and output artifacts.
5. **Given** downstream MoonSpec, implementation notes, verification, commit text, or pull request metadata are generated for this work, **When** traceability is reviewed, **Then** the Jira issue key MM-387 remains present.

### Edge Cases

- A source document named by the Jira brief, `docs/Tasks/PresetComposability.md`, is absent in the current checkout; the preserved MM-387 Jira brief and current `docs/UI/MissionControlArchitecture.md` are the active sources for this story.
- A task may contain only manual steps, only preset-derived steps, or a mix of manual, preset, included, and detached steps; the UI must not make the ordering ambiguous.
- A composed preset preview may fail or produce unresolved includes; unresolved preset includes must be rejected before runtime submission rather than hidden in the UI.
- Expansion summaries may be missing or stale; flat steps, logs, diagnostics, and output artifacts remain canonical execution evidence.

## Assumptions

- The selected runtime mode means the Mission Control architecture document is treated as runtime source requirements for product behavior, even though this story updates the canonical UI contract.
- `docs/UI/MissionControlArchitecture.md` is the correct canonical location for Mission Control task list, detail, submit, and compatibility vocabulary behavior.
- This story does not require executable React behavior changes unless planning discovers that existing implementation contradicts the runtime contract.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control MUST define preset provenance presentation for task previews, task edit/rerun reconstruction, task lists, and task detail pages.
- **FR-002**: Mission Control MAY show preset grouping and provenance summaries only as explanatory metadata and MUST NOT make preset composition a runtime execution concept.
- **FR-003**: Task detail behavior MUST allow provenance summaries and chips for Manual, Preset, and Preset path metadata.
- **FR-004**: Task step presentation MUST keep flat steps as the primary execution ordering model even when preset grouping is available.
- **FR-005**: Submit integration for `/tasks/new` MUST allow preview of composed presets and MUST forbid unresolved preset includes from being submitted as runtime work.
- **FR-006**: Expansion tree artifacts or summaries MUST be presented as secondary evidence behind flat steps, logs, diagnostics, and output artifacts.
- **FR-007**: User-facing vocabulary MUST distinguish presets from internal binding/provenance metadata and MUST avoid subtask, sub-plan, or separate workflow-run labels for preset includes.
- **FR-008**: Canonical documentation updates MUST remain desired-state documentation and keep volatile migration or implementation tracking out of canonical docs.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-387` and the original Jira preset brief.

### Key Entities

- **Preset Provenance**: Metadata that explains whether a task or step originated manually, from a preset, or from a preset include path.
- **Preset Path Chip**: A compact UI label that may identify Manual, Preset, or Preset path context without changing execution ordering.
- **Composed Preset Preview**: The `/tasks/new` preview state that explains included preset structure before submission.
- **Expansion Summary**: A secondary artifact or summary that describes preset expansion but is not canonical execution evidence.
- **Flat Execution Steps**: The ordered runtime step list that remains canonical for execution, logs, diagnostics, and output artifacts.

## Source Design Requirements

- **DESIGN-REQ-014**: Source "Create/edit preset behavior" requires create/edit flows to preserve preset context while keeping execution based on resolved flat steps. Scope: in scope. Maps to FR-001, FR-002, FR-004, FR-005.
- **DESIGN-REQ-015**: Source "Cross-document invariants" requires compile-time-only composition and no live runtime preset expansion. Scope: in scope. Maps to FR-002, FR-005.
- **DESIGN-REQ-022**: Source "MissionControlArchitecture preset surfaces" requires Mission Control preview, edit, list, and detail surfaces to explain preset-derived work without implying nested runtime behavior. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-025**: Source "Snapshot durability and evidence" requires expansion summaries to remain secondary to canonical execution evidence. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-026**: Source "Execution-plane boundary" requires user-facing labels to avoid implying separate runtime workflows for preset includes. Scope: in scope. Maps to FR-007.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Review of the canonical Mission Control architecture finds explicit preset provenance scope for preview, edit/rerun, task list, and task detail surfaces.
- **SC-002**: Review of task detail architecture finds Manual, Preset, and Preset path provenance summaries or chips documented as explanatory metadata.
- **SC-003**: Review of submit integration rules finds composed preset preview allowed and unresolved preset includes forbidden as runtime work.
- **SC-004**: Review of evidence hierarchy finds expansion summaries secondary to flat steps, logs, diagnostics, and output artifacts.
- **SC-005**: Review of compatibility vocabulary finds preset include labels must not use subtask, sub-plan, or separate workflow-run terminology.
- **SC-006**: All five in-scope source design requirements map to at least one functional requirement, and MM-387 remains present in MoonSpec artifacts and verification evidence.
