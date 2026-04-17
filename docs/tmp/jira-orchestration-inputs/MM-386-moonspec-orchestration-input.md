# MM-386 MoonSpec Orchestration Input

## Source

- Jira issue: MM-386
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document flattened plan execution contract
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-386 from MM project
Summary: Document flattened plan execution contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-386 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-386: Document flattened plan execution contract

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 4. docs/Tasks/SkillAndPlanContracts.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-020
  - DESIGN-REQ-021
  - DESIGN-REQ-001
  - DESIGN-REQ-019
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a runtime contract owner, I want SkillAndPlanContracts to reject unresolved preset includes and treat provenance as optional metadata so the executor remains a flat graph executor regardless of authoring origin.

Acceptance Criteria
- SkillAndPlanContracts states preset composition is an authoring concern and defines only the flattened execution contract after expansion.
- PlanDefinition production rules state nodes are executable plan nodes only and include objects are invalid in stored plan artifacts.
- Plan node examples include optional source metadata with binding_id, include_path, blueprint_step_slug, and detached fields.
- Plan validation rejects unresolved preset include entries and structurally invalid claimed preset provenance while allowing absent provenance.
- DAG semantics clarify that manual authoring, preset expansion, and other plan-producing tools all produce the same flattened node-and-edge graph.
- Execution invariants state nested preset semantics do not exist at runtime and provenance is never executable logic.

Requirements
- Document the preset expansion boundary in SkillAndPlanContracts.
- Document optional source provenance on plan nodes.
- Document validation rules for absent, valid, and invalid provenance.
- Document that runtime behavior depends only on nodes, edges, policies, artifacts, and tool contracts.

Relevant Implementation Notes
- The canonical active documentation target is `docs/Tasks/SkillAndPlanContracts.md`.
- The issue references `docs/Tasks/PresetComposability.md`, but that source document is not present in the current checkout; preserve the reference as Jira traceability while applying the requested SkillAndPlanContracts changes against the repository state.
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `docs/tmp/`.
- Preset composition must be documented as an authoring-time concern before a plan artifact is finalized.
- Stored plan artifacts must be flat DAG execution contracts made of executable nodes and edges; unresolved include objects are invalid runtime input.
- Source provenance on a plan node is optional metadata for traceability, not executable logic.
- Valid provenance should support `binding_id`, `include_path`, `blueprint_step_slug`, and `detached` fields.
- Runtime behavior must depend only on flattened nodes, edges, policies, artifacts, and tool contracts.
- Manual authoring, preset expansion, and other plan-producing tools all produce the same flattened node-and-edge graph for the executor.

Verification
- Confirm `docs/Tasks/SkillAndPlanContracts.md` documents preset composition as an authoring concern and the execution contract as flat after expansion.
- Confirm PlanDefinition production rules state nodes are executable plan nodes and unresolved include objects are invalid stored plan artifacts.
- Confirm plan node examples include optional source provenance metadata with `binding_id`, `include_path`, `blueprint_step_slug`, and `detached`.
- Confirm validation rules reject unresolved preset includes and structurally invalid claimed provenance while allowing absent provenance.
- Confirm DAG semantics state manual authoring, preset expansion, and other plan-producing tools all produce the same flattened node-and-edge graph.
- Confirm execution invariants state nested preset semantics do not exist at runtime and provenance is never executable logic.
- Preserve MM-386 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-385 blocks this issue.
- MM-387 is blocked by this issue.
