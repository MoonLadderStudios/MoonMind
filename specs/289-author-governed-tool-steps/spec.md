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
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-576-trusted-jira-get-issue.json`

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
```

## User Story - Governed Tool Step Authoring

**Summary**: As a task author, I want to configure a Tool step as a typed governed operation so deterministic integrations run with schema, authorization, capability, retry, and error contracts.

**Goal**: Task authors can select a governed Tool operation, discover it by integration or domain, configure schema-shaped inputs including dynamic provider options, and receive validation before submission so deterministic work is submitted as a governed Tool step rather than arbitrary shell text.

**Independent Test**: Open the task authoring surface, add a Tool step, search or browse grouped Tool operations, configure a Jira-style governed operation with schema-valid inputs and dynamic target-status options, submit the task, and verify the submitted step is a typed Tool operation while invalid tools, invalid inputs, unavailable authorization/capability states, and arbitrary shell input are rejected before execution.

**Acceptance Scenarios**:

1. **Given** a task author is configuring a step, **When** they select Step Type `Tool`, **Then** they can find typed Tool operations through integration or domain grouping and search.
2. **Given** a task author selects a Tool operation, **When** the operation has an input schema, **Then** the authoring surface presents inputs governed by that schema and preserves the selected tool id plus a resolvable or pinned version.
3. **Given** a Tool operation exposes dynamic options such as Jira target statuses, **When** prerequisite inputs are available, **Then** the author can choose from provider-derived options permitted for that context.
4. **Given** a Tool step is missing a tool id, has an unresolved version, invalid schema inputs, missing authorization, unavailable worker capabilities, forbidden fields, or unknown retry/side-effect policy, **When** the author submits, **Then** submission is blocked with actionable validation feedback before execution.
5. **Given** a task author attempts to submit arbitrary shell input as a Tool step, **When** the selected operation is not an approved typed command tool with bounded inputs and policy, **Then** the shell input is rejected before execution.
6. **Given** the authoring and validation surfaces describe deterministic work, **When** the user reviews the step, **Then** the user-facing Step Type remains `Tool` and the system does not present `Script`, `Activity`, or worker placement terminology as the step concept.

### Edge Cases

- Dynamic option providers may be unavailable or return no permitted options; the authoring surface must show that the Tool cannot be completed for the current context instead of allowing an unvalidated free-text substitute.
- A tool version may be omitted only when the system can resolve an active version deterministically before submission.
- Tool inputs must validate as schema-shaped data for the selected Tool, and unknown or forbidden fields must be surfaced as validation errors.
- Approved typed command tools may accept bounded command-like inputs only when their Tool contract explicitly declares the input schema and policy.

## Assumptions

- Tool catalog metadata is available to the authoring surface or boot payload closely enough to support grouping, search, schema display, and validation feedback for the governed Tool operations in scope.
- Dynamic option providers are treated as part of the selected Tool contract and may fail closed when prerequisite inputs, authorization, or provider permissions are unavailable.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-007 | `docs/Steps/StepTypes.md` section 5.1 `tool` | Tool steps represent explicit, bounded, typed operations whose definitions declare name/version, schemas, authorization, worker capabilities, retry policy, execution binding, validation, and error model. | In scope | FR-001, FR-002, FR-003, FR-006 |
| DESIGN-REQ-008 | `docs/Steps/StepTypes.md` section 6.3 `Tool picker` | Tool selection supports search, integration/domain grouping, schema-driven forms, and dynamic option providers such as Jira target statuses. | In scope | FR-001, FR-003, FR-004 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` section 8.2 `Tool validation` | Tool validation requires selected tool existence, resolvable or pinned version, schema-valid inputs, authorization, worker capabilities, absence of forbidden fields, and known retry/side-effect policy. | In scope | FR-002, FR-005, FR-006 |
| DESIGN-REQ-020 | `docs/Steps/StepTypes.md` sections 10.1 and 15 | Tool remains the user-facing concept for typed executable operations, arbitrary shell scripts are not a first-class Step Type, and users are not required to understand worker placement or Temporal Activity semantics to author steps. | In scope | FR-005, FR-007 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to select a Tool step from grouped integration/domain categories and search across available typed Tool operations.
- **FR-002**: Each authored Tool step MUST preserve a typed tool id and either a pinned version or a deterministically resolved version before submission.
- **FR-003**: The authoring surface MUST present Tool inputs from the selected Tool contract schema rather than arbitrary unbounded script fields.
- **FR-004**: Dynamic option providers MUST populate contract-defined option fields, such as Jira target statuses, when prerequisite context and authorization permit.
- **FR-005**: System MUST reject arbitrary shell input unless the selected Tool is an explicitly approved typed command tool with bounded inputs and policy.
- **FR-006**: System MUST block Tool submission before execution when selected tool existence, version resolution, schema validation, authorization, worker capability, forbidden-field, retry-policy, or side-effect-policy checks fail.
- **FR-007**: User-facing authoring and validation copy MUST keep `Tool` as the Step Type label and MUST NOT require users to understand Script, Temporal Activity, or worker placement terminology to configure ordinary Tool steps.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-576` and the canonical Jira preset brief for traceability.

### Key Entities

- **Tool Definition**: A typed operation contract with identity, versioning, input/output schema, authorization requirements, worker capability requirements, retry policy, execution binding, validation rules, and error model.
- **Tool Step Draft**: A task authoring step with Step Type `Tool`, selected Tool definition, resolved or pinned version, schema-shaped inputs, dynamic option selections, and validation state.
- **Dynamic Option Provider**: A contract-defined option source that derives valid field choices from current step context, permissions, and integration state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A task author can find a governed Tool operation through grouping or search and submit one valid Tool step with typed id, version, and schema-valid inputs.
- **SC-002**: Dynamic options for a context-dependent Tool field are presented when available and fail closed with visible validation when unavailable.
- **SC-003**: Invalid Tool submissions covering missing tool, unresolved version, invalid schema inputs, missing authorization, unavailable capabilities, forbidden fields, and unknown policy are blocked before execution.
- **SC-004**: Arbitrary shell input cannot be submitted as a Tool step unless it belongs to an approved typed command Tool contract.
- **SC-005**: User-visible authoring and validation surfaces consistently use `Tool` terminology and do not present Script or Temporal Activity as the Step Type concept.
- **SC-006**: Final verification preserves `MM-576`, the canonical Jira preset brief, and DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, and DESIGN-REQ-020 coverage in active MoonSpec artifacts and delivery metadata.
