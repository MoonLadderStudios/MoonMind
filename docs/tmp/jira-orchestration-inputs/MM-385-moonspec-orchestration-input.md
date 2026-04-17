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

