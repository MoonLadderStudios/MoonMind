# Feature Specification: Document Task Snapshot And Compilation Boundary

**Feature Branch**: `198-document-task-snapshot-boundary`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**:

```text
# MM-385 MoonSpec Orchestration Input

## Source

- Jira issue: MM-385
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document task snapshot and compilation boundary
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-385 from MM project
Summary: Document task snapshot and compilation boundary
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-385 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-385: Document task snapshot and compilation boundary

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 3. docs/Tasks/TaskArchitecture.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-019
  - DESIGN-REQ-015
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a control-plane maintainer, I want TaskArchitecture to treat preset compilation as a control-plane phase and preserve authored preset metadata alongside flat steps so submitted work remains executable and reconstructible without live preset lookup.

Acceptance Criteria
- TaskArchitecture system snapshot says presets are recursively composable authoring objects resolved entirely in the control plane.
- A Preset compilation subsection defines recursive resolution, tree validation, flattening, and provenance preservation before execution contract finalization.
- Task contract normalization preserves authored preset binding metadata, flattened step provenance, manual and preset-derived step order, and fully resolved execution payloads.
- TaskPayload includes optional authoredPresets and steps[].source metadata with documented runtime semantics.
- Snapshot durability requirements preserve pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Execution-plane boundary language states that workers do not expand presets or depend on live preset catalog correctness.

Requirements
- Document preset compilation as a control-plane phase.
- Document task payload snapshot fields for authored presets and step source provenance.
- Document durability rules that keep submitted runs executable after catalog changes.
- Add invariants for compile-time-only composition and no live preset lookup dependency.

Relevant Implementation Notes
- The canonical active documentation target is `docs/Tasks/TaskArchitecture.md`.
- The issue references `docs/Tasks/PresetComposability.md`, but that source document is not present in the current checkout; preserve the reference as Jira traceability while applying the requested TaskArchitecture changes against the repository state.
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `docs/tmp/`.
- Preset compilation belongs in the control plane before execution contract finalization.
- Runtime workers consume fully resolved execution payloads and must not expand presets or depend on live preset catalog correctness.
- Authored preset binding metadata, flattened step provenance, manual and preset-derived ordering, pinned bindings, include-tree summary, detachment state, and final submitted order must remain reconstructible from the submitted task snapshot.

Verification
- Confirm `docs/Tasks/TaskArchitecture.md` documents preset compilation as a control-plane phase.
- Confirm the task payload contract includes optional authored preset metadata and per-step source provenance.
- Confirm snapshot durability rules preserve authored preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Confirm execution-plane boundary language states workers receive resolved payloads and do not perform live preset expansion.
- Preserve MM-385 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-384 blocks this issue.
- MM-386 is blocked by this issue.
```

## User Story - Task Snapshot And Compilation Boundary

**Summary**: As a control-plane maintainer, I want task architecture to define preset compilation as a control-plane phase and preserve authored preset provenance in task snapshots so submitted work remains executable and reconstructible without live preset lookup.

**Goal**: Operators and maintainers can rely on the task contract and authoritative task input snapshot to contain the resolved execution payload and the authored preset provenance needed to reconstruct or audit a submitted run after preset catalog changes.

**Independent Test**: Can be fully tested by reviewing the task architecture contract and validating that it defines control-plane preset compilation, resolved execution payload boundaries, authored preset metadata, per-step source provenance, snapshot durability, and runtime worker independence from live preset expansion.

**Acceptance Scenarios**:

1. **Given** a task draft uses nested or composed presets, **When** the control plane finalizes the task contract for execution, **Then** preset resolution, tree validation, flattening, and provenance preservation are complete before the execution-plane payload is finalized.
2. **Given** a submitted task originated from presets and manual edits, **When** its authoritative task input snapshot is inspected, **Then** the snapshot preserves authored preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
3. **Given** a runtime worker consumes a submitted task, **When** execution begins, **Then** the worker receives resolved steps and does not expand presets or depend on live preset catalog correctness.
4. **Given** a preset catalog entry changes after a task is submitted, **When** the submitted task is edited, rerun, audited, or diagnosed, **Then** the preserved task snapshot remains reconstructible without relying on the current catalog state.
5. **Given** downstream MoonSpec, implementation notes, verification, commit text, or pull request metadata are generated for this work, **When** traceability is reviewed, **Then** the Jira issue key MM-385 remains present.

### Edge Cases

- A source document named by the Jira brief, `docs/Tasks/PresetComposability.md`, is absent in the current checkout; the preserved MM-385 Jira brief and current `docs/Tasks/TaskArchitecture.md` are the active sources for this story.
- A task may mix manual steps, preset-derived steps, detached preset steps, and recursively included preset steps; the final order and source metadata must remain clear.
- A preset may be deleted, deactivated, or changed after submission; the already submitted task must still be reconstructible from the snapshot.
- A worker or adapter may only receive the execution-facing step payload; it must not infer missing preset state or perform live catalog lookup.

## Assumptions

- The selected runtime mode means the architecture document is treated as runtime source requirements for product behavior, even though this story primarily updates canonical documentation.
- The existing task architecture document is the correct canonical location for the control-plane and execution-plane boundary language.
- This story does not require changing executable preset expansion code unless planning discovers implementation drift from the documented runtime contract.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system contract MUST define presets as recursively composable authoring objects that are resolved entirely in the control plane before execution-facing task contract finalization.
- **FR-002**: The control-plane contract MUST define a preset compilation phase that includes recursive resolution, tree validation, flattening, and provenance preservation.
- **FR-003**: Task contract normalization MUST preserve authored preset binding metadata, flattened step provenance, manual step order, preset-derived step order, and fully resolved execution payloads.
- **FR-004**: The task payload contract MUST include optional authored preset metadata and per-step source metadata with documented runtime semantics.
- **FR-005**: Snapshot durability rules MUST preserve pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- **FR-006**: Execution-plane boundary rules MUST state that workers do not expand presets and do not depend on live preset catalog correctness for submitted work.
- **FR-007**: Already submitted tasks MUST remain executable, reconstructible, and auditable after referenced preset catalog entries change.
- **FR-008**: Canonical documentation updates MUST remain desired-state documentation and keep volatile migration or implementation tracking out of canonical docs.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-385` and the original Jira preset brief.

### Key Entities

- **Authored Preset Binding**: The preset reference, pinned version, include alias, and input mapping that explain how the user-authored draft referenced preset catalog entries.
- **Preset Compilation**: The control-plane phase that turns composed preset authoring objects into a resolved execution-facing task payload.
- **Step Source Metadata**: Per-step provenance that identifies whether a step is manual, preset-derived, included through a preset tree, or detached from template identity.
- **Authoritative Task Input Snapshot**: The preserved representation of the submitted task draft used for edit, rerun, audit, and diagnostics.
- **Resolved Execution Payload**: The worker-facing payload whose steps no longer require live preset expansion.

## Source Design Requirements

- **DESIGN-REQ-015**: Source "Cross-document invariants" requires compile-time-only composition and no live preset lookup dependency. Scope: in scope. Maps to FR-001, FR-006, FR-007.
- **DESIGN-REQ-017**: Source "TaskArchitecture system snapshot" requires presets to be recursively composable authoring objects resolved entirely in the control plane. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-018**: Source "TaskArchitecture preset compilation" requires recursive resolution, tree validation, flattening, and provenance preservation before execution contract finalization. Scope: in scope. Maps to FR-002.
- **DESIGN-REQ-019**: Source "Task contract normalization" requires authored preset binding metadata, flattened step provenance, manual and preset-derived order, and resolved execution payloads to be preserved. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-025**: Source "Snapshot durability" requires pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order to be durable. Scope: in scope. Maps to FR-005, FR-007.
- **DESIGN-REQ-026**: Source "Execution-plane boundary" requires workers to avoid preset expansion and live catalog dependency. Scope: in scope. Maps to FR-006.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Review of the canonical task architecture contract finds one explicit preset compilation section covering recursive resolution, tree validation, flattening, and provenance preservation.
- **SC-002**: Review of the task payload contract finds authored preset metadata and per-step source metadata described with runtime semantics.
- **SC-003**: Review of snapshot durability rules finds all five required preserved values: pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- **SC-004**: Review of execution-plane boundary language confirms workers consume resolved payloads and perform zero live preset expansion or catalog lookup for submitted work.
- **SC-005**: All six in-scope source design requirements map to at least one functional requirement, and MM-385 remains present in MoonSpec artifacts and verification evidence.
