# Feature Specification: Define Step Type Authoring Model

**Feature Branch**: `288-define-step-type-authoring-model`
**Created**: 2026-05-01
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-575 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-575` from the trusted `jira.get_issue` response, reproduced in `## Original Preset Brief` below for durable downstream verification. Raw local orchestration captures were treated as transient run artifacts; the reproduced brief is the committed traceability record.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-575`, with the normalized brief reproduced below for permanence.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-575`, so `Specify` was the first incomplete MM-575 stage.

## Original Preset Brief

```text
Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-575 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-575: Define the Step Type authoring model

Current Jira status at orchestration input fetch time: In Progress
Issue type: Story
Labels: moonmind-workflow-mm-911309af-6b4f-48e7-8835-e533aa9af8cf

Canonical source: normalized Jira preset brief synthesized from trusted jira.get_issue response fields because the MCP issue response did not expose recommendedImports.presetInstructions, normalizedPresetBrief, presetBrief, or presetInstructions, and the Implementation plan and Source fields were null.
Trusted response source: runtime `jira.get_issue` response for `MM-575`; relevant normalized fields are reproduced in this committed brief.

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 1. Purpose
- 2. Desired-State Summary
- 3. Terminology
- 4. Core Invariants
- 6.1 Step editor
- 6.2 Step type picker
- 10. Naming Policy
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-014

As a task author, I can choose exactly one Step Type for each step so the editor shows the right fields and uses consistent product terminology.

Acceptance Criteria
- Every authored step has exactly one Step Type with canonical values tool, skill, or preset.
- Changing Step Type updates the visible configuration controls and handles incompatible data explicitly.
- The product-facing label is Step Type, not capability, activity, invocation, command, or script.
- Task, Step, Tool, Skill, Preset, Expansion, Plan, and Activity terminology follows the design definitions.

Requirements
- Represent the selected Step Type explicitly in draft state.
- Keep type-specific payloads separate enough that invalid mixed-type drafts can be detected.
- Reserve capability terminology for security or worker-placement contexts only.
```

## User Story - Step Type Authoring Model

**Summary**: As a task author, I can choose exactly one Step Type for each step so the editor shows the right fields and uses consistent product terminology.

**Goal**: Each authored step has one explicit Step Type value, type-specific draft data remains separated enough to detect invalid mixed-type drafts, and product copy consistently uses the Step Type vocabulary.

**Independent Test**: Render the Create page step editor, verify one Step Type selector with Tool, Skill, and Preset options, switch among types, and confirm that visible controls, preserved instructions, discarded incompatible data, submitted payload shape, and terminology all match the Step Type model.

**Acceptance Scenarios**:

1. **Given** a task author opens an authored step, **When** they inspect its discriminator control, **Then** the step exposes exactly one control named Step Type with Tool, Skill, and Preset choices.
2. **Given** the author changes the Step Type, **When** the selected value changes to Tool, Skill, or Preset, **Then** only the matching type-specific configuration controls are visible below the selector.
3. **Given** a step has meaningful type-specific values, **When** the author changes to an incompatible Step Type, **Then** shared instructions are preserved and incompatible type-specific data is explicitly cleared or otherwise handled visibly.
4. **Given** a step is submitted or compiled for execution, **When** its Step Type is Tool or Skill, **Then** the payload contains only the compatible executable payload for that type and rejects mixed Tool/Skill payloads.
5. **Given** the Step Type model appears in the authoring surface, **When** labels, helper copy, and validation messages are inspected, **Then** they use Step Type, Tool, Skill, Preset, Expansion, Plan, and Activity terminology according to the design definitions and do not use capability as the umbrella label.

### Edge Cases

- A newly created step defaults to one valid Step Type and does not require a second discriminator before it can be edited.
- Hidden Skill, Tool, or Preset state is not silently submitted after the author changes to another Step Type.
- Preset remains an authoring-time placeholder; applied presets must produce executable Tool or Skill steps before runtime execution.
- Runtime validation rejects non-executable Step Types such as preset or activity in execution payloads.

## Assumptions

- Runtime intent applies: this story verifies product behavior in the Create page authoring surface and execution payload validation, not documentation-only wording.
- Related Step Type work may already satisfy parts of MM-575; this spec preserves MM-575 traceability and verifies the authoring-model requirements directly.
- Preset authoring can remain a preview/apply workflow as long as runtime submissions use expanded executable steps.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | docs/Steps/StepTypes.md sections 1, 2, 4 | Every authored step has exactly one Step Type that determines what the step represents, which fields are shown, and how it is validated. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-002 | docs/Steps/StepTypes.md sections 2, 3, 6.1, 6.2 | The Step Type control offers Tool, Skill, and Preset and renders type-specific configuration below the selector. | In scope | FR-001, FR-002, FR-006 |
| DESIGN-REQ-003 | docs/Steps/StepTypes.md section 3 | Task, Step, Tool, Skill, Preset, Expansion, Plan, and Activity terminology follows the design definitions. | In scope | FR-005 |
| DESIGN-REQ-005 | docs/Steps/StepTypes.md sections 4, 5.3 | Preset steps are authoring-time placeholders and must expand into executable Tool and/or Skill steps before runtime execution. | In scope | FR-004 |
| DESIGN-REQ-006 | docs/Steps/StepTypes.md sections 4, 6.1 | Changing Step Type must preserve compatible fields and clearly handle incompatible fields. | In scope | FR-003 |
| DESIGN-REQ-014 | docs/Steps/StepTypes.md section 10 | Product-facing copy uses Step Type as the umbrella term and avoids capability/activity/invocation/command/script as ordinary authoring labels. | In scope | FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each authored step MUST represent its selected Step Type explicitly as exactly one of Tool, Skill, or Preset in draft state.
- **FR-002**: The step editor MUST show only the configuration controls that correspond to the selected Step Type.
- **FR-003**: Changing Step Type MUST preserve compatible shared instructions and MUST explicitly clear, preserve, or confirm removal of incompatible type-specific data.
- **FR-004**: Runtime executable payloads MUST reject non-executable Step Types and mixed Tool/Skill payloads.
- **FR-005**: User-facing authoring copy MUST use Step Type, Tool, Skill, Preset, Expansion, Plan, and Activity according to the source design terminology.
- **FR-006**: Product-facing labels MUST NOT use capability, activity, invocation, command, or script as the umbrella label for the Step Type selector.

### Key Entities

- **Step Draft**: A user-authored step with shared instructions, one selected Step Type, and separated type-specific draft state.
- **Step Type**: The user-facing discriminator with canonical values Tool, Skill, and Preset.
- **Executable Step Payload**: The runtime-ready representation after authoring that can execute as Tool or Skill and cannot contain incompatible mixed payloads.
- **Preset Expansion**: The deterministic authoring-time process that turns a Preset step into concrete executable Tool and/or Skill steps.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests verify one Step Type selector with exactly Tool, Skill, and Preset choices for an authored step.
- **SC-002**: Frontend tests verify switching Step Type changes visible controls while preserving shared instructions.
- **SC-003**: Frontend tests verify incompatible type-specific data is visibly cleared or handled on Step Type changes.
- **SC-004**: Runtime contract tests verify non-executable Step Types and mixed executable payloads are rejected.
- **SC-005**: Verification evidence confirms primary authoring labels use Step Type vocabulary and preserve `MM-575` traceability.
