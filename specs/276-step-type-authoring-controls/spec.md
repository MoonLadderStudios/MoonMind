# Feature Specification: Step Type Authoring Controls

**Feature Branch**: `276-step-type-authoring-controls`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-556 as the canonical Moon Spec orchestration input. Implement MM-556: Unify Step Type authoring controls. Build the task authoring experience so the step editor has exactly one user-facing Step Type control covering Tool, Skill, and Preset. The selected Step Type must determine which type-specific configuration UI is shown. When the user changes Step Type, preserve compatible fields where possible and clearly handle incompatible fields before data is lost. User-facing copy must consistently use Step Type for the discriminator and avoid exposing internal runtime terminology such as Capability, Activity, Invocation, Command, or Script as the step type label. Preserve MM-556 in generated spec artifacts and pull request references. Source Reference: docs/Steps/StepTypes.md sections 1, 2, 3, 4, 6.1, 6.2, and 10. Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-015."

## User Story - Unified Step Type Selection

**Summary**: As a task author, I want one Step Type control in the step editor so I can choose Tool, Skill, or Preset without learning MoonMind internal runtime terminology.

**Goal**: Task authors can choose the kind of work a step represents from a single Step Type discriminator and see only the configuration controls relevant to that selected type.

**Independent Test**: Render the task Create page, inspect the primary step editor, switch between Tool, Skill, and Preset, and verify that exactly one Step Type control drives the visible configuration area while preserving compatible step instructions.

**Acceptance Scenarios**:

1. **Given** the Create page step editor is visible, **When** the user inspects a step, **Then** the step exposes one Step Type control with Tool, Skill, and Preset choices.
2. **Given** Step Type is Skill, **When** the user views the step configuration, **Then** Skill-specific controls are visible and Tool/Preset-specific controls are hidden.
3. **Given** Step Type is Tool, **When** the user views the step configuration, **Then** Tool-specific controls are visible and Skill/Preset-specific controls are hidden.
4. **Given** Step Type is Preset, **When** the user views the step configuration, **Then** Preset-specific selection and apply controls are visible in the step editor and Skill/Tool-specific controls are hidden.
5. **Given** a user has entered step instructions, **When** the user changes Step Type, **Then** compatible instructions remain available and incompatible type-specific fields are not silently submitted while hidden.
6. **Given** the step editor is visible, **When** labels and primary helper copy are inspected, **Then** the discriminator is called Step Type and does not use Capability, Activity, Invocation, Command, or Script as the step type label.

### Edge Cases

- A newly added step defaults to a valid Step Type and remains independently editable.
- Hidden advanced Skill fields are not submitted after switching away from Skill.
- A Preset step can use the existing preset expansion flow without requiring a separate authoring section outside the step editor.

## Assumptions

- Runtime mode applies: this story changes the Create page task-authoring behavior and validates it with frontend tests.
- The first delivered slice may keep the existing execution payload semantics for executable Skill steps while moving preset use into the step-authoring surface.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | docs/Steps/StepTypes.md sections 1, 2, 4 | Every authored step has exactly one Step Type and the selected type controls what the step represents and which fields are shown. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-002 | docs/Steps/StepTypes.md sections 2, 6.1, 6.2 | The step editor must expose Tool, Skill, and Preset through one Step Type picker and render type-specific configuration below it. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-015 | docs/Steps/StepTypes.md section 10 | UI copy must use Step Type and avoid internal discriminator terms such as Capability, Activity, Invocation, Command, and Script. | In scope | FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The step editor MUST expose exactly one user-facing Step Type control per step with Tool, Skill, and Preset options.
- **FR-002**: The selected Step Type MUST determine which type-specific configuration area is visible for that step.
- **FR-003**: Users MUST be able to choose Skill and configure the existing skill selector and advanced skill fields only while Skill is the selected Step Type.
- **FR-004**: Users MUST be able to choose Preset from the step editor and access preset selection/application controls without relying on a separate preset-use section.
- **FR-005**: Primary step-discriminator UI copy MUST use Step Type and MUST NOT use Capability, Activity, Invocation, Command, or Script as the step type label.
- **FR-006**: Changing Step Type MUST preserve compatible instructions and MUST prevent hidden incompatible type-specific fields from being submitted silently.

### Key Entities

- **Step Draft**: A user-authored step in the Create page, including instructions, selected Step Type, and any type-specific draft fields.
- **Step Type**: The user-facing discriminator with Tool, Skill, and Preset values.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A rendered step has one accessible control named Step Type with exactly Tool, Skill, and Preset options.
- **SC-002**: Switching among Tool, Skill, and Preset changes the visible configuration area without removing existing instructions.
- **SC-003**: Frontend tests cover Skill, Tool, and Preset Step Type rendering and hidden Skill field submission behavior.
- **SC-004**: The Create page no longer presents preset use as a separate canonical authoring section outside the step editor.
