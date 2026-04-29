# Feature Specification: Validate Tool and Skill Executable Steps

**Feature Branch**: `277-validate-tool-skill-executable-steps`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-557 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-557` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-557` and local artifact `artifacts/moonspec-inputs/MM-557-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-557` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-557 MoonSpec Orchestration Input

## Source

- Jira issue: MM-557
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Validate Tool and Skill executable steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`; potentially related custom fields `Implementation plan` and `Source` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-557 from MM project
Summary: Validate Tool and Skill executable steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-557 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-557: Validate Tool and Skill executable steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.1 `tool`
- 5.2 `skill`
- 8.1 Common validation
- 8.2 Tool validation
- 8.3 Skill validation
- 9. Jira Example
- 15. Non-Goals

Coverage IDs:
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-013
- DESIGN-REQ-017

As an operator, I want Tool and Skill steps validated against their distinct contracts so deterministic operations and agentic work are configured safely before execution.

Acceptance Criteria
- Tool validation requires an existing resolvable tool, schema-valid inputs, required authorization, worker capabilities, known side-effect policy, and no forbidden fields.
- Skill validation requires a resolvable skill or documented auto semantics, contract-valid inputs, known runtime compatibility, required context, allowed permissions, and enforceable autonomy controls.
- Common validation requires stable local identity, title or generated display label, Step Type, type-specific payload, and visible errors before submission.
- Arbitrary shell snippets are rejected unless the selected Tool is an approved typed command tool with bounded inputs and policy.
- Jira transition examples validate as Tool steps while Jira triage or implementation examples validate as Skill steps.

Requirements
- Tool steps are deterministic typed operations, not scripts.
- Skill steps represent agentic work even when they may use tools internally.
- Executable steps are validated before submission.
- Tool and Skill are not treated as interchangeable merely because both may map into plan nodes.
```

## User Story - Validate Executable Step Contracts

**Summary**: As an operator, I want Tool and Skill executable steps validated against distinct contracts so deterministic operations and agentic work are configured safely before execution.

**Goal**: Task template and preset submission paths reject malformed executable steps early, keep Tool and Skill semantics distinct, and preserve deterministic Jira operations as Tool steps while agentic Jira work remains Skill steps.

**Independent Test**: Create and save task step templates containing Tool and Skill steps, then verify valid examples persist and expand with their declared Step Type while mixed, malformed, forbidden, or arbitrary shell-shaped steps fail with visible validation errors.

**Acceptance Scenarios**:

1. **Given** a template step declares Step Type `tool`, **When** the template is created or expanded, **Then** the step is accepted only if it declares a resolvable typed tool identifier, object inputs, known authorization/capability/policy metadata where supplied, and no forbidden fields.
2. **Given** a template step declares Step Type `skill`, **When** the template is created or saved from a task, **Then** the step is accepted only if it declares a resolvable skill selector or documented `auto` selector, object arguments, runtime/context metadata where supplied, allowed permissions/capabilities, and enforceable autonomy metadata when present.
3. **Given** a step omits explicit Step Type but otherwise matches the current Skill shape, **When** existing templates are loaded, **Then** the system treats the authored executable step as Skill and validates it through the Skill contract without accepting Tool-only fields.
4. **Given** a step mixes Tool and Skill payloads or selects Tool without a Tool payload, **When** the template is created, saved, or expanded, **Then** validation fails before the template can be used.
5. **Given** a step includes arbitrary shell snippets as command/script fields, **When** the step is validated, **Then** validation rejects it unless it is represented as an approved typed Tool with bounded object inputs and policy metadata.
6. **Given** Jira examples are authored, **When** Jira transition is represented as a Tool step and Jira triage/implementation is represented as a Skill step, **Then** validation accepts the correctly typed examples and rejects the swapped or mixed variants.

### Edge Cases

- A legacy skill-shaped template has no explicit `type`; it must remain valid only as Skill-shaped data and must not silently gain Tool semantics.
- A Tool step uses `tool.name` instead of `tool.id`; the identifier is still resolvable if non-empty and its inputs remain an object.
- A Tool step uses `tool.args`; it is normalized as Tool inputs only when the value is an object.
- A Skill step includes empty `requiredCapabilities`; empty values are ignored, but non-list values are rejected.
- A template include remains a Preset expansion mechanism and is not treated as a Tool or Skill executable step.

## Assumptions

- Runtime mode applies: this story changes executable step validation behavior, not only documentation.
- The first implementation slice targets task step template create/save/expand boundaries because those are the current persisted preset authoring paths where executable step blueprints enter the system.
- Existing Create page Step Type UI from MM-556 is treated as input to this backend validation story rather than reimplemented here.
- Full live registry authorization checks can be added behind provider-specific registries later; this story requires deterministic structural and metadata validation at the template service boundary.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | docs/Steps/StepTypes.md section 5.1 | Tool steps are typed executable operations with name/version, schemas, authorization, capabilities, retry policy, execution binding, and validation/error model. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-004 | docs/Steps/StepTypes.md section 5.2 | Skill steps represent agent-facing behavior with selector, instructions, context, runtime preferences, allowed tools/capabilities, and autonomy controls. | In scope | FR-005, FR-006, FR-007 |
| DESIGN-REQ-005 | docs/Steps/StepTypes.md section 8.1 | Common validation requires stable identity or display label, Step Type, type-specific payload, and surfaced validation errors before submission. | In scope | FR-008, FR-009, FR-010 |
| DESIGN-REQ-013 | docs/Steps/StepTypes.md sections 8.2, 8.3, and 9 | Tool and Skill validation must stay distinct; Jira transitions are Tool steps while Jira triage/implementation is Skill work. | In scope | FR-011, FR-012, FR-013 |
| DESIGN-REQ-017 | docs/Steps/StepTypes.md section 15 | Arbitrary shell scripts must not become a first-class Step Type. | In scope | FR-014 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept an authored Tool step only when `type` is `tool` and a Tool payload object is present.
- **FR-002**: System MUST require Tool steps to declare a non-empty tool identifier through `tool.id` or `tool.name`.
- **FR-003**: System MUST require Tool step inputs, when present through `tool.inputs` or `tool.args`, to be an object.
- **FR-004**: System MUST preserve Tool step identifier, version, object inputs, and declared metadata needed for authorization, capabilities, side-effect, retry, execution, and validation policy.
- **FR-005**: System MUST accept an authored Skill step only when it has an explicit `type: skill` or uses the existing skill-shaped default and a Skill payload object or documented `auto` selector.
- **FR-006**: System MUST require Skill step args, when present, to be an object and Skill required capabilities, when present, to be a list.
- **FR-007**: System MUST preserve Skill selector, args, required capabilities, context, permissions, and autonomy metadata when supplied in the allowed Skill payload.
- **FR-008**: System MUST reject executable steps with unsupported Step Type values.
- **FR-009**: System MUST reject steps that mix Tool and Skill payloads contrary to the selected Step Type.
- **FR-010**: System MUST surface validation failures through existing task template validation errors before templates are persisted or expanded.
- **FR-011**: System MUST accept a Jira transition example authored as a Tool step with typed inputs.
- **FR-012**: System MUST accept a Jira triage or implementation example authored as a Skill step.
- **FR-013**: System MUST reject Jira transition examples authored as Skill steps when the payload declares a deterministic Tool operation, and reject Jira agentic examples authored as Tool steps when they use Skill-only fields.
- **FR-014**: System MUST reject arbitrary shell command/script snippets unless represented as an approved typed Tool payload with bounded object inputs and policy metadata.

### Key Entities

- **Executable Step Blueprint**: A task template step definition with common step fields and one selected Step Type.
- **Tool Step Payload**: Typed deterministic operation metadata including identifier, version, object inputs, and optional policy metadata.
- **Skill Step Payload**: Agentic work metadata including selector, object args, capabilities, context, permissions, and autonomy controls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests cover valid Tool, valid Skill, and legacy Skill-shaped executable step validation.
- **SC-002**: Unit tests cover mixed Tool/Skill payload rejection and unsupported Step Type rejection.
- **SC-003**: Unit tests cover arbitrary shell-shaped step rejection.
- **SC-004**: Template expansion preserves valid Tool and Skill Step Type payloads after rendering.
- **SC-005**: Existing seeded template tests continue to pass without requiring checked-in seed template rewrites.
