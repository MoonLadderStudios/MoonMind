# Feature Specification: Compile-Time Preset Composition With Provenance Preservation

**Feature Branch**: `341-compile-time-preset-composition`
**Created**: 2026-05-12
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-642 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-642 MoonSpec Orchestration Input

## Source

- Jira issue: MM-642
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Compile-time preset composition with provenance preservation
- Priority: Medium
- Labels: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.
- Trusted response artifact: `/work/agent_jobs/mm:0e8a2988-d5ec-40c0-abd6-ca28183deeb5/artifacts/moonspec-inputs/MM-642-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-642 from MM project
Summary: Compile-time preset composition with provenance preservation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-642 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-642: Compile-time preset composition with provenance preservation

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 5.4 Preset compilation
- Invariant 6
- Invariant 7
Coverage IDs:
- DESIGN-REQ-010
- DESIGN-REQ-011
As a control-plane engineer, I want a control-plane preset compiler that resolves recursive preset composition before execution submission, validates the include tree, flattens manual + preset-derived steps into the final submitted order with provenance, and produces a resolved execution payload that does not require live preset catalog lookup at execution time.
Acceptance Criteria
- Recursive preset composition is fully resolved before submission.
- Compiler validates the include tree (cycles, missing references, version mismatch) and surfaces explicit errors.
- Manual and preset-derived steps are flattened in their final submitted order with stable identity.
- authoredPresets bindings and steps[].source provenance (kind, presetId/slug, version, includePath, originalStepId) are preserved on the submitted contract.
- Resolved execution payload runs without reading the live preset catalog.
- Detached templates are represented in step source provenance.
Requirements
Implement preset compiler that produces both the resolved worker-facing payload and the authored binding metadata used by snapshot reconstruction.
"""

**Implementation Intent**: Runtime. The Jira preset brief selects control-plane behavior for task submission and execution boundaries; the referenced task architecture invariants are treated as runtime source requirements.

## User Story - Compile-Time Preset Composition

**Summary**: As a control-plane engineer, I want recursive preset composition resolved before execution submission so that submitted worker payloads are deterministic, self-contained, and auditable without live preset catalog lookup.

**Goal**: Ensure a submitted task that combines manual and preset-derived steps has one final executable order with durable authored-preset and per-step provenance.

**Independent Test**: Submit a task draft containing manual steps plus recursive preset-derived steps, then verify before execution starts that the submitted task has deterministic flattened steps, preserved `authoredPresets` bindings, preserved `steps[].source` provenance, and a worker-facing payload that does not require the live preset catalog.

**Acceptance Scenarios**:

1. **Given** a task draft references a preset include tree, **When** the draft is submitted, **Then** the include tree is fully resolved before execution finalization.
2. **Given** the include tree contains a cycle, missing reference, version mismatch, disabled preset, unauthorized preset, or invalid mapping, **When** submission attempts compilation, **Then** the system blocks execution finalization with an explicit validation error.
3. **Given** manual steps and preset-derived steps appear in one draft, **When** compilation succeeds, **Then** submitted steps are flattened into the final deterministic order with stable step identity.
4. **Given** preset-derived or detached steps carry origin data, **When** the submitted task snapshot is recorded, **Then** `authoredPresets` and `steps[].source` preserve kind, preset identifier or slug, version, include path, original step identifier, input mapping, and detachment state where those values exist.
5. **Given** a worker starts an already submitted task, **When** it reads the worker-facing execution payload, **Then** the payload contains resolved executable steps and does not require worker-side preset expansion or live preset catalog lookup.
6. **Given** the live preset catalog changes after submission, **When** the submitted task is reconstructed, audited, rerun, or diagnosed, **Then** the submitted snapshot and provenance preserve the original final order and source bindings.

### Edge Cases

- Recursive include trees contain cycles or unsupported include shapes.
- Nested presets reference missing, disabled, unauthorized, or mismatched-version definitions.
- Included presets provide conflicting aliases or incompatible input mappings.
- Preset-derived steps are detached or edited before submission.
- A task contains no presets and must remain a manual-only flat submission without fabricated preset metadata.
- A preset definition is modified or deleted after a task has already been submitted.

## Assumptions

- Preset composition is a control-plane submission concern, not a worker concern.
- Existing task snapshots are the durable reconstruction source for submitted work.
- MM-642 is a single-story runtime feature request because it describes one independently testable submission and execution-boundary behavior.

## Source Design Requirements

- **DESIGN-REQ-010**: Source `docs/Tasks/TaskArchitecture.md` section `11. Invariants`, invariant 6. Preset composition must be compile-time control-plane behavior, and submitted execution payloads must not require live preset lookup. Scope: in scope. Mapped to FR-001, FR-002, FR-003, and FR-005.
- **DESIGN-REQ-011**: Source `docs/Tasks/TaskArchitecture.md` section `11. Invariants`, invariant 7. Task snapshots must preserve pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order. Scope: in scope. Mapped to FR-003, FR-004, FR-006, and FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST resolve recursive preset composition before execution submission is finalized.
- **FR-002**: The system MUST validate preset include trees and block execution finalization for cycles, missing references, version mismatches, disabled or unauthorized presets, conflicting aliases, or incompatible mappings.
- **FR-003**: The system MUST flatten manual and preset-derived steps into the final deterministic submitted order with stable step identity.
- **FR-004**: Submitted task snapshots MUST preserve `authoredPresets` bindings and `steps[].source` provenance, including kind, preset identifier or slug, version, include path, original step identifier, input mapping, and detachment state where present.
- **FR-005**: Worker-facing execution payloads MUST contain resolved executable steps and MUST NOT require worker-side preset expansion or live preset catalog lookup.
- **FR-006**: Already submitted work MUST be reconstructable from submitted snapshot metadata after live preset catalog changes without changing original step order or provenance.
- **FR-007**: Manual-only task submissions MUST remain unchanged and MUST NOT receive fabricated preset binding or source metadata.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-642` and the canonical Jira preset brief.

### Key Entities

- **Task Draft**: Authoring-state task containing manual steps, optional preset selections, runtime settings, publish intent, Jira provenance, and attachments before submission.
- **Preset Include Tree**: Nested preset composition structure that must be validated and resolved before execution finalization.
- **Compiled Task Snapshot**: Authoritative submitted representation containing final ordered steps, authored preset bindings, include-tree summary, and provenance needed for audit, rerun, and reconstruction.
- **Step Source Provenance**: Per-step source metadata describing manual, preset-derived, included, or detached origin information.
- **Worker-Facing Payload**: Resolved execution contract consumed by workers after preset compilation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of submitted tasks with recursive preset includes are validated before execution finalization.
- **SC-002**: Re-submitting equivalent valid draft inputs produces the same final step order and stable identity in repeated submissions.
- **SC-003**: 100% of preset-derived or detached submitted steps with reliable origin data preserve source provenance in the submitted snapshot.
- **SC-004**: Workers can execute a compiled preset-derived task after submission without consulting the live preset catalog.
- **SC-005**: A submitted task can be reconstructed after live preset catalog changes without changing original final order, authored preset bindings, or step provenance.
- **SC-006**: Manual-only task submission behavior remains unchanged for tasks with zero presets.
- **SC-007**: Traceability review confirms `MM-642`, the canonical Jira preset brief, DESIGN-REQ-010, and DESIGN-REQ-011 remain preserved across MoonSpec artifacts and final verification evidence.
