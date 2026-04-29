# Feature Specification: Governed Tool Step Authoring

**Feature Branch**: `282-governed-tool-steps`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-563 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-563` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-563` and local artifact `artifacts/moonspec-inputs/MM-563-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-563` under `specs/`, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-563 MoonSpec Orchestration Input

## Source

- Jira issue: MM-563
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Author and validate governed Tool steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`; potentially related custom field `Implementation plan` was present but empty.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-563-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-563 from MM project
Summary: Author and validate governed Tool steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-563 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-563: Author and validate governed Tool steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.1 `tool`
- 6.3 Tool picker
- 8.2 Tool validation
- 9. Jira Example
- 10.1 Keep `Tool`
- 15. Non-Goals
Coverage IDs:
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-015
As a task author, I can configure a Tool step as a typed governed operation with schema-backed inputs and policy validation, while arbitrary shell remains excluded from Step Type authoring.
Acceptance Criteria
- A valid Tool step can be authored with tool id, version or resolvable version, and schema-valid inputs.
- Invalid Tool steps fail before submission with actionable validation errors.
- Tool forms are driven by the selected tool contract rather than free-form script fields.
- Arbitrary shell snippets cannot be submitted as a Step Type.
- Tool terminology remains the user-facing label for typed executable operations.
Requirements
- Tool definitions declare name/version, schemas, authorization, worker capabilities, retry policy, binding, validation, and error model.
- Tool validation rejects missing tools, invalid inputs, missing authorization, unavailable capabilities, forbidden fields, and unknown retry or side-effect policy.
- Tool steps represent deterministic bounded work such as Jira transitions or GitHub reviewer requests.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

## User Story - Governed Tool Authoring

**Summary**: As a task author, I want to configure Tool steps as typed governed operations so deterministic work can be submitted with contract-shaped inputs instead of arbitrary script text.

**Goal**: Task authors can select Tool as the Step Type, enter a tool identifier, optional version, and JSON object inputs, and submit a payload that preserves the typed Tool binding while blocking malformed or script-like Tool submissions.

**Independent Test**: Render the Create page, switch a step to Tool, enter a typed Jira tool id, optional version, and JSON inputs, submit the task, and verify the submitted payload contains a Tool step with those fields while invalid JSON, missing tool id, and shell-like fields are rejected before execution.

**Acceptance Scenarios**:

1. **Given** a task author selects Step Type `Tool`, **When** they enter a tool id, optional version, schema-shaped JSON inputs, and submit, **Then** the submitted task contains a `type: tool` step with a Tool payload and no Skill payload.
2. **Given** a Tool step has no selected tool id, **When** the author submits, **Then** submission is blocked with an actionable Tool selection message.
3. **Given** a Tool step has invalid JSON inputs, **When** the author submits, **Then** submission is blocked with an actionable JSON object validation message.
4. **Given** a Tool step input includes forbidden shell/script/command fields, **When** the author submits, **Then** submission is blocked before execution.
5. **Given** the Create page presents the Tool configuration surface, **When** the author inspects labels and helper copy, **Then** the user-facing Step Type remains `Tool` and does not present `Script` as the concept label.

### Edge Cases

- Empty Tool inputs are allowed only as an empty JSON object after a tool id is selected.
- Tool input JSON must parse to an object, not an array, string, or primitive value.
- Switching away from Tool preserves authored instructions while preventing hidden Skill payloads from being submitted for Tool steps.

## Assumptions

- The first runtime slice uses text entry for the tool id and version because no runtime tool catalog endpoint is exposed to the Create page boot payload yet.
- Schema-backed form generation can be layered on the same `tool.inputs` JSON object contract once tool catalog metadata is available in the Create page.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | `docs/Steps/StepTypes.md` section 5.1 `tool` | Tool steps represent explicit, bounded, typed operations with declared contracts and direct deterministic work. | In scope | FR-001, FR-002, FR-004 |
| DESIGN-REQ-004 | `docs/Steps/StepTypes.md` sections 6.3, 8.2, and 9 | Tool authoring supports a Tool picker/form concept, validates tool existence, resolvable version, schema-valid inputs, authorization/capability policy, and Jira deterministic examples. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-015 | `docs/Steps/StepTypes.md` sections 10.1 and 15 | The UI keeps Tool as the user-facing term and arbitrary shell scripts are not a first-class Step Type. | In scope | FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to author a Tool step with a tool id, optional version, and JSON object inputs.
- **FR-002**: System MUST submit authored Tool steps as `type: tool` entries with a Tool payload and no Skill payload.
- **FR-003**: System MUST reject Tool steps with missing tool id or invalid/non-object JSON inputs before task submission.
- **FR-004**: System MUST preserve valid Tool id, version, and input fields in the submitted task payload.
- **FR-005**: System MUST reject arbitrary shell/script/command fields in executable step payloads before execution.
- **FR-006**: User-facing authoring copy MUST keep `Tool` as the Step Type label and MUST NOT present arbitrary `Script` as a Step Type concept.

### Key Entities

- **Tool Step Draft**: A task step draft with Step Type `Tool`, instructions, selected tool id, optional version, and JSON object inputs.
- **Tool Payload**: The submitted executable object carrying tool id, optional version, and inputs for downstream validation/execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid authored Tool step submits one `type: tool` step containing `tool.id`, optional `tool.version`, and `tool.inputs`.
- **SC-002**: Missing tool id and invalid Tool input JSON are blocked before any `/api/executions` request is sent.
- **SC-003**: Backend task contract validation rejects shell/script/command fields in executable steps.
- **SC-004**: Create-page Tool authoring copy uses Tool terminology and does not expose Script as a Step Type option.
