# Feature Specification: Compile Step Type Payloads Into Runtime Plans and Promotable Proposals

**Feature Branch**: `286-compile-step-type-payloads`
**Created**: 2026-04-29
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-567 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-567 MoonSpec Orchestration Input

## Source

- Jira issue: MM-567
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Compile Step Type payloads into runtime plans and promotable proposals
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-567-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-567 from MM project
Summary: Compile Step Type payloads into runtime plans and promotable proposals
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-567 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-567: Compile Step Type payloads into runtime plans and promotable proposals

Source Reference
Source Document: docs/Steps/StepTypes.md
Source Title: Step Types
Source Sections:
- 7.1 Authoring payload
- 7.2 Runtime plan mapping
- 13. Proposal and Promotion Semantics
- 14. Migration Guidance
- 15. Non-Goals
Coverage IDs:
- DESIGN-REQ-008
- DESIGN-REQ-013
- DESIGN-REQ-016
- DESIGN-REQ-018
- DESIGN-REQ-019

As an operator, I can trust executable Step Type payloads to compile into runtime plan nodes and proposals without live preset lookup, hidden preset work, or user-facing Temporal terminology.

Acceptance Criteria
- Executable Tool and Skill steps compile into canonical runtime plan materialization.
- Preset provenance is retained as audit metadata but runtime execution succeeds from the flat executable payload.
- Stored promotable proposals are executable by default and do not silently re-expand live presets.
- Promotion validates the reviewed flat payload.
- Refreshing from a preset catalog is an explicit user action with preview and validation.
- Activity remains an implementation detail in runtime code and docs, not a user-facing Step Type.

Requirements
- Tool and Skill step runtime translations are implementation concerns hidden behind Step Type authoring.
- Preset-derived metadata supports audit and reconstruction but does not affect correctness.
- Runtime contract convergence aligns proposal promotion, task editing, and execution reconstruction with Step Type semantics.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-567` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-567` and local artifact `artifacts/moonspec-inputs/MM-567-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-567`, so `Specify` was the first incomplete MM-567 stage.

## User Story - Compile Step Type Runtime and Proposal Payloads

**Summary**: As an operator, I can trust executable Tool and Skill Step Type payloads to compile into runtime plan nodes and promotable proposals without hidden preset execution or user-facing Temporal terminology.

**Goal**: Runtime execution and proposal promotion use the reviewed flat executable payload as the source of truth, while retaining preset provenance strictly as metadata for audit, review, reconstruction, and explicit refresh workflows.

**Independent Test**: Submit executable Tool and Skill step payloads with preset-derived source metadata, materialize them into runtime plan nodes, create/promote stored task proposals from the flat payload, and verify unresolved Preset steps or Activity-labeled steps fail before runtime execution.

**Acceptance Scenarios**:

1. **SCN-001 - Tool runtime node**: **Given** an executable step with `type: "tool"` and a typed Tool payload, **When** the runtime planner materializes the task, **Then** it produces a typed tool plan node and preserves preset source metadata as node input metadata only.
2. **SCN-002 - Skill runtime node**: **Given** an executable step with `type: "skill"` and a Skill payload, **When** the runtime planner materializes the task, **Then** it produces an agent runtime plan node selected by the Skill payload rather than by hidden preset state.
3. **SCN-003 - Proposal preservation**: **Given** a task proposal stores preset-derived executable Tool or Skill steps, **When** the proposal is read or promoted, **Then** preset provenance remains visible as metadata and the reviewed flat payload is validated for execution.
4. **SCN-004 - No live preset re-expansion**: **Given** a stored promotable proposal references preset provenance, **When** promotion starts, **Then** the system does not require live preset lookup or silently re-expand the catalog entry for correctness.
5. **SCN-005 - Unresolved Preset rejection**: **Given** a stored or submitted executable task still contains `type: "preset"`, **When** validation or promotion runs, **Then** it fails before runtime materialization.
6. **SCN-006 - Activity remains internal**: **Given** a submitted step labels its user-facing Step Type as `activity` or `Activity`, **When** executable validation runs, **Then** the payload is rejected and no user-facing Activity Step Type is accepted.

### Edge Cases

- Preset source metadata is present without an authored preset binding; execution must still use the flat Tool or Skill step.
- Promotion applies an allowed runtime override; reviewed steps, instructions, and provenance still come from the stored proposal payload.
- A proposal stores authored preset bindings for audit; promotion must not depend on resolving the preset catalog.
- A generated Tool step carries a Skill-like legacy tool representation; explicit Step Type validation must reject conflicting non-skill Tool payloads.
- Documentation or UI language attempts to expose Temporal Activity as a Step Type.

## Assumptions

- Earlier Step Type stories delivered explicit payload validation and draft reconstruction; this story verifies runtime and proposal convergence for MM-567 and may reuse those implementations as evidence.
- Refreshing a proposal or draft from a newer preset version is a separate explicit preview/apply action and is not part of default promotion.
- Existing proposal storage is sufficient; no new persistent storage is needed.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Functional Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-008 | `docs/Steps/StepTypes.md` section 7.1 | Executable task submission should contain Tool and Skill steps by default. | In scope | FR-001, FR-005 |
| DESIGN-REQ-013 | `docs/Steps/StepTypes.md` section 7.2 | Executable Tool and Skill steps compile into runtime plan nodes; Preset has no runtime node by default. | In scope | FR-001, FR-002, FR-005 |
| DESIGN-REQ-016 | `docs/Steps/StepTypes.md` section 13 | Stored promotable task payloads should already be executable and flattened by default. | In scope | FR-003, FR-004, FR-006 |
| DESIGN-REQ-018 | `docs/Steps/StepTypes.md` section 14 | Runtime contract convergence aligns proposal promotion, task editing, and execution reconstruction with Step Type semantics. | In scope | FR-001, FR-003, FR-004, FR-007 |
| DESIGN-REQ-019 | `docs/Steps/StepTypes.md` section 15 | Activity remains an implementation detail and presets are not hidden runtime work. | In scope | FR-005, FR-008 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Runtime planning MUST materialize executable `type: "tool"` steps as typed tool plan nodes and executable `type: "skill"` steps as agent runtime plan nodes.
- **FR-002**: Preset-derived source metadata MUST be preserved for audit, UI grouping, review, and reconstruction without being required for runtime correctness.
- **FR-003**: Stored task proposals MUST preserve flat executable task payloads and expose preset provenance as metadata when present.
- **FR-004**: Proposal promotion MUST validate and execute the reviewed stored flat payload without silently re-expanding live preset catalog entries.
- **FR-005**: Executable validation MUST reject unresolved `type: "preset"` steps before runtime materialization.
- **FR-006**: Proposal promotion MUST reject stored proposals whose task payload is not executable under the canonical task contract.
- **FR-007**: Runtime overrides during promotion MUST be bounded controls that do not rewrite reviewed steps, instructions, or provenance metadata.
- **FR-008**: User-facing Step Type validation and documentation MUST keep `Activity` as an internal Temporal implementation detail, not an accepted user-facing Step Type.

### Key Entities

- **Executable Step**: A submitted Tool or Skill step that can be materialized into a runtime plan node.
- **Runtime Plan Node**: The internal execution node produced from a validated executable step.
- **Preset Provenance**: Metadata that records where an executable step came from without controlling runtime correctness.
- **Promotable Proposal**: A stored task proposal whose reviewed `taskCreateRequest` can be validated and submitted for execution.
- **Reviewed Flat Payload**: The stored task payload containing concrete executable steps, not unresolved preset placeholders.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Runtime planner tests verify explicit Tool and Skill steps produce the correct plan node categories and carry preset source metadata only as metadata.
- **SC-002**: Task contract tests verify executable submissions accept Tool and Skill and reject Preset, Activity, and conflicting payloads.
- **SC-003**: Proposal service tests verify promotion preserves preset provenance, rejects unresolved Preset steps, and applies runtime override without changing reviewed steps.
- **SC-004**: Proposal API tests verify task previews classify preset provenance from stored flat payload metadata.
- **SC-005**: Documentation verification confirms Step Type docs keep Activity internal and state that promotion validates flat payloads without live preset lookup.
- **SC-006**: Final verification preserves Jira issue key `MM-567` and the original Jira preset brief in active MoonSpec artifacts and delivery metadata.
