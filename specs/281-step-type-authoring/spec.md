# Feature Specification: Present Step Type Authoring

**Feature Branch**: `281-step-type-authoring`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-562 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-562` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-562`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-562` under `specs/`, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-562 MoonSpec Orchestration Input

## Source

- Jira issue: MM-562
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Present Step Type authoring in the task step editor
- Trusted fetch tool: jira.get_issue
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the response did not expose recommended preset instructions or a normalized preset brief.

## Canonical MoonSpec Feature Request

Jira issue: MM-562 from MM project
Summary: Present Step Type authoring in the task step editor
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-562 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-562: Present Step Type authoring in the task step editor

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 1. Purpose
- 2. Desired-State Summary
- 3. Terminology
- 4. Core Invariants
- 6. User Experience Contract
- 10. Naming Policy

Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-009
- DESIGN-REQ-018

User Story
As a task author, I can choose Tool, Skill, or Preset from a single Step Type control so the editor shows the right fields without requiring internal runtime vocabulary.

Acceptance Criteria
- The step editor exposes exactly one user-facing Step Type selector for ordinary step authoring.
- The selector offers Tool, Skill, and Preset using the documented helper text or equivalent concise copy.
- Changing Step Type changes the type-specific controls below the selector.
- Meaningful incompatible data is not silently lost when the user changes Step Type.
- Temporal Activity and capability terminology remain absent from the primary user-facing Step Type selector.

Requirements
- Every authored step in the editor has one selected Step Type.
- The selected Step Type controls available sub-options and validation surface.
- UI copy consistently uses Step Type, Tool, Skill, and Preset for the authoring model.
```

## User Story - Step Type Authoring Presentation

**Summary**: As a task author, I can choose Tool, Skill, or Preset from a single Step Type control so the editor shows the right fields without requiring internal runtime vocabulary.

**Goal**: Ordinary task authoring presents one clear Step Type choice per step, explains the available choices with concise product copy, and updates the configuration area without exposing Temporal or capability terminology as the primary discriminator.

**Independent Test**: Render the Create page step editor, inspect the Step Type selector, switch among Tool, Skill, and Preset, and verify the selector choices, helper copy, visible configuration controls, instruction preservation, and absence of internal discriminator labels.

**Acceptance Scenarios**:

1. **Given** a task author opens the step editor, **When** they inspect an authored step, **Then** the step exposes exactly one user-facing control named Step Type.
2. **Given** the Step Type selector is visible, **When** the author inspects its choices, **Then** Tool, Skill, and Preset are available with concise helper copy that describes each choice.
3. **Given** the author changes Step Type, **When** the selected value becomes Tool, Skill, or Preset, **Then** only the matching type-specific controls are shown below the selector.
4. **Given** the author has entered step instructions and type-specific values, **When** the author changes Step Type, **Then** compatible instructions remain available and incompatible hidden fields are not silently submitted.
5. **Given** the primary Step Type selector is visible, **When** its label, options, and helper copy are inspected, **Then** it uses Step Type, Tool, Skill, and Preset rather than Temporal Activity or capability terminology.

### Edge Cases

- A newly added step has one valid selected Step Type and can be changed independently from other steps.
- Preset selection remains scoped to the step where Step Type is Preset.
- Advanced Skill fields that become hidden after a Step Type change are not submitted as active configuration for non-Skill steps.
- The separate Task Presets management area, if present, is not the canonical Step Type selector for ordinary step authoring.

## Assumptions

- Runtime mode applies: this story verifies and completes Create page task-authoring behavior, not documentation-only wording.
- Existing MM-556 and MM-558 Step Type implementation work may satisfy most behavior, but MM-562 must preserve its own Jira traceability and verify the specific helper-copy acceptance criterion.
- The current Tool implementation may remain a typed-operation placeholder when no concrete tool picker is available, as long as the Step Type authoring model and validation surface remain clear.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | docs/Steps/StepTypes.md sections 1, 2, 4 | Every authored step has exactly one Step Type that determines what the step represents and which fields are shown. | In scope | FR-001, FR-003, FR-006 |
| DESIGN-REQ-002 | docs/Steps/StepTypes.md sections 2, 3, 6.1, 6.2 | The Step Type control offers Tool, Skill, and Preset and renders type-specific configuration below the selector. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-009 | docs/Steps/StepTypes.md section 6.1 | Changing Step Type preserves compatible fields and prevents meaningful incompatible data from being silently lost or submitted. | In scope | FR-004 |
| DESIGN-REQ-018 | docs/Steps/StepTypes.md section 10 | Primary authoring copy uses Step Type, Tool, Skill, and Preset and avoids Temporal Activity or capability terminology as the step discriminator. | In scope | FR-002, FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The step editor MUST expose exactly one user-facing Step Type selector for each ordinary authored step.
- **FR-002**: The Step Type selector MUST offer Tool, Skill, and Preset with documented helper text or equivalent concise copy for the choices.
- **FR-003**: The selected Step Type MUST determine which type-specific controls appear below the selector.
- **FR-004**: Changing Step Type MUST preserve compatible instructions and MUST prevent hidden incompatible type-specific fields from being silently submitted.
- **FR-005**: Primary Step Type selector copy MUST use Step Type, Tool, Skill, and Preset and MUST NOT use Temporal Activity or capability terminology as the primary discriminator.
- **FR-006**: Each authored step MUST maintain its own selected Step Type and type-specific draft state independently from other steps.

### Key Entities

- **Step Draft**: A user-authored task step with instructions, selected Step Type, and type-specific draft state.
- **Step Type**: The user-facing discriminator with values Tool, Skill, and Preset.
- **Type-Specific Controls**: The visible configuration controls controlled by the selected Step Type.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests verify a rendered step has one accessible Step Type selector with exactly Tool, Skill, and Preset options.
- **SC-002**: Frontend tests verify the Step Type choices expose concise helper copy for Tool, Skill, and Preset.
- **SC-003**: Frontend tests verify switching among Tool, Skill, and Preset changes visible configuration without removing existing instructions.
- **SC-004**: Frontend tests verify hidden Skill fields are not submitted for non-Skill Step Types.
- **SC-005**: Frontend tests verify independent step-scoped Step Type and Preset state.
