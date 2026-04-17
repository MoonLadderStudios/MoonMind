# Feature Specification: Composable Preset Expansion

**Feature Branch**: `195-composable-preset-expansion`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**:

```text
# MM-383 MoonSpec Orchestration Input

## Source

- Jira issue: MM-383
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document composable preset expansion contracts
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-383 from MM project
Summary: Document composable preset expansion contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-383 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-383: Document composable preset expansion contracts

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - Design posture throughout
  - 1. docs/Tasks/TaskPresetsSystem.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a task platform engineer, I want TaskPresetsSystem to define composable preset entries and deterministic expansion so presets can reuse other presets without changing runtime execution semantics.

Acceptance Criteria
- TaskPresetsSystem defines Preset Include, Expansion Tree, Flattened Plan, Preset Provenance, and Detachment.
- Preset version steps are documented as a union of kind: step and kind: include entries with required pinned include versions and distinct aliases for repeated child includes.
- Scope rules prevent GLOBAL presets from including PERSONAL presets and reject unreadable, missing, inactive, or incompatible includes.
- The composable expansion pipeline documents recursive resolution, cycle detection, limit enforcement, deterministic ID assignment, provenance attachment, flattening, and artifact/audit storage.
- Cycle and limit failures include enough detail to identify the include path that caused rejection.
- Save-as-preset semantics preserve intact includes only when exact provenance remains and serialize detached or custom steps as concrete steps.
- The executor boundary explicitly states that nested preset semantics are resolved before PlanDefinition storage.

Requirements
- Define preset composition as compile-time control-plane behavior only.
- Document kind: include storage semantics with pinned version, alias, input mapping, and no v1 child override behavior.
- Document deterministic resolved step ID inputs and per-step provenance shape.
- Document expand API output that can return composition and flat plan views.
- Document exact-match preservation semantics for save-as-preset.

Verification
- Confirm the canonical documentation updates preserve desired-state documentation under `docs/` and keep volatile implementation planning under `docs/tmp/`.
- Confirm coverage for DESIGN-REQ-001 through DESIGN-REQ-010, DESIGN-REQ-025, and DESIGN-REQ-026 from `docs/Tasks/PresetComposability.md`.
- Run documentation-focused validation and relevant tests for task preset behavior if implementation touches executable preset contracts.

Out of Scope
- Changing runtime execution semantics for already-expanded `PlanDefinition` storage.
- Implementing unpinned include resolution or child override behavior.
- Allowing GLOBAL presets to include PERSONAL presets.
```

## User Story - Composable Preset Expansion

**Summary**: As a task platform engineer, I want task presets to support pinned include entries that expand deterministically so reusable preset building blocks can be composed before runtime execution.

**Goal**: Preset authors can declare includes in preset versions, and operators receive a deterministic flattened plan with provenance while the executor continues to consume only resolved steps.

**Independent Test**: Can be fully tested by creating parent and child presets, expanding the parent, and verifying the flattened steps, provenance, include rejection rules, cycle and limit failures, and executor-boundary documentation without running a Temporal workflow.

**Acceptance Scenarios**:

1. **Given** an active child preset with concrete steps and an active parent preset with a pinned include entry, **When** the parent is expanded, **Then** the response contains the child steps in a deterministic flattened order with provenance that identifies the root preset, child preset, version, alias, and include path.
2. **Given** a global parent preset attempts to include a personal child preset, **When** expansion is requested, **Then** expansion is rejected before producing executable steps.
3. **Given** an include references a missing, unreadable, inactive, or input-incompatible preset version, **When** expansion is requested, **Then** expansion fails with an error that identifies the include path and reason.
4. **Given** presets include each other recursively or exceed the configured expansion step limit, **When** expansion is requested, **Then** expansion fails with an error that identifies the include path responsible for rejection.
5. **Given** a task is saved back as a preset from provenance-preserving expanded steps, **When** the saved selection exactly matches an include subtree, **Then** the include can remain intact; otherwise detached or customized steps are serialized as concrete steps.
6. **Given** a flattened plan is submitted for execution, **When** runtime execution begins, **Then** no nested preset semantics remain in the executor boundary.

### Edge Cases

- Repeated inclusion of the same child preset is allowed only when each include has a distinct alias in the parent version.
- Child preset inputs must be provided through the parent include input mapping; child presets do not receive ad hoc v1 override behavior.
- Include expansion must remain deterministic when the same root preset and inputs are expanded repeatedly.
- A source document named by the Jira brief, `docs/Tasks/PresetComposability.md`, is absent in the current checkout; the preserved MM-383 Jira brief is the canonical source for these requirements.

## Assumptions

- Include entries resolve within the existing task template catalog and use existing scope visibility rules.
- Personal presets may include global presets, but global presets may not include personal presets.
- The current runtime continues to expose legacy `steps[]` expansion output while adding composition and provenance metadata for the same flattened plan view.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Preset versions MUST support a step union with concrete `kind: step` entries and compositional `kind: include` entries.
- **FR-002**: Include entries MUST require a child preset slug, pinned child version, and distinct alias when the same parent includes child presets.
- **FR-003**: Include entries MUST support explicit input mapping into the child preset and MUST NOT support child step override behavior.
- **FR-004**: Expansion MUST recursively resolve include entries into a flattened ordered step list before execution-facing output is produced.
- **FR-005**: Expansion MUST detect include cycles and return an error that identifies the include path.
- **FR-006**: Expansion MUST enforce the root preset expansion step limit after includes are flattened and return an error that identifies the include path that exceeded the limit.
- **FR-007**: Expansion MUST reject global-to-personal includes and reject missing, unreadable, inactive, or input-incompatible child preset versions.
- **FR-008**: Expanded steps MUST include deterministic identifiers and provenance metadata that identify the root preset, source preset, pinned version, include alias, and include path.
- **FR-009**: Expansion output MUST expose both the flattened step plan and composition metadata suitable for preview or audit use.
- **FR-010**: Save-as-preset semantics MUST preserve intact include provenance only for exact matches and serialize detached or customized selections as concrete steps.
- **FR-011**: Runtime executor documentation and behavior MUST keep nested preset semantics resolved before `PlanDefinition` storage and execution.
- **FR-012**: MoonSpec artifacts, verification evidence, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-383` and the original Jira preset brief.

### Key Entities

- **Preset Include**: A versioned catalog reference inside a preset version with slug, pinned version, alias, optional scope, and input mapping.
- **Expansion Tree**: The recursive include graph resolved during expansion, including aliases and paths.
- **Flattened Plan**: The ordered concrete step list produced by expansion and consumed by existing execution boundaries.
- **Preset Provenance**: Metadata attached to each flattened step that records where the step came from.
- **Detachment**: The save-as-preset state where customized or partially selected steps become concrete steps instead of preserved includes.

## Source Design Requirements

- **DESIGN-REQ-001**: Source "Design posture throughout" requires preset composition to remain compile-time control-plane behavior only. Scope: in scope. Maps to FR-004, FR-011.
- **DESIGN-REQ-002**: Source "TaskPresetsSystem" requires terms for Preset Include, Expansion Tree, Flattened Plan, Preset Provenance, and Detachment. Scope: in scope. Maps to FR-001, FR-008, FR-010.
- **DESIGN-REQ-003**: Source "TaskPresetsSystem" requires preset version steps to be a union of concrete steps and includes. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-004**: Source "TaskPresetsSystem" requires includes to use pinned versions. Scope: in scope. Maps to FR-002.
- **DESIGN-REQ-005**: Source "TaskPresetsSystem" requires distinct aliases for repeated child includes. Scope: in scope. Maps to FR-002.
- **DESIGN-REQ-006**: Source "TaskPresetsSystem" requires scope rules that prevent global presets from including personal presets. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-007**: Source "TaskPresetsSystem" requires missing, unreadable, inactive, or incompatible includes to be rejected. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-008**: Source "TaskPresetsSystem" requires recursive resolution, cycle detection, limit enforcement, deterministic ID assignment, provenance attachment, flattening, and artifact or audit storage. Scope: in scope. Maps to FR-004, FR-005, FR-006, FR-008, FR-009.
- **DESIGN-REQ-009**: Source "TaskPresetsSystem" requires cycle and limit failures to identify the include path. Scope: in scope. Maps to FR-005, FR-006.
- **DESIGN-REQ-010**: Source "TaskPresetsSystem" requires save-as-preset exact-match preservation and detached concrete-step serialization. Scope: in scope. Maps to FR-010.
- **DESIGN-REQ-025**: Source "Cross-document invariants" requires expand API output to support composition and flat plan views. Scope: in scope. Maps to FR-009.
- **DESIGN-REQ-026**: Source "Cross-document invariants" requires executor boundaries to receive resolved nested preset semantics before `PlanDefinition` storage. Scope: in scope. Maps to FR-011.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Expanding a parent preset with one pinned child include returns the expected concrete child steps in stable order with deterministic IDs and provenance metadata.
- **SC-002**: Expansion fails with path-bearing validation errors for global-to-personal include attempts, cycles, missing or inactive child versions, incompatible child inputs, and flattened step limit excess.
- **SC-003**: Existing non-composed preset expansion behavior remains compatible for concrete-step-only presets.
- **SC-004**: Task preset system documentation describes include storage, expansion, provenance, detachment, and executor boundary semantics.
- **SC-005**: Final verification can compare implementation evidence against the preserved `MM-383` Jira preset brief.
