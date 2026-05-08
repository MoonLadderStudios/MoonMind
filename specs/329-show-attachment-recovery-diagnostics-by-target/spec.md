# Feature Specification: Show Attachment and Recovery Diagnostics By Target

**Feature Branch**: `329-show-attachment-recovery-diagnostics-by-target`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-635 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-635 MoonSpec Orchestration Input

## Source

- Jira issue: MM-635
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Show attachment and recovery diagnostics by target
- Priority: Medium
- Labels: `moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, `Test plan`, and `Source` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-635 from MM project
Summary: Show attachment and recovery diagnostics by target
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-635 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-635: Show attachment and recovery diagnostics by target

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 5.6 User-facing reads
- 13 Observability and operator surfaces
- 14 Boundary with page-level and subsystem docs

Coverage IDs:
- DESIGN-REQ-023
- DESIGN-REQ-024

As an operator inspecting task details, I want target-aware attachment metadata, generated context refs, recovery provenance, and explicit failure phases so I can understand outcomes without parsing raw workflow history.

Acceptance Criteria
- Task detail exposes attachment metadata by objective and step target.
- Diagnostics identify upload, validation, materialization, or context generation failures and which target failed.
- Step-aware surfaces identify current step attachment context separately from unrelated inputs.
- Task detail identifies resumed executions and preserved prior steps reused from source.
- Failed Resume diagnostics identify checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution phases.
- Detailed behavior remains delegated to related subsystem docs.

Requirements
- Details and diagnostics expose target-aware metadata, refs, recovery provenance, and failure phases without raw history parsing.
- This document is the architecture contract; detailed UI, image, skill, Temporal, ledger, and rerun behavior belongs in related docs.

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
- Breakdown decision: `moonspec-breakdown` was not run because the MM-635 Jira preset brief defines one independently testable operator-facing diagnostics story.
- Resume decision: No existing Moon Spec artifact set for `MM-635` was found under `specs/`; specification was the first incomplete stage.

## User Story - Target-Aware Task Diagnostics

**Summary**: As an operator inspecting task details, I want attachment metadata, generated context references, recovery provenance, and failure phases grouped by target so that I can understand task outcomes without parsing raw workflow history.

**Goal**: Operators can open task details and see which objective or step target owns each attachment-related input, prepared context reference, resume provenance item, and diagnostic failure phase.

**Independent Test**: Can be fully tested by viewing task details for attachment-aware tasks, step-aware tasks, resumed executions, and failed Resume attempts, then confirming the displayed metadata and diagnostics identify the relevant objective or step target and explain outcomes without requiring raw workflow-history inspection.

**Acceptance Scenarios**:

1. **Given** a task has objective-scoped and step-scoped attachments, **When** an operator opens task details, **Then** the attachment metadata is grouped by objective target and by each step target.
2. **Given** attachment preparation produced manifest or generated context references, **When** an operator reviews diagnostics, **Then** the relevant references are visible with enough context to identify the target they describe.
3. **Given** an attachment-related failure occurs, **When** the failure is shown in task details, **Then** the diagnostic identifies the affected target and whether the failure happened during upload, validation, materialization, or context generation.
4. **Given** an operator inspects a step-aware surface, **When** the current step has attachment context, **Then** that step's attachment context is distinguished from unrelated objective or other-step inputs.
5. **Given** a task detail view represents a resumed execution, **When** the operator reviews its recovery provenance, **Then** the view identifies the source execution and shows prior completed steps that were reused from that source.
6. **Given** a Resume attempt fails, **When** the operator reviews failure diagnostics, **Then** the view identifies whether the failure phase was checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution.

### Edge Cases

- A task can have no attachments; task details should avoid implying missing diagnostic evidence when no target-aware attachment data exists.
- Some targets can have attachments while others do not; empty targets must not hide populated target groups.
- Generated context references can be unavailable because preparation failed; diagnostics must expose the failure phase instead of requiring raw history parsing.
- A resumed execution can reuse some prior steps while executing later steps normally; preserved and newly executed steps must remain distinguishable.
- Detailed behavior can be governed by related page-level or subsystem docs; this story must preserve the architecture-level boundary rather than redefining every subsystem rule.

## Assumptions

- Task details are the primary operator surface for this story because the Jira brief asks operators to inspect task details.
- Normal artifact preview, download, authorization, and redaction policies continue to apply to any displayed references.
- Objective targets and step targets use the product's established task terminology.
- The story is limited to operator-visible details and diagnostics; attachment upload, storage, preparation, and Resume execution semantics remain owned by their subsystem contracts.

## Source Design Requirements

- **DESIGN-REQ-023** (`docs/Tasks/TaskArchitecture.md` lines 200-204 and 660-671): User-facing reads and operator diagnostics must expose attachment metadata by target, generated context or manifest references where appropriate, and attachment failure target plus failure phase. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-004, FR-005, and FR-006.
- **DESIGN-REQ-024** (`docs/Tasks/TaskArchitecture.md` lines 671-689): Step-aware surfaces, resumed execution details, failed Resume diagnostics, and architecture-to-subsystem boundaries must be visible without replacing the detailed contracts in related docs. Scope: in scope. Mapped to FR-007, FR-008, FR-009, FR-010, and FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Task details MUST expose attachment metadata grouped by objective target and by step target when those attachments exist.
- **FR-002**: Task details MUST clearly distinguish targets with attachments from targets without attachments.
- **FR-003**: Diagnostics MUST expose attachment manifest references when they are available and relevant to the target being inspected.
- **FR-004**: Diagnostics MUST expose generated context references when they are available and relevant to the target being inspected.
- **FR-005**: Attachment-related diagnostics MUST identify which objective or step target failed.
- **FR-006**: Attachment-related diagnostics MUST identify whether a failure occurred during upload, validation, materialization, or context generation.
- **FR-007**: Step-aware task surfaces MUST identify the current step's attachment context separately from objective-level inputs and unrelated step inputs.
- **FR-008**: Task details for resumed executions MUST identify that the execution was resumed and expose source execution provenance.
- **FR-009**: Task details for resumed executions MUST show completed prior steps that were reused from the source execution.
- **FR-010**: Failed Resume diagnostics MUST identify whether the failure occurred during checkpoint validation, workspace restoration, preserved-output injection, or failed-step execution.
- **FR-011**: Operator-visible details MUST preserve the architecture-level boundary by linking or deferring detailed subsystem behavior to the relevant page-level, image, skill, Temporal, ledger, and rerun contracts.
- **FR-012**: Operator-facing diagnostics MUST avoid requiring raw workflow-history parsing to identify target ownership, generated context references, recovery provenance, or failure phases.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-635` and the original Jira preset brief for traceability.

### Key Entities

- **Task Target**: The task objective target or a declared step target that can own attachment metadata, prepared context references, or diagnostics.
- **Attachment Diagnostic**: Operator-visible evidence describing attachment metadata, generated context references, manifest references, or failure phase for a target.
- **Recovery Provenance**: Operator-visible evidence that identifies a resumed execution, its source execution, and prior completed steps reused from that source.
- **Failure Phase**: The bounded stage label that explains where an attachment or Resume failure occurred.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation scenarios with objective-scoped and step-scoped attachments, operators can identify the owning target for every displayed attachment metadata item.
- **SC-002**: In validation scenarios with attachment preparation failures, every displayed failure identifies exactly one affected target and one bounded failure phase.
- **SC-003**: In validation scenarios with generated context or manifest references, operators can identify which target each reference describes without reading raw workflow history.
- **SC-004**: In validation scenarios with resumed executions, operators can identify the source execution and each preserved prior step shown as reused.
- **SC-005**: In validation scenarios with failed Resume attempts, operators can identify the failed Resume phase from the bounded set of checkpoint validation, workspace restoration, preserved-output injection, and failed-step execution.
- **SC-006**: Traceability review confirms `MM-635`, the original Jira preset brief, DESIGN-REQ-023, and DESIGN-REQ-024 are preserved in MoonSpec artifacts and final evidence.
