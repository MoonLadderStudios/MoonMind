# Feature Specification: Operator Observability for Attachments, Recovery, and Resume Diagnostics

**Feature Branch**: `350-operator-observability-diagnostics`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-651 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-651 MoonSpec Orchestration Input

## Source

- Jira issue: MM-651
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Operator observability for attachments, recovery, and Resume diagnostics
- Priority: Medium
- Labels: `moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, `Test plan`, and `Source` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-651 from MM project
Summary: Operator observability for attachments, recovery, and Resume diagnostics
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-651 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-651: Operator observability for attachments, recovery, and Resume diagnostics

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 5.6 User-facing reads
- 13 Observability and operator surfaces
- Invariant 12

Coverage IDs:
- DESIGN-REQ-012
- DESIGN-REQ-030
- DESIGN-REQ-031

As an operator, I want Mission Control to expose attachment metadata by target on task detail, surface manifest/context refs in diagnostics, identify failure phases (upload/validation/materialization/context generation) and the failing target on attachment failures, identify resumed executions with preserved prior steps shown as reused from the source run, and identify Resume failure phases (checkpoint validation, workspace restoration, preserved-output injection, failed-step execution) so that I can diagnose attachment- and Resume-related issues without parsing raw workflow history, while compatibility shims do not drift the canonical objective vs step-target meaning.

Acceptance Criteria
- Task detail surfaces attachment metadata grouped by target (objective vs step).
- Diagnostics expose manifest and generated context refs where appropriate.
- Attachment failures identify both the failing target and the failing phase (upload, validation, materialization, context generation).
- Step-aware surfaces show each step's attachment context separately from unrelated step inputs.
- Resumed executions are clearly identified and preserved prior steps render as reused from the source run with provenance.
- Resume failure diagnostics identify the failing phase (checkpoint validation, workspace restoration, preserved-output injection, failed-step execution).
- Compatibility shims preserve canonical objective vs step target meaning without drift.

Requirements
- Implement task-detail attachment surfaces, diagnostics surfaces for prepare and Resume, and explicit guardrails ensuring compatibility-layer mappings do not change canonical target meaning.

Relevant Implementation Notes
- Treat `docs/Tasks/TaskArchitecture.md` as the source design reference for user-facing reads, operator observability surfaces, and invariant coverage.
- Preserve target semantics for objective attachments and step attachments across UI, diagnostics, compatibility-layer mappings, and verification evidence.
- Surface manifest refs, generated context refs, failure phase, failing target, resumed execution provenance, and reused prior-step provenance without requiring operators to parse raw workflow history.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input type: Single-story runtime feature request.
- Runtime decision: Jira Orchestrate always runs as a runtime implementation workflow, and `docs/Tasks/TaskArchitecture.md` is treated as runtime source requirements.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-651 Jira preset brief defines one independently testable operator observability story.
- Resume decision: No existing Moon Spec artifact set for `MM-651` was found under `specs/`; specification was the first incomplete stage.

## User Story - Operator Observability Diagnostics

**Summary**: As an operator inspecting task details, I want attachment metadata, generated context references, recovery provenance, and explicit failure phases grouped by target so that I can diagnose attachment and Resume outcomes without parsing raw workflow history.

**Goal**: Operators can open task details and diagnostics and immediately identify the objective or step target, relevant prepared references, recovery provenance, and bounded failure phase for attachment and Resume outcomes.

**Independent Test**: Can be fully tested by viewing task details and diagnostics for attachment-aware tasks, step-aware tasks, resumed executions, attachment failures, and failed Resume attempts, then confirming that the displayed evidence identifies target ownership, relevant references, reused prior steps, and failure phase without raw workflow-history inspection.

**Acceptance Scenarios**:

1. **Given** a task has objective-scoped and step-scoped attachments, **When** an operator opens task details, **Then** attachment metadata is grouped by objective target and by each step target.
2. **Given** attachment preparation produced manifest or generated context references, **When** an operator reviews diagnostics, **Then** those references are visible where appropriate and identify the target they describe.
3. **Given** an attachment-related failure occurs, **When** the failure is shown to an operator, **Then** the diagnostic identifies the failing objective or step target and whether the phase was upload, validation, materialization, or context generation.
4. **Given** a step-aware surface displays a current step, **When** that step has attachment context, **Then** the surface identifies that step's context separately from objective inputs and unrelated step inputs.
5. **Given** an execution was resumed from a failed source execution, **When** an operator opens task details, **Then** the view identifies the execution as resumed and shows preserved prior steps as reused from the source run with provenance.
6. **Given** a Resume attempt fails, **When** an operator reviews diagnostics, **Then** the diagnostic identifies whether the failure phase was checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution.
7. **Given** compatibility aliases or mappings are present, **When** operator-visible target metadata is shown, **Then** objective-scoped and step-scoped target meaning remains unchanged.

### Edge Cases

- A task can have no attachments; task details should avoid implying missing evidence when no target-aware attachment data exists.
- Some targets can have attachments while others do not; empty targets must not hide populated target groups.
- Manifest or generated context references can be unavailable because preparation failed; diagnostics must expose the bounded failure phase instead.
- A resumed execution can reuse completed prior steps while executing the failed step normally; preserved and newly executed work must remain distinguishable.
- A compatibility mapping can receive legacy or alias-shaped target data; the operator-facing result must preserve canonical objective versus step target meaning.

## Assumptions

- Task details and diagnostics are the primary operator surfaces because the Jira brief asks operators to diagnose issues without raw workflow-history parsing.
- Normal artifact preview, authorization, and redaction policies continue to apply to displayed references.
- Objective targets and step targets use the product's established task terminology.
- This story is limited to operator-visible observability and semantic guardrails; attachment upload, storage, preparation, and Resume execution behavior remain owned by their subsystem contracts.

## Source Design Requirements

- **DESIGN-REQ-012** (`docs/Tasks/TaskArchitecture.md` invariant 12, lines 608-612): Compatibility aliases and migration layers must not change the canonical meaning of objective-scoped versus step-scoped attachments. Scope: in scope. Mapped to FR-011 and FR-012.
- **DESIGN-REQ-030** (`docs/Tasks/TaskArchitecture.md` section 5.6 lines 200-204 and section 13 lines 660-671): User-facing reads and operator diagnostics must expose attachment metadata by target, manifest references, generated context references, failing target, and attachment failure phase. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, and FR-010.
- **DESIGN-REQ-031** (`docs/Tasks/TaskArchitecture.md` section 13 lines 671-673): Step-aware surfaces, resumed execution details, preserved-step provenance, and failed Resume diagnostics must be visible without requiring raw workflow-history parsing. Scope: in scope. Mapped to FR-007, FR-008, FR-009, FR-010, and FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Task details MUST expose attachment metadata grouped by objective target and by step target when those attachments exist.
- **FR-002**: Task details MUST clearly distinguish targets that have attachments from targets that do not have attachments.
- **FR-003**: Diagnostics MUST expose attachment manifest references when they are available and relevant to the target being inspected.
- **FR-004**: Diagnostics MUST expose generated context references when they are available and relevant to the target being inspected.
- **FR-005**: Attachment-related diagnostics MUST identify which objective or step target failed.
- **FR-006**: Attachment-related diagnostics MUST identify whether a failure occurred during upload, validation, materialization, or context generation.
- **FR-007**: Step-aware surfaces MUST identify the current step's attachment context separately from objective-level inputs and unrelated step inputs.
- **FR-008**: Task details for resumed executions MUST identify that the execution was resumed and expose source-run provenance.
- **FR-009**: Task details for resumed executions MUST show completed prior steps that were preserved and reused from the source run.
- **FR-010**: Operator-facing diagnostics MUST avoid requiring raw workflow-history parsing to identify target ownership, manifest references, generated context references, recovery provenance, or failure phases.
- **FR-011**: Compatibility-layer mappings MUST preserve canonical objective-scoped versus step-scoped target meaning in operator-visible details and diagnostics.
- **FR-012**: Operator-visible target metadata MUST not retarget, merge, or relabel objective-scoped attachments as step-scoped attachments, or step-scoped attachments as objective-scoped attachments.
- **FR-013**: Failed Resume diagnostics MUST identify whether the failure occurred during checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-651` and the original Jira preset brief for traceability.

### Key Entities

- **Task Target**: The task objective target or a declared step target that can own attachment metadata, prepared context references, or diagnostics.
- **Attachment Diagnostic**: Operator-visible evidence describing attachment metadata, manifest references, generated context references, failing target, or attachment failure phase for a target.
- **Recovery Provenance**: Operator-visible evidence identifying a resumed execution, its source run, and prior completed steps reused from that source.
- **Failure Phase**: The bounded stage label that explains where an attachment or Resume failure occurred.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation scenarios with objective-scoped and step-scoped attachments, operators can identify the owning target for every displayed attachment metadata item.
- **SC-002**: In validation scenarios with attachment preparation failures, every displayed failure identifies exactly one affected target and one bounded failure phase.
- **SC-003**: In validation scenarios with generated context or manifest references, operators can identify which target each reference describes without reading raw workflow history.
- **SC-004**: In validation scenarios with resumed executions, operators can identify the source run and each preserved prior step shown as reused.
- **SC-005**: In validation scenarios with failed Resume attempts, operators can identify the failed Resume phase from the bounded set of checkpoint validation, workspace restoration, preserved-output injection, and failed-step execution.
- **SC-006**: Traceability review confirms `MM-651`, the original Jira preset brief, DESIGN-REQ-012, DESIGN-REQ-030, and DESIGN-REQ-031 are preserved in MoonSpec artifacts and final evidence.
