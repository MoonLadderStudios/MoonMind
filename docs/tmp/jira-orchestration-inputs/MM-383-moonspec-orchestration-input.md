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
