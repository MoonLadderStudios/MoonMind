# Feature Specification: Agentic Skill Step Authoring

**Feature Branch**: `283-agentic-skill-steps`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: '# MM-564 MoonSpec Orchestration Input\n\n## Source\n\n- Jira issue: MM-564\n- Jira project key: MM\n- Issue type: Story\n- Current status at fetch time: In Progress\n- Summary: Author and validate agentic Skill steps\n- Trusted fetch tool: `jira.get_issue`\n- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.\n- Trusted response artifact: `artifacts/moonspec-inputs/MM-564-trusted-jira-get-issue.json`\n\n## Canonical MoonSpec Feature Request\n\nJira issue: MM-564 from MM project\nSummary: Author and validate agentic Skill steps\nIssue type: Story\nCurrent Jira status: In Progress\nJira project key: MM\n\nUse this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-564 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.\n\nMM-564: Author and validate agentic Skill steps\n\nSource Reference\nSource Document: docs/Steps/StepTypes.md\nSource Title: Step Types\nSource Sections:\n- 5.2 `skill`\n- 6.4 Skill picker\n- 8.3 Skill validation\n- 9. Jira Example\nCoverage IDs:\n- DESIGN-REQ-005\n- DESIGN-REQ-015\nAs a task author, I can configure a Skill step for agentic work with a skill selector, instructions, context, runtime preferences, permissions, and autonomy controls validated before execution.\nAcceptance Criteria\n- A Skill step can be authored with a selected skill and validated inputs.\n- Missing required instructions, context, permissions, or runtime compatibility blocks submission.\n- Skill configuration visibly communicates that the work is agentic.\n- Skill steps may reference allowed tools internally without being represented as Tool steps.\nRequirements\n- Skill steps are used for interpretation, planning, implementation, synthesis, and other open-ended reasoning.\n- Skill validation covers existence or auto resolution, contract inputs, runtime compatibility, required context, permissions, and autonomy controls.\n\n## Orchestration Constraints\n\nSelected mode: runtime.\nDefault to runtime mode and only use docs mode when explicitly requested.\nIf the brief points at an implementation document, treat it as runtime source requirements.\nClassify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.\nInspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.\n'

Preserved source Jira preset brief: `MM-564` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-564` and local artifact `artifacts/moonspec-inputs/MM-564-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-564` under `specs/`, so `Specify` was the first incomplete stage.

## Original Preset Brief

```text
# MM-564 MoonSpec Orchestration Input

## Source

- Jira issue: MM-564
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Author and validate agentic Skill steps
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-564-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-564 from MM project
Summary: Author and validate agentic Skill steps
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-564 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-564: Author and validate agentic Skill steps

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 5.2 `skill`
- 6.4 Skill picker
- 8.3 Skill validation
- 9. Jira Example
Coverage IDs:
- DESIGN-REQ-005
- DESIGN-REQ-015
As a task author, I can configure a Skill step for agentic work with a skill selector, instructions, context, runtime preferences, permissions, and autonomy controls validated before execution.
Acceptance Criteria
- A Skill step can be authored with a selected skill and validated inputs.
- Missing required instructions, context, permissions, or runtime compatibility blocks submission.
- Skill configuration visibly communicates that the work is agentic.
- Skill steps may reference allowed tools internally without being represented as Tool steps.
Requirements
- Skill steps are used for interpretation, planning, implementation, synthesis, and other open-ended reasoning.
- Skill validation covers existence or auto resolution, contract inputs, runtime compatibility, required context, permissions, and autonomy controls.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

```

## User Story - Agentic Skill Authoring

**Summary**: As a task author, I want to configure Skill steps for agentic work so that interpretation, implementation, synthesis, and other open-ended work can be submitted with explicit skill controls instead of being represented as deterministic Tool operations.

**Goal**: Task authors can choose Step Type `Skill`, configure a selected skill or documented auto resolution, instructions, context, runtime preferences, allowed tools/permissions, and autonomy controls, and receive validation before submission when required Skill inputs or compatibility constraints are missing.

**Independent Test**: Render the task authoring experience, switch a draft step to `Skill`, fill a selected skill with instructions and agentic controls, submit the task, and verify the submitted payload contains an executable Skill step while missing instructions, invalid skill input shapes, incompatible runtime choices, or forbidden Tool-only payload fields are rejected before submission.

**Acceptance Scenarios**:

1. **Given** a task author selects Step Type `Skill`, **When** they enter a skill selector, instructions, optional context, runtime preferences, allowed tools or permissions, and autonomy controls, **Then** the submitted task contains a `type: skill` executable step with Skill payload data and no Tool payload.
2. **Given** a Skill step is missing required instructions or selected skill information and no documented auto-resolution path applies, **When** the author submits the task, **Then** submission is blocked with actionable Skill validation feedback.
3. **Given** a Skill step carries runtime, context, permissions, or autonomy fields, **When** those values are incompatible or malformed, **Then** submission is blocked before execution with a visible validation error.
4. **Given** a Skill step references allowed tools or required capabilities internally, **When** the step is submitted, **Then** the step remains classified as `Skill` and is not converted to a Tool step.
5. **Given** the authoring UI presents Skill configuration, **When** the author reviews the controls, **Then** the interface makes the agentic boundary clear and distinguishes Skill work from deterministic Tool work.

### Edge Cases

- A Skill step using documented `auto` semantics is accepted only when the system can preserve the auto selector explicitly and validate the remaining Skill inputs.
- Empty optional context, permissions, or autonomy controls are omitted or normalized without creating hidden invalid configuration.
- Switching away from Skill prevents hidden Skill-only controls from being submitted as active configuration for non-Skill steps.
- Skill steps that include Tool-only payload fields are rejected instead of being silently coerced.

## Assumptions

- The first runtime slice can use the existing skill selector and advanced Skill fields already present in task authoring rather than requiring a new remote skill catalog browser.
- Runtime compatibility validation means known local submission-time compatibility checks; deeper provider availability checks may remain runtime concerns unless already exposed to the authoring surface.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-005 | `docs/Steps/StepTypes.md` section 5.2 `skill` | Skill steps represent agent-facing behavior for interpretation, planning, implementation, synthesis, and other open-ended reasoning; they expose selector, instructions, context, runtime/model preferences, allowed tools/capabilities, and approval/autonomy controls. | In scope | FR-001, FR-002, FR-003, FR-004, FR-006 |
| DESIGN-REQ-015 | `docs/Steps/StepTypes.md` sections 6.4, 8.3, and 9 | Skill selection supports search/descriptions/compatibility hints, makes the agentic boundary clear, validates selector/input/runtime/context/permission/autonomy requirements, and keeps agentic Jira work represented as Skill even when tools may be used internally. | In scope | FR-002, FR-003, FR-004, FR-005, FR-006, FR-007 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to author a Skill step with a Skill selector or documented `auto` selector, instructions, and optional Skill-specific context, runtime preference, permission/tool, capability, and autonomy fields.
- **FR-002**: System MUST submit authored Skill steps as `type: skill` entries with a Skill payload and no Tool payload.
- **FR-003**: System MUST reject Skill steps with missing required instructions, missing or unresolvable skill selector data, non-object Skill inputs, or malformed required capabilities before task submission.
- **FR-004**: System MUST preserve valid Skill selector, instructions, context, runtime preferences, allowed tools or permissions, required capabilities, and autonomy metadata in the submitted task payload.
- **FR-005**: System MUST keep Skill steps classified as agentic Skill work even when the step references allowed tools internally.
- **FR-006**: System MUST reject Tool-only payload fields on Skill steps before execution.
- **FR-007**: User-facing Skill configuration MUST visibly distinguish agentic Skill work from deterministic Tool work through labels, validation messages, or compatibility hints.

### Key Entities

- **Skill Step Draft**: A task step draft with Step Type `Skill`, instructions, selected skill or auto selector, and optional agentic controls.
- **Skill Payload**: The submitted executable object carrying Skill selector and Skill-specific inputs for downstream validation and runtime materialization.
- **Agentic Controls**: Optional context, runtime preferences, allowed tools or permissions, required capabilities, and autonomy settings that shape how the Skill work may execute.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A task author can submit a valid Skill step and the resulting task payload preserves `type: skill` plus Skill-specific payload data.
- **SC-002**: Invalid Skill submissions for missing instructions, missing selector, malformed inputs, or Tool-only payload fields are rejected before execution.
- **SC-003**: Existing Tool and Preset authoring tests continue to pass, demonstrating Skill changes do not regress adjacent Step Types.
- **SC-004**: Source design requirements `DESIGN-REQ-005` and `DESIGN-REQ-015` are traceably covered by spec requirements, implementation tasks, and verification evidence.
