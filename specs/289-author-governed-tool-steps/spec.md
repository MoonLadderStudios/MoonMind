# Feature Specification: Author Governed Tool Steps

**Feature Branch**: `289-author-governed-tool-steps`
**Created**: 2026-05-01
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-576 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-576` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-576` and local artifact `artifacts/moonspec-inputs/MM-576-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-576` under `specs/`, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-576 MoonSpec Orchestration Input

## Source

- Jira issue: MM-576
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Author governed Tool steps
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `artifacts/moonspec-inputs/MM-576-trusted-jira-get-issue.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Labels: moonmind-workflow-mm-911309af-6b4f-48e7-8835-e533aa9af8cf

## Canonical MoonSpec Feature Request

Jira issue: MM-576 from MM project
Summary: Author governed Tool steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-576 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-576: Author governed Tool steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.1 tool
- 6.3 Tool picker
- 8.2 Tool validation
- 10.1 Keep Tool
- 15. Non-Goals
Coverage IDs:
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-019
- DESIGN-REQ-020
As a task author, I can configure a Tool step as a typed governed operation so deterministic integrations run with schema, authorization, capability, retry, and error contracts.
Acceptance Criteria
- Tool steps require a typed tool id, resolvable or pinned version, and schema-valid inputs.
- The Tool picker supports integration/domain grouping and search.
- Dynamic option providers can populate fields such as Jira target statuses.
- Arbitrary shell input is rejected unless it is an approved typed command tool with bounded inputs and policy.
Requirements
- Tool definitions declare name/version, input schema, output schema, required authorization, worker capabilities, retry policy, execution binding, validation, and error model.
- Tool steps are presented as direct deterministic work.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

```

## User Story - Governed Tool Selection

**Summary**: As a task author, I can configure a Tool step through a governed picker so deterministic integrations run with visible contracts and bounded schema-shaped inputs.

**Goal**: Tool authoring moves beyond free-form id entry by exposing searchable grouped tool choices, visible contract metadata, and Jira transition target statuses derived from trusted tool data while preserving the typed Tool payload submitted for execution.

**Independent Test**: Render the Create page, switch a step to Tool, search grouped Jira tools returned by the trusted tool discovery endpoint, select `jira.transition_issue`, enter an issue key, choose a target status loaded through the trusted transitions tool, submit the task, and verify the submitted Tool payload includes the selected tool id and schema-shaped inputs without arbitrary shell/script fields.

**Acceptance Scenarios**:

1. **Given** trusted tool discovery returns Jira, GitHub, and deployment tools, **When** a task author opens a Tool step, **Then** Tool choices are searchable and grouped by integration or domain.
2. **Given** a Tool is selected, **When** the author inspects the Tool panel, **Then** visible contract metadata communicates schema-backed inputs and deterministic governed execution without using Script as the Step Type concept.
3. **Given** `jira.transition_issue` is selected and the author provides an issue key, **When** trusted Jira transitions are available, **Then** the author can choose a target status from those dynamic options and the Tool inputs JSON is updated deterministically.
4. **Given** dynamic options cannot be loaded, **When** the author continues editing, **Then** manual JSON object input remains available and the unavailable dynamic option is reported without bypassing validation.
5. **Given** the author submits the Tool step, **When** the request is sent, **Then** the payload remains a `type: tool` step with a Tool payload and no Skill payload or shell/script fields.

### Edge Cases

- Tool discovery can fail; manual governed Tool authoring must remain available.
- Tool search with no matches shows an empty result state without clearing the current Tool id.
- Jira transition options require a non-empty issue key and must not guess target statuses.
- Dynamic status selection updates only the Tool inputs JSON object and preserves unrelated authored fields.

## Assumptions

- The existing `/mcp/tools` and `/mcp/tools/call` trusted endpoints are the runtime source for Tool discovery and Jira transition options.
- Tool grouping can use the namespace before the first dot in the tool id until richer catalog metadata is exposed.
- Full schema-generated forms for every tool can be layered later; this story covers contract visibility and the Jira target-status dynamic option required by MM-576.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-007 | `docs/Steps/StepTypes.md` section 5.1 `tool` | Tool steps represent bounded typed operations with declared contracts for schema, authorization, capabilities, retry, binding, validation, and errors. | In scope | FR-001, FR-002, FR-006 |
| DESIGN-REQ-008 | `docs/Steps/StepTypes.md` sections 6.3 and 8.2 | Tool selection supports search/grouping, schema-driven inputs, dynamic option providers, and validation of selected tool, version, inputs, authorization, capabilities, forbidden fields, retry, and side-effect policy. | In scope | FR-001, FR-003, FR-004, FR-005, FR-006 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` section 10.1 | User-facing Tool authoring keeps Tool/Typed Tool terminology and avoids Script as the Step Type concept. | In scope | FR-002, FR-007 |
| DESIGN-REQ-020 | `docs/Steps/StepTypes.md` section 15 | Tool authoring must not introduce arbitrary shell scripts as a first-class Step Type or require users to understand worker capability placement. | In scope | FR-002, FR-006, FR-007 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Tool step authoring panel MUST load available trusted tools from the governed tool discovery surface when available.
- **FR-002**: The Tool step authoring panel MUST keep manual typed Tool authoring available when discovery or dynamic options are unavailable.
- **FR-003**: Users MUST be able to search discovered Tool choices and see choices grouped by integration or domain.
- **FR-004**: Selecting a discovered Tool MUST populate the Tool id while preserving the existing version and JSON object inputs unless the author edits them.
- **FR-005**: For `jira.transition_issue`, after the author provides an issue key, the UI MUST request available Jira transitions through the trusted tool call surface and offer returned target statuses as selectable options.
- **FR-006**: Selecting a dynamic Jira target status MUST update the Tool inputs JSON object with the chosen status and MUST NOT guess statuses or transition IDs.
- **FR-007**: User-facing Tool authoring copy MUST describe typed governed Tool execution and MUST NOT present Script as a Step Type concept.
- **FR-008**: Submitted authored Tool steps MUST remain `type: tool` payloads with a Tool payload and no Skill payload.

### Key Entities

- **Trusted Tool Definition**: A discovered tool entry with id/name, description, and input schema metadata from the governed tool discovery surface.
- **Tool Choice Group**: A UI grouping derived from the tool namespace or domain for scanning and search.
- **Dynamic Tool Option**: A trusted option value returned for a specific Tool input, such as Jira target status options derived from `jira.get_transitions`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests verify discovered Tool choices are grouped and filterable by search text.
- **SC-002**: Frontend tests verify selecting `jira.transition_issue` and choosing a trusted target status updates Tool inputs and submits the expected Tool payload.
- **SC-003**: Frontend tests verify discovery or transition-option failures leave manual JSON Tool authoring available with a visible unavailable-state message.
- **SC-004**: Contract tests continue to verify Tool submissions do not include Skill payloads or shell/script fields.
- **SC-005**: Traceability evidence preserves `MM-576` and DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, and DESIGN-REQ-020 in MoonSpec artifacts and verification output.
