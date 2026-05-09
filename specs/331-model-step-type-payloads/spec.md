# Feature Specification: Model Explicit Step Type Payloads and Validation

**Feature Branch**: `331-model-step-type-payloads`
**Created**: 2026-05-09
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-569 as the canonical Moon Spec orchestration input.

Additional constraints:
Preserve source issue manual-mm-569-mm-574 traceability.

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-569 MoonSpec Orchestration Input

## Source

- Jira issue: MM-569
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Model explicit Step Type payloads and validation
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-569 from MM project
Summary: Model explicit Step Type payloads and validation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-569 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-569: Model explicit Step Type payloads and validation

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 7.1 Authoring payload
- 8. Validation Rules
- 11. API Shape
- 14. Migration Guidance
Coverage IDs:
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-018
- DESIGN-REQ-021

As a platform maintainer, I can validate draft and submitted steps as an explicit discriminated model so invalid mixed-type payloads fail before execution.

Acceptance Criteria
- A step payload has stable local identity, display label or title, type, and exactly the matching type-specific sub-payload.
- Tool validation checks tool existence/version, schema inputs, authorization, worker capability, forbidden fields, retry policy, and side-effect policy where those services are available.
- Skill validation checks skill resolution, contract inputs, runtime compatibility, required context, allowed tools or permissions, and autonomy constraints.
- Preset validation checks preset/version, input schema, deterministic expansion, generated-step validation, policy limits, and visible warnings.
- Executable submission rejects unresolved Preset steps unless a separately supported linked-preset mode is explicitly enabled.

Requirements
- Use an explicit Step discriminated union or equivalent normalized internal shape.
- Keep legacy readers during migration while preventing new authoring surfaces from emitting ambiguous shapes.
- Surface validation errors before submission.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path: docs/Steps/StepTypes.md.

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing MoonSpec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-569` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-569` and local handoff `/work/agent_jobs/mm:39219996-0c55-46b6-b755-ecb17f3bca83/artifacts/moonspec-inputs/MM-569-canonical-moonspec-input.md`.
Traceability source: `manual-mm-569-mm-574`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-569`, so Specify was the first incomplete stage.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Validate Explicit Step Payloads

**Summary**: As a platform maintainer, I can validate draft and submitted steps as explicit Step Type payloads so invalid mixed-type payloads fail before execution.

**Goal**: Draft authoring and executable submission flows accept only coherent Step Type payloads, report field-specific validation failures before execution, and preserve migration safety for existing readers without letting new authoring surfaces emit ambiguous shapes.

**Independent Test**: Can be fully tested by creating draft and submission payloads for Tool, Skill, and Preset steps, then validating that matching type-specific payloads pass, mixed or incomplete payloads fail with field-addressable errors, unresolved Preset runtime submission is blocked unless an explicit linked-preset mode exists, and traceability to `MM-569` plus `manual-mm-569-mm-574` is preserved.

**Acceptance Scenarios**:

1. **Given** a step payload with stable local identity, display title or label, Step Type, and exactly the matching type-specific payload, **When** the authoring or submission validator evaluates it, **Then** the step is accepted for the appropriate draft or executable context.
2. **Given** a step payload that mixes Tool, Skill, and Preset-specific fields or omits the sub-payload required by its Step Type, **When** validation runs, **Then** validation fails before execution with a field-addressable error for the offending field.
3. **Given** a Tool step, **When** validation runs, **Then** the selected tool, version or resolution, input schema, authorization, worker capability, forbidden fields, retry policy, and side-effect policy are checked wherever the corresponding services are available.
4. **Given** a Skill step, **When** validation runs, **Then** skill resolution, contract inputs, runtime compatibility, required context, allowed tools or permissions, and autonomy constraints are checked.
5. **Given** a Preset step, **When** it is applied or submitted for expansion, **Then** preset identity, active version, input schema, deterministic expansion, generated-step validation, policy limits, and visible warnings are checked.
6. **Given** an executable submission still containing an unresolved Preset step, **When** no explicit linked-preset execution mode is enabled, **Then** submission is rejected before workflow execution.

### Edge Cases

- A draft created from a legacy reader contains older field names that can be read for migration but should not be emitted by new authoring surfaces.
- A selected Tool, Skill, or Preset no longer exists or cannot be resolved.
- Validation depends on an unavailable authorization, capability, or policy service.
- Preset expansion produces generated steps that are themselves invalid.
- A step has a valid Step Type but no stable identity or user-visible label/title.

## Assumptions

- Existing legacy readers may remain during migration, but any new authoring or submission output for this story should use the normalized Step Type shape.
- Validation should be fail-fast and field-addressable whenever a required registry, permission, capability, or policy check cannot be completed.
- Linked-preset runtime execution is out of scope unless an explicit supported mode already exists.

## Source Design Requirements

- **DESIGN-REQ-012**: Source `docs/Steps/StepTypes.md` sections "Runtime and Payload Contract", "API Shape", and "Migration Guidance". Step payloads MUST use an explicit Step Type discriminator or equivalent normalized shape that distinguishes Tool, Skill, and Preset configuration. Scope: in scope. Maps to FR-001, FR-002, FR-010.
- **DESIGN-REQ-013**: Source `docs/Steps/StepTypes.md` sections "Validation Rules" and "API Shape". Every step MUST have stable local identity, title or generated display label, Step Type, matching type-specific payload, schema-valid inputs, and field-addressable validation errors before submission. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-014**: Source `docs/Steps/StepTypes.md` section "Tool validation". Tool steps MUST validate tool availability, version resolution, input schema, authorization, worker capability, forbidden fields, retry policy, side-effect policy, and rejection of arbitrary ungoverned shell snippets. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-015**: Source `docs/Steps/StepTypes.md` section "Skill validation". Skill steps MUST validate skill resolution, contract inputs, runtime compatibility, required context, allowed tools or permissions, and approval or autonomy constraints. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-018**: Source `docs/Steps/StepTypes.md` sections "Preset validation", "Submit-time auto-expansion", and "Runtime payload". Preset steps MUST validate preset identity, active version, input schema, deterministic expansion, generated-step validity, policy limits, and warnings; executable runtime submission MUST reject unresolved Preset steps unless a separately supported linked-preset mode is explicit. Scope: in scope. Maps to FR-006, FR-007, FR-008.
- **DESIGN-REQ-021**: Source `docs/Steps/StepTypes.md` section "Migration Guidance". Legacy readers MAY remain during migration, but new authoring surfaces MUST stop emitting ambiguous legacy shapes and migration must converge draft, submission, and runtime contracts. Scope: in scope. Maps to FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST represent every authored step with stable local identity, user-visible title or generated label, Step Type, and exactly one matching type-specific payload.
- **FR-002**: System MUST reject mixed-type, missing-type, or mismatched type-specific step payloads before execution.
- **FR-003**: System MUST surface validation failures as field-addressable errors that identify the invalid step field and actionable reason.
- **FR-004**: System MUST validate Tool steps for selected tool availability, version resolution, schema-valid inputs, authorization, worker capability, forbidden fields, retry policy, and side-effect policy wherever those checks are available.
- **FR-005**: System MUST validate Skill steps for skill resolution, contract inputs, runtime compatibility, required context, allowed tools or permissions, and approval or autonomy constraints.
- **FR-006**: System MUST validate Preset steps for preset availability, active authoring version, schema-valid inputs, deterministic expansion, generated-step validity, policy limits, and visible warnings.
- **FR-007**: System MUST prevent executable workflow creation from unresolved Preset steps unless an explicitly supported linked-preset mode is enabled.
- **FR-008**: System MUST preserve user-entered Preset inputs and visible validation errors when preset apply or submit-time expansion fails.
- **FR-009**: System MUST allow legacy reader paths needed for migration while preventing new authoring surfaces from emitting ambiguous legacy step shapes.
- **FR-010**: System MUST keep draft, submission, and executable payload validation aligned so accepted executable steps do not require live preset catalog lookup for correctness.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-569`, source traceability key `manual-mm-569-mm-574`, and the canonical Jira preset brief.

### Key Entities

- **Step Payload**: A single authored or executable step with stable identity, display label or title, Step Type, type-specific configuration, and optional provenance.
- **Type-Specific Payload**: The Tool, Skill, or Preset configuration that is valid only for its matching Step Type.
- **Validation Error**: A field-addressable result containing the invalid path, reason, and recoverable guidance.
- **Preset Expansion Result**: The concrete generated executable steps plus warnings or errors produced from a Preset step.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid Tool, Skill, and Preset examples with matching Step Type payloads pass validation in draft context.
- **SC-002**: 100% of mixed-type or missing type-specific payload examples fail validation before executable workflow creation.
- **SC-003**: Every validation failure produced by this story includes a field path and human-readable reason.
- **SC-004**: Executable submission rejects unresolved Preset steps in all tested cases unless an explicit linked-preset mode is present.
- **SC-005**: Regression coverage includes at least one Tool, one Skill, one Preset, one mixed-type failure, and one legacy-reader migration case.
- **SC-006**: Traceability review confirms `MM-569`, `manual-mm-569-mm-574`, and DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, and DESIGN-REQ-021 remain preserved across MoonSpec artifacts and final evidence.
