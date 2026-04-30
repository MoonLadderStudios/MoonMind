# Feature Specification: Add Step Type Authoring Controls

**Feature Branch**: `287-step-type-authoring-controls`
**Created**: 2026-04-30
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-568 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-568` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-568` and local artifact `artifacts/moonspec/mm-568-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-568`, so `Specify` was the first incomplete MM-568 stage.

## Original Preset Brief

```text
Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-568 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-568: Add Step Type authoring controls

Current Jira status at orchestration input fetch time: In Progress
Issue type: Story
Labels: moonmind-workflow-mm-faa3480e-4aa5-47f7-ab3d-d505fb116446

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 1. Purpose
- 2. Desired-State Summary
- 4. Core Invariants
- 6.1 Step editor
- 6.2 Step type picker
- 10. Naming Policy
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-008
- DESIGN-REQ-017
As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.
Acceptance Criteria
- The editor shows exactly one Step Type selector with Tool, Skill, and Preset options.
- Changing Step Type changes the visible type-specific form.
- Compatible fields are preserved across changes where possible.
- Incompatible meaningful data is either visibly discarded or requires confirmation before removal.
- Primary UI labels use Step Type and avoid Capability, Activity, Invocation, Command, or Script as the umbrella label.
Requirements
- Every authored step must have one selected Step Type.
- The selected Step Type must drive available sub-options.
- The product-facing picker must use Tool, Skill, and Preset helper text from the design.
```

## User Story - Step Type Authoring Controls

**Summary**: As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.

**Goal**: Task authors configure each step through one clear Step Type selector that drives the visible configuration form, preserves compatible shared fields, and makes incompatible type-specific data removal explicit.

**Independent Test**: Render the Create page step editor, verify one Step Type selector with Tool, Skill, and Preset options, switch among types, and confirm the visible form, preserved shared instructions, explicit incompatible-data discard notice, helper copy, and label vocabulary.

**Acceptance Scenarios**:

1. **Given** a task author opens the step editor, **When** they inspect an authored step, **Then** exactly one user-facing Step Type selector is visible for that step.
2. **Given** the Step Type selector is visible, **When** the author inspects the options, **Then** Tool, Skill, and Preset are available with concise helper text from the source design or equivalent product copy.
3. **Given** the author changes Step Type, **When** the selected value changes to Tool, Skill, or Preset, **Then** only the matching type-specific configuration form is visible below the selector.
4. **Given** the author entered shared step instructions and type-specific values, **When** the author changes Step Type, **Then** shared instructions are preserved and incompatible meaningful type-specific data is visibly discarded or requires confirmation before removal.
5. **Given** the primary Step Type UI is visible, **When** labels and helper copy are inspected, **Then** the umbrella label is Step Type and the primary choices do not use Capability, Activity, Invocation, Command, or Script.

### Edge Cases

- A newly added step starts with one valid selected Step Type and can be changed independently from other steps.
- Preset-specific selections and preview state are scoped to the step and cleared when the step changes away from Preset.
- Tool-specific values are not submitted or silently retained as active values after changing to Skill or Preset.
- Skill-specific values are not submitted or silently retained as active values after changing to Tool or Preset.
- The separate preset management surface, if present, is not treated as the canonical Step Type authoring selector.

## Assumptions

- Runtime mode applies: this story verifies Create page behavior, not documentation-only wording.
- Existing adjacent Step Type stories may already satisfy part of the selector and helper-copy behavior, but MM-568 must preserve its own Jira traceability and complete the incompatible-data handling acceptance criterion.
- Instructions are treated as compatible shared step data across Tool, Skill, and Preset.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | docs/Steps/StepTypes.md sections 1, 2, 4 | Every authored step has exactly one Step Type that determines what the step represents and which fields are shown. | In scope | FR-001, FR-003, FR-006 |
| DESIGN-REQ-002 | docs/Steps/StepTypes.md sections 2, 6.1, 6.2 | The Step Type control offers Tool, Skill, and Preset and renders type-specific configuration below the selector. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-008 | docs/Steps/StepTypes.md sections 4, 6.1 | Changing Step Type preserves compatible fields and explicitly handles incompatible meaningful data before or when it is removed. | In scope | FR-004 |
| DESIGN-REQ-017 | docs/Steps/StepTypes.md section 10 | Primary authoring labels use Step Type, Tool, Skill, and Preset and avoid internal runtime terminology as the umbrella label. | In scope | FR-002, FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The step editor MUST expose exactly one user-facing Step Type selector for each ordinary authored step.
- **FR-002**: The Step Type selector MUST offer Tool, Skill, and Preset with concise helper text matching the source design intent.
- **FR-003**: The selected Step Type MUST determine which type-specific configuration form is visible below the selector.
- **FR-004**: Changing Step Type MUST preserve compatible shared fields and MUST either visibly discard incompatible meaningful type-specific data or require confirmation before removing it.
- **FR-005**: Primary Step Type labels and helper copy MUST use Step Type, Tool, Skill, and Preset and MUST NOT use Capability, Activity, Invocation, Command, or Script as the umbrella label.
- **FR-006**: Each authored step MUST maintain its own selected Step Type and type-specific draft state independently from other authored steps.

### Key Entities

- **Step Draft**: A user-authored task step with shared instructions, selected Step Type, and type-specific draft state.
- **Step Type**: The user-facing discriminator with Tool, Skill, and Preset options.
- **Type-Specific Configuration Form**: The visible controls controlled by the selected Step Type.
- **Discard Notice**: User-visible feedback that incompatible type-specific data was removed after a Step Type change.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests verify a rendered step has one accessible Step Type selector with Tool, Skill, and Preset options.
- **SC-002**: Frontend tests verify switching among Tool, Skill, and Preset changes the visible configuration form while preserving shared instructions.
- **SC-003**: Frontend tests verify incompatible type-specific values are cleared with visible feedback or guarded by confirmation when Step Type changes.
- **SC-004**: Frontend tests verify primary Step Type UI avoids Capability, Activity, Invocation, Command, and Script as umbrella labels.
- **SC-005**: Frontend tests verify multiple authored steps keep independent Step Type and Preset state.
- **SC-006**: Final verification preserves Jira issue key `MM-568` and the original Jira preset brief in MoonSpec artifacts and delivery metadata.
