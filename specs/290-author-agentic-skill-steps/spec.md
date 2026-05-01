# Feature Specification: Author Agentic Skill Steps

**Feature Branch**: `290-author-agentic-skill-steps`  
**Created**: 2026-05-01  
**Status**: Draft  
**Input**: User description: '# MM-577 MoonSpec Orchestration Input

## Source

- Jira issue: MM-577
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Author agentic Skill steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-577-trusted-jira-get-issue-summary.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-577 from MM project
Summary: Author agentic Skill steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-577 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-577: Author agentic Skill steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.2 skill
- 6.4 Skill picker
- 8.3 Skill validation
Coverage IDs:
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-019

As a task author, I can configure a Skill step for agentic work so interpretation, implementation, planning, and synthesis use reusable skill behavior with clear runtime boundaries.

Acceptance Criteria
- Skill steps clearly communicate the agentic boundary to users.
- Skill configuration includes selector, instructions, context, runtime/model preferences, permissions, and approvals when supported.
- Invalid or unresolved skill selections fail before submission with actionable validation errors.
- Users can distinguish deterministic Tool work from agentic Skill work in authoring and review.

Requirements
- Skill steps invoke agent-facing reusable behavior rather than direct typed operations.
- Skill validation uses documented auto semantics only when supported.

## Relevant Implementation Notes

- Source design path: `docs/Steps/StepTypes.md`.
- Section 5.2 `skill`: Skill steps invoke agent-facing behavior for work requiring interpretation, planning, implementation, synthesis, or other open-ended reasoning; the authored step remains Skill even when the skill uses tools internally.
- Section 5.2 expected configuration fields: skill selector, instructions, repository/project context, runtime or model preferences when applicable, allowed tools or required capabilities when applicable, and approval/autonomy controls when applicable.
- Section 6.4 Skill picker: skill selection supports search, descriptions, and compatibility hints, and the UI must make the agentic boundary clear so users distinguish deterministic Tool work from agentic Skill work.
- Section 8.3 Skill validation: a Skill step is valid only when the selected skill exists or resolves through documented `auto` semantics, inputs validate against the skill contract, runtime compatibility and required context are known, selected permissions/tools are allowed, and approval/autonomy constraints are enforceable.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for task authoring: implement the authoring experience for agentic Skill steps, keeping Tool, Skill, and Preset semantics distinct while preserving MM-577 traceability.'

Preserved source Jira preset brief: `MM-577` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-577` and local artifact `artifacts/moonspec-inputs/MM-577-canonical-moonspec-input.md`.  
Classification: single-story runtime feature request.  
Resume decision: no existing Moon Spec feature directory preserved `MM-577`; related `specs/283-agentic-skill-steps` covers earlier `MM-564` but is not the active Jira issue, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-577 MoonSpec Orchestration Input

## Source

- Jira issue: MM-577
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Author agentic Skill steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-577-trusted-jira-get-issue-summary.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-577 from MM project
Summary: Author agentic Skill steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-577 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-577: Author agentic Skill steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.2 skill
- 6.4 Skill picker
- 8.3 Skill validation
Coverage IDs:
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-019

As a task author, I can configure a Skill step for agentic work so interpretation, implementation, planning, and synthesis use reusable skill behavior with clear runtime boundaries.

Acceptance Criteria
- Skill steps clearly communicate the agentic boundary to users.
- Skill configuration includes selector, instructions, context, runtime/model preferences, permissions, and approvals when supported.
- Invalid or unresolved skill selections fail before submission with actionable validation errors.
- Users can distinguish deterministic Tool work from agentic Skill work in authoring and review.

Requirements
- Skill steps invoke agent-facing reusable behavior rather than direct typed operations.
- Skill validation uses documented auto semantics only when supported.

## Relevant Implementation Notes

- Source design path: `docs/Steps/StepTypes.md`.
- Section 5.2 `skill`: Skill steps invoke agent-facing behavior for work requiring interpretation, planning, implementation, synthesis, or other open-ended reasoning; the authored step remains Skill even when the skill uses tools internally.
- Section 5.2 expected configuration fields: skill selector, instructions, repository/project context, runtime or model preferences when applicable, allowed tools or required capabilities when applicable, and approval/autonomy controls when applicable.
- Section 6.4 Skill picker: skill selection supports search, descriptions, and compatibility hints, and the UI must make the agentic boundary clear so users distinguish deterministic Tool work from agentic Skill work.
- Section 8.3 Skill validation: a Skill step is valid only when the selected skill exists or resolves through documented `auto` semantics, inputs validate against the skill contract, runtime compatibility and required context are known, selected permissions/tools are allowed, and approval/autonomy constraints are enforceable.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for task authoring: implement the authoring experience for agentic Skill steps, keeping Tool, Skill, and Preset semantics distinct while preserving MM-577 traceability.
```

## User Story - Agentic Skill Step Authoring

**Summary**: As a task author, I want to configure a Skill step for agentic work so interpretation, implementation, planning, and synthesis use reusable skill behavior with clear runtime boundaries instead of being represented as deterministic Tool operations.

**Goal**: Task authors can choose or enter a Skill, provide instructions and structured context, set runtime/model preferences and capability or permission hints when supported, and receive actionable validation before submission when the Skill selection or inputs are invalid.

**Independent Test**: Render the task authoring experience, configure a primary step with a Skill selector, instructions, JSON args containing `MM-577`, and required capabilities, submit the task, and verify the submitted payload preserves an executable Skill step while invalid or unresolved Skill selections and malformed Skill args are rejected before execution.

**Acceptance Scenarios**:

1. **Given** a task author configures a Skill step for `MM-577`, **When** they provide a selected skill, instructions, structured context or args, and supported runtime/capability preferences, **Then** the submitted task contains a `type: skill` executable payload with those Skill controls preserved.
2. **Given** Skill work may use tools internally, **When** a task author submits the Skill step, **Then** the authored step remains represented as agentic Skill work and is distinguishable from deterministic Tool work.
3. **Given** the Skill selector is missing, unresolved, or only valid under unsupported auto semantics, **When** the task author submits, **Then** submission is blocked with actionable validation feedback.
4. **Given** Skill args, context, required capabilities, permissions, runtime/model preferences, or approval/autonomy controls are malformed or unsupported, **When** the author submits, **Then** validation fails before execution with field-specific feedback when possible.
5. **Given** required capabilities are authored as CSV text, **When** the author submits a Skill step, **Then** each capability is trimmed, empty entries are rejected or omitted consistently, and malformed CSV values are blocked before execution.
6. **Given** a user reviews or edits a Skill step, **When** the Skill picker and configuration controls are shown, **Then** the UI communicates that the work is agentic and separate from direct Tool execution.

### Edge Cases

- Documented `auto` Skill semantics are accepted only when the selected runtime supports them and the remaining Skill inputs validate.
- Empty optional Skill context, permissions, and autonomy controls are omitted or normalized without creating hidden invalid configuration.
- Switching a step away from Skill prevents hidden Skill-only fields from being submitted as active non-Skill configuration.
- Tool-only payload fields on a Skill step are rejected instead of silently coercing the step into a Tool.

## Assumptions

- The runtime story uses the existing Create-page task authoring surface and current executable-step contract rather than adding a new remote Skill catalog browser.
- Runtime/model preferences, permissions, and approvals are preserved when supported by the existing payload surfaces; unsupported values fail validation rather than being guessed.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-009 | `docs/Steps/StepTypes.md` section 5.2 `skill` and section 6.4 Skill picker | Skill steps represent agent-facing behavior and must clearly communicate the agentic boundary so users can distinguish Skill work from Tool work. | In scope | FR-001, FR-002, FR-007 |
| DESIGN-REQ-010 | `docs/Steps/StepTypes.md` section 5.2 `skill` | Skill configuration includes selector, instructions, relevant context, runtime or model preferences, allowed tools/capabilities, and approval/autonomy controls when supported. | In scope | FR-001, FR-004 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` section 8.3 Skill validation | Skill validation requires resolvable selection, valid inputs, known runtime compatibility, required context, allowed permissions/tools, and enforceable approval/autonomy constraints. | In scope | FR-003, FR-004, FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to author a Skill step with a Skill selector or documented supported `auto` selector, instructions, and optional Skill-specific context, args, runtime/model preference, permission/tool, capability, and approval/autonomy fields when supported.
- **FR-002**: System MUST submit authored Skill steps as executable Skill work and keep them visibly distinct from deterministic Tool steps in authoring and review.
- **FR-003**: System MUST reject Skill steps with missing required instructions, missing or unresolved skill selector data, unsupported auto semantics, non-object Skill inputs, or malformed required capabilities before task submission.
- **FR-004**: System MUST preserve valid Skill selector, instructions, context or args, runtime/model preferences, allowed tools or permissions, required capabilities, and approval/autonomy metadata in the submitted task payload when those fields are supported.
- **FR-005**: System MUST validate Skill runtime compatibility, required context, permissions/tools, and approval/autonomy constraints before execution whenever those constraints are available at submission time.
- **FR-006**: System MUST reject Tool-only payload fields on Skill steps before execution.
- **FR-007**: User-facing Skill configuration MUST communicate that Skill work is agentic and reusable, not a direct typed Tool operation.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-577` and the canonical Jira preset brief for traceability.

### Key Entities

- **Skill Step Draft**: A task step draft with Step Type `Skill`, instructions, selected skill or supported auto selector, and optional agentic controls.
- **Skill Payload**: The submitted executable object carrying Skill selector and Skill-specific inputs for downstream validation and runtime materialization.
- **Agentic Controls**: Optional context, args, runtime/model preferences, allowed tools or permissions, required capabilities, and approval/autonomy settings that shape Skill execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A task author can submit a valid `MM-577` Skill step and the resulting payload preserves `type: skill` plus Skill-specific payload data.
- **SC-002**: Invalid Skill submissions for missing selector, unsupported auto semantics, malformed inputs, malformed required capabilities, or Tool-only payload fields are rejected before execution.
- **SC-003**: Existing Tool and Preset authoring tests continue to pass, demonstrating Skill changes do not regress adjacent Step Types.
- **SC-004**: `DESIGN-REQ-009`, `DESIGN-REQ-010`, `DESIGN-REQ-019`, and `MM-577` are traceably covered by spec requirements, implementation tasks, and verification evidence.
