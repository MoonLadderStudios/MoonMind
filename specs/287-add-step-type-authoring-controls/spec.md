# Feature Specification: Add Step Type Authoring Controls

**Feature Branch**: `287-add-step-type-authoring-controls`  
**Created**: 2026-04-30  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-568 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-568 MoonSpec Orchestration Input

## Source

- Jira issue: MM-568
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add Step Type authoring controls
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the response did not expose recommended preset instructions or a normalized preset brief.

## Canonical MoonSpec Feature Request

Jira issue: MM-568 from MM project
Summary: Add Step Type authoring controls
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-568 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-568: Add Step Type authoring controls

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

Story
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

Orchestration constraints:
- Jira Orchestrate always runs as a runtime implementation workflow.
- If the brief points at an implementation document, treat it as runtime source requirements.
- Classification: single-story runtime feature request.
- Resume decision: no existing Moon Spec directory preserved MM-568, so Specify is the first incomplete MM-568 stage."

Preserved source Jira preset brief: `MM-568` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-568`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-568`; nearby Step Type feature directories preserve different Jira keys and are treated as implementation evidence only.

## User Story - Step Type Authoring Controls

**Summary**: As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.

**Goal**: Ordinary task authoring exposes one clear Step Type selector per step, uses the documented Tool, Skill, and Preset choices, renders only the relevant type-specific controls, preserves compatible authoring data, and avoids internal runtime terminology as the primary step discriminator.

**Independent Test**: Render the Create page step editor, inspect the Step Type selector, switch among Tool, Skill, and Preset, and verify the options, helper copy, visible configuration controls, instruction preservation, hidden-field safeguards, and absence of internal umbrella labels.

**Acceptance Scenarios**:

1. **Given** the step editor is visible, **When** a task author inspects an authored step, **Then** the step exposes exactly one user-facing Step Type selector.
2. **Given** the Step Type selector is visible, **When** the author inspects its options, **Then** Tool, Skill, and Preset are available with helper copy consistent with the source design.
3. **Given** the author changes Step Type, **When** the selected value becomes Tool, Skill, or Preset, **Then** only the matching type-specific configuration controls are visible.
4. **Given** the author has entered step instructions, **When** the author changes Step Type, **Then** compatible instructions remain available.
5. **Given** type-specific Skill values are hidden after switching away from Skill, **When** the step is submitted or validated as another Step Type, **Then** incompatible hidden Skill values are not silently submitted as active configuration.
6. **Given** the primary Step Type selector is visible, **When** its label, options, and helper copy are inspected, **Then** it uses Step Type, Tool, Skill, and Preset and avoids Capability, Activity, Invocation, Command, and Script as umbrella labels.

### Edge Cases

- A newly added step has one valid selected Step Type and can be changed independently from other steps.
- Preset selection and preview state remain scoped to the step whose Step Type is Preset.
- A Tool step without a selected governed Tool surfaces a validation message instead of submitting hidden Skill data.
- The Preset management area, if present, is not the canonical Step Type selector for ordinary step authoring.

## Assumptions

- Runtime mode applies: this story verifies and completes Create page task-authoring behavior, not documentation-only wording.
- Existing Step Type work for earlier Jira issues may satisfy most behavior; this feature still preserves MM-568 traceability and verifies the MM-568 acceptance criteria.
- The current Tool configuration may remain a governed Tool input surface or placeholder when no richer typed picker is available, as long as the Step Type authoring controls and validation surface are clear.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Steps/StepTypes.md` sections 1, 2, and 4 | Every authored step has exactly one Step Type that controls what the step represents, which fields are shown, validation, and executable or preset behavior. | In scope | FR-001, FR-003, FR-006 |
| DESIGN-REQ-002 | `docs/Steps/StepTypes.md` sections 2, 6.1, and 6.2 | The step editor must expose Tool, Skill, and Preset from the same Step Type control and render type-specific configuration below it. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-008 | `docs/Steps/StepTypes.md` sections 4 and 6.1 | Changing Step Type must preserve compatible fields or clearly discard/confirm incompatible fields before meaningful data is lost. | In scope | FR-004, FR-006 |
| DESIGN-REQ-017 | `docs/Steps/StepTypes.md` section 10 | Primary user-facing labels must use Step Type, Tool, Skill, and Preset and avoid Capability, Activity, Invocation, Command, and Script as the umbrella discriminator. | In scope | FR-002, FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The step editor MUST expose exactly one user-facing Step Type selector for each ordinary authored step.
- **FR-002**: The Step Type selector MUST offer Tool, Skill, and Preset with helper text consistent with the source design.
- **FR-003**: The selected Step Type MUST determine which type-specific configuration controls appear below the selector.
- **FR-004**: Changing Step Type MUST preserve compatible instructions across Tool, Skill, and Preset changes.
- **FR-005**: Primary Step Type selector copy MUST use Step Type, Tool, Skill, and Preset and MUST NOT use Capability, Activity, Invocation, Command, or Script as umbrella labels.
- **FR-006**: Incompatible hidden type-specific fields MUST NOT be silently submitted as active configuration after a Step Type change.
- **FR-007**: Each authored step MUST maintain its selected Step Type and Preset draft state independently from other authored steps.

### Key Entities

- **Step Draft**: A user-authored task step with instructions, selected Step Type, and type-specific draft values.
- **Step Type**: The user-facing discriminator with values Tool, Skill, and Preset.
- **Type-Specific Configuration**: The visible controls and draft values governed by the selected Step Type.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests verify a rendered step has one accessible Step Type selector with exactly Tool, Skill, and Preset options.
- **SC-002**: Frontend tests verify the Step Type choices expose source-consistent helper copy for Tool, Skill, and Preset.
- **SC-003**: Frontend tests verify switching among Tool, Skill, and Preset changes visible configuration without removing existing instructions.
- **SC-004**: Frontend tests verify hidden Skill fields are not submitted for non-Skill Step Types.
- **SC-005**: Frontend tests verify independent step-scoped Preset state.
- **SC-006**: Final verification preserves Jira issue key `MM-568` and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, and DESIGN-REQ-017 across MoonSpec artifacts and delivery evidence.
