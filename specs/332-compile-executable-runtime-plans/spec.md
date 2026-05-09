# Feature Specification: Compile Executable Steps into Runtime Plans

**Feature Branch**: `332-compile-executable-runtime-plans`  
**Created**: 2026-05-09  
**Status**: Draft  
**Input**: User description: """
Use the Jira preset brief for MM-573 as the canonical Moon Spec orchestration input.

Additional constraints:
Preserve source issue manual-mm-569-mm-574 traceability.

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-573 MoonSpec Orchestration Input

## Source

- Jira issue: MM-573
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Compile executable steps into runtime plans
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.
- Trusted response artifact: `/work/agent_jobs/mm:e2310203-c332-4714-9c20-66bf68a3f43f/artifacts/moonspec-inputs/MM-573-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-573 from MM project
Summary: Compile executable steps into runtime plans
Issue type: Story
Current Jira status: In Progress
Jira project key: MM
Source Jira issue: manual-mm-569-mm-574

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-573 and source issue manual-mm-569-mm-574 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-573: Compile executable steps into runtime plans

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 7.1 Authoring payload
- 7.2 Runtime plan mapping
- 11. API Shape
- 13. Proposal and Promotion Semantics
- 14. Migration Guidance
- 15. Non-Goals

Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-012
- DESIGN-REQ-018
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022

As an operator, submitted tasks execute from flattened Tool and Skill steps so runtime correctness does not depend on unresolved presets or live catalog re-expansion.

Acceptance Criteria
- Executable submissions normally accept only Tool and Skill steps.
- Tool steps map to typed tool plan nodes.
- Skill steps map to plan nodes, child workflows, activities, or managed sessions without changing the Step Type UI contract.
- Preset steps produce no runtime node by default.
- Preset provenance is retained for audit and reconstruction but is not required to execute.
- Promotion validates the reviewed flat payload and never silently re-expands a live preset.

Requirements
- Durable execution payloads should contain expanded executable steps by default.
- Preset-derived execution must not depend on live catalog lookup at runtime.
- Proposal promotion must preserve executable intent.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for Step Type runtime execution: submitted and promoted tasks must execute from flattened executable Tool and Skill steps, map those steps into runtime plan nodes or runtime execution boundaries, retain Preset provenance for audit, and avoid unresolved Preset runtime nodes or live catalog re-expansion during execution.
"""

Preserved source traceability:

- Target Jira issue: `MM-573`
- Source Jira issue: `manual-mm-569-mm-574`
- Source design: `docs/Steps/StepTypes.md`
- Original orchestration input artifact: `/work/agent_jobs/mm:e2310203-c332-4714-9c20-66bf68a3f43f/artifacts/moonspec-inputs/MM-573-moonspec-orchestration-input.md`
- Classification: single-story runtime feature request.
- Resume decision: no existing Moon Spec feature directory preserved `MM-573`, so Specify is the first incomplete stage.

## User Story - Runtime Executes Flattened Steps

**Summary**: As an operator, I want submitted and promoted tasks to execute from flattened Tool and Skill steps so runtime correctness does not depend on unresolved presets or live catalog re-expansion.

**Goal**: Executable submissions and promoted proposals carry a durable, reviewed, flattened step payload that the runtime can compile into executable plan nodes while preserving preset provenance for audit and reconstruction.

**Independent Test**: Submit or promote a task that originated from a preset, inspect the durable execution payload and runtime plan, and verify that only Tool and Skill steps are executable, each maps to the expected runtime materialization, preset provenance remains available, and no live preset catalog lookup is needed to execute the task.

**Acceptance Scenarios**:

1. **Given** a task submission contains concrete Tool and Skill steps, **When** the runtime accepts the executable payload, **Then** it compiles only those executable steps into runtime plan nodes.
2. **Given** an executable Tool step is present, **When** the runtime plan is built, **Then** the step maps to a typed tool invocation with the reviewed input payload and required governance metadata.
3. **Given** an executable Skill step is present, **When** the runtime plan is built, **Then** the step maps to a plan node, child workflow, activity, or managed-session request without changing the user-facing Step Type contract.
4. **Given** preset-derived work reaches runtime, **When** the durable execution payload is inspected, **Then** preset provenance is retained for audit and reconstruction while unresolved Preset steps produce no runtime node by default.
5. **Given** a reviewed proposal is promoted to execution, **When** promotion validates the stored payload, **Then** it preserves executable intent and does not silently re-expand a live preset catalog entry.
6. **Given** a payload still contains unresolved Preset steps where executable steps are required, **When** runtime compilation is attempted, **Then** execution is rejected or expansion is completed before workflow creation rather than treating the Preset step as hidden runtime work.

### Edge Cases

- A preset-derived task can be reconstructed for audit from provenance without needing that provenance to execute.
- A live preset catalog entry may change after a task or proposal is reviewed; existing executable payloads still run from the reviewed flattened content.
- Skill steps may use different runtime materializations across adapters, but the Step Type UI contract and durable executable intent remain stable.
- A draft-authoring payload may contain Preset steps, but executable workflow creation must not depend on unresolved Preset runtime nodes.
- Legacy payload readers may exist during migration, but new durable executable payloads should prefer flattened Tool and Skill steps.

## Assumptions

- Runtime intent applies because the Jira brief asks for executable submission and proposal promotion behavior, not documentation-only changes.
- `docs/Steps/StepTypes.md` is the source design for Step Type runtime semantics.
- The related source set `manual-mm-569-mm-574` is a traceability source and does not change the selected target issue, which is `MM-573`.
- Preset preview and application authoring behavior is covered by the related `MM-572` story; this story focuses on runtime compilation and promotion semantics.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/Steps/StepTypes.md` sections 9.3 and 9.4 | Runtime execution payloads should contain only executable Tool and Skill steps by default. | In scope | FR-001, FR-004 |
| DESIGN-REQ-007 | `docs/Steps/StepTypes.md` section 9.4 | Tool steps must map to typed runtime tool invocation plan nodes. | In scope | FR-002 |
| DESIGN-REQ-012 | `docs/Steps/StepTypes.md` section 9.4 | Skill steps may map to plan nodes, child workflows, activities, or managed-session requests without changing the Step Type UI contract. | In scope | FR-003 |
| DESIGN-REQ-018 | `docs/Steps/StepTypes.md` sections 9.3, 9.4, and 17 | Preset steps produce no runtime node by default and presets must not become hidden runtime work. | In scope | FR-004, FR-006 |
| DESIGN-REQ-020 | `docs/Steps/StepTypes.md` sections 9.3 and 16 phase 4 | Preset-derived runtime steps retain provenance but do not require preset catalog lookup for runtime correctness. | In scope | FR-004, FR-005 |
| DESIGN-REQ-021 | `docs/Steps/StepTypes.md` sections 15 and 16 phase 5 | Proposal promotion preserves executable intent and validates the reviewed payload. | In scope | FR-005 |
| DESIGN-REQ-022 | `docs/Steps/StepTypes.md` section 15 | Promotion must not silently re-expand a live preset catalog entry; refreshing from a preset is explicit. | In scope | FR-005 |

## Requirements

### Functional Requirements

- **FR-001**: Durable executable task payloads MUST contain flattened executable steps by default, limited to Tool and Skill step types unless a separate authoring expansion path completes before workflow creation.
- **FR-002**: The runtime plan compiler MUST map each Tool step to a typed tool invocation plan node using the reviewed tool identity, version when available, inputs, and required governance metadata.
- **FR-003**: The runtime plan compiler MUST map each Skill step to the appropriate runtime materialization, such as a plan node, child workflow, activity, or managed-session request, while preserving the Step Type contract visible to authors.
- **FR-004**: Preset-derived executable steps MUST retain provenance metadata sufficient for audit and reconstruction, but runtime execution MUST NOT require live preset catalog lookup after expansion.
- **FR-005**: Proposal promotion MUST validate and execute the reviewed flattened payload, preserve executable intent, and avoid silent live preset re-expansion.
- **FR-006**: The system MUST reject or pre-expand unresolved Preset steps before executable workflow creation; unresolved Preset steps MUST NOT compile into hidden runtime nodes by default.
- **FR-007**: Downstream artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve target Jira issue `MM-573`, source issue `manual-mm-569-mm-574`, and this original Jira preset brief.

### Key Entities

- **Executable Step**: A durable Tool or Skill step that can be compiled into runtime work without additional authoring expansion.
- **Runtime Plan Node**: Runtime representation of executable work produced from a Tool or Skill step.
- **Preset Provenance**: Metadata attached to preset-derived executable steps that identifies preset origin, version or inputs when available, and reconstruction context.
- **Promoted Proposal Payload**: Reviewed flattened task payload promoted from a proposal into execution.

## Success Criteria

- **SC-001**: Runtime compilation tests show Tool and Skill steps compile into executable plan nodes while unresolved Preset steps do not produce runtime nodes by default.
- **SC-002**: Tests or verification evidence demonstrate that preset-derived executable steps retain provenance without requiring live preset catalog lookup during runtime execution.
- **SC-003**: Promotion tests show reviewed flattened proposal payloads execute without silent live preset re-expansion.
- **SC-004**: Invalid executable submissions containing unresolved Preset steps are rejected or expanded before workflow creation with operator-visible failure details when expansion cannot complete.
- **SC-005**: Source traceability review confirms `MM-573`, `manual-mm-569-mm-574`, the canonical Jira preset brief, and DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, and DESIGN-REQ-022 are preserved in MoonSpec artifacts.
