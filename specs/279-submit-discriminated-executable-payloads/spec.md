# Feature Specification: Submit Discriminated Executable Payloads

**Feature Branch**: `279-submit-discriminated-executable-payloads`
**Created**: 2026-04-29
**Status**: Draft
**Input**:

```text
# MM-559 MoonSpec Orchestration Input

## Source

- Jira issue: MM-559
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Submit discriminated executable payloads
- Labels: moonmind-workflow-mm-b197665d-a0b9-489d-a38a-9723a9d469c1
- Trusted fetch tool: jira.get_issue
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose recommended preset instructions or a normalized preset brief.

## Canonical MoonSpec Feature Request

Jira issue: MM-559 from MM project
Summary: Submit discriminated executable payloads
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-559 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-559: Submit discriminated executable payloads

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 7.1 Authoring payload
- 7.2 Runtime plan mapping
- 10.3 Keep `Activity` Temporal-specific
- 11. API Shape
- 15. Non-Goals
- 16. Open Design Decisions

Coverage IDs:
- DESIGN-REQ-008
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-016
- DESIGN-REQ-019

As a workflow maintainer, I want task submissions to use explicit discriminated Step payloads that compile into runtime plans without exposing Temporal implementation details to authors.

Acceptance Criteria
- The API shape exposes StepType values tool, skill, and preset with distinct sub-payloads.
- Executable submission normally accepts only ToolStep or SkillStep.
- Preset-derived source metadata can be present but is not required for runtime correctness.
- Runtime materialization maps Tool and Skill into plan nodes and does not materialize Preset as a runtime node by default.
- Temporal Activity remains an implementation detail and is not used as a Step Type label.

Requirements
- Step payloads are explicit and discriminated.
- Preset expansion produces executable payloads before submission.
- Provenance metadata is durable enough for audit and reconstruction but not hidden work.
- Internal alternatives such as step.action.kind cannot change the user-facing Step Type terminology.
```

Classification: single-story runtime feature request. Existing artifact inspection found no prior `MM-559` spec under `specs/`, so `Specify` is the first incomplete MoonSpec stage.

## User Story - Submit Executable Step Payloads

**Summary**: As a workflow maintainer, I want task submissions to carry explicit Tool and Skill step discriminators so runtime plans can be materialized without exposing Temporal implementation details or unresolved preset placeholders.

**Goal**: Draft and preset-expanded task submissions preserve clear user-facing Step Type intent through submission validation and runtime materialization.

**Independent Test**: Can be fully tested by submitting or validating task payloads with Tool, Skill, and Preset step shapes and confirming that only executable Tool and Skill steps enter runtime plans while preset provenance remains audit metadata.

**Acceptance Scenarios**:

1. **Given** a task submission contains a Tool step with `type: "tool"` and a tool sub-payload, **When** the task payload is validated and materialized, **Then** the step remains classified as a Tool step and maps to a typed tool plan node.
2. **Given** a task submission contains a Skill step with `type: "skill"` and a skill sub-payload, **When** the task payload is validated and materialized, **Then** the step remains classified as a Skill step and maps to an agent-facing skill plan node.
3. **Given** a task submission contains an unresolved Preset step with `type: "preset"`, **When** executable submission validation runs, **Then** the submission is rejected before runtime materialization.
4. **Given** preset-expanded executable steps include source provenance metadata, **When** the task payload is validated and materialized, **Then** provenance is preserved for audit and reconstruction but is not required to choose the runtime plan node.
5. **Given** a submitted step uses Temporal Activity terminology as its Step Type, **When** executable submission validation runs, **Then** the submission is rejected and no Activity Step Type enters the runtime plan.

### Edge Cases

- A step includes both Tool and Skill sub-payloads.
- A step omits `type` while carrying legacy skill or tool-shaped fields.
- A command-like Tool step has unbounded inputs or lacks required policy metadata.
- A preset-derived step has missing or partial provenance metadata.
- A runtime plan is generated from a single explicit step or multiple explicit steps.

## Assumptions

- Existing legacy readers may continue to accept older task shapes during migration, but new executable authoring and submitted payloads should prefer explicit `type`, `tool`, and `skill` fields.
- Preset preview and apply flows are responsible for expanding Preset steps before submission; this story enforces the executable submission boundary.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Functional Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-008 | `docs/Steps/StepTypes.md` section 7.1 | Executable task submission should contain only executable Tool and Skill steps by default. | In scope | FR-001, FR-004 |
| DESIGN-REQ-011 | `docs/Steps/StepTypes.md` section 7.2 | Runtime materialization maps Tool and Skill steps into plan nodes and does not materialize Preset as a runtime node by default. | In scope | FR-002, FR-004 |
| DESIGN-REQ-012 | `docs/Steps/StepTypes.md` section 10.3 | Temporal Activity remains an implementation detail and must not be used as the Step Type label. | In scope | FR-005 |
| DESIGN-REQ-016 | `docs/Steps/StepTypes.md` section 11 | Step API shape is explicit and discriminated with `tool`, `skill`, and `preset` values and distinct sub-payloads. | In scope | FR-001, FR-003 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` sections 15 and 16 | Presets are not hidden runtime work, and internal alternatives must not change user-facing Step Type terminology. | In scope | FR-004, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept executable submitted steps with explicit `type: "tool"` plus a Tool sub-payload or `type: "skill"` plus a Skill sub-payload.
- **FR-002**: System MUST materialize Tool steps and Skill steps into runtime plan nodes using their explicit Step Type intent rather than Temporal Activity terminology.
- **FR-003**: System MUST expose or preserve Step Type values `tool`, `skill`, and `preset` in authoring and template-derived payload contracts with distinct sub-payloads.
- **FR-004**: System MUST reject unresolved Preset steps at the executable submission boundary unless a future linked-preset execution mode is explicitly implemented.
- **FR-005**: System MUST reject Activity or other Temporal implementation labels as submitted Step Type values.
- **FR-006**: System MUST preserve preset-derived source or provenance metadata when present without requiring it for runtime correctness.
- **FR-007**: System MUST reject steps with conflicting discriminators, such as Tool and Skill sub-payloads on the same executable step.

### Key Entities

- **Executable Step**: A submitted task step whose Step Type is `tool` or `skill` and whose type-specific sub-payload is valid.
- **Preset Step**: An authoring-time placeholder that must be expanded before executable submission.
- **Step Provenance**: Optional metadata describing preset origin, include path, original step identity, or related reconstruction details.
- **Runtime Plan Node**: The internal execution unit produced from an executable step.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation tests cover Tool, Skill, Preset rejection, Activity rejection, conflicting payloads, and provenance-preservation cases.
- **SC-002**: Runtime materialization tests prove Tool and Skill submitted steps produce executable plan nodes and Preset steps do not.
- **SC-003**: UI or template expansion tests prove preset-applied submissions send flattened executable steps with explicit Step Type values.
- **SC-004**: Traceability evidence preserves `MM-559` and all in-scope `DESIGN-REQ-*` mappings in MoonSpec artifacts and final verification output.
