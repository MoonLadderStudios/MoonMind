# Feature Specification: Document Flattened Plan Execution Contract

**Feature Branch**: `199-document-flattened-plan-contract`
**Created**: 2026-04-17
**Status**: Draft
**Input**:

```text
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
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `local-only handoffs`.
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
```

## User Story - Flattened Plan Execution Contract

**Summary**: As a runtime contract owner, I want plan contracts to reject unresolved preset includes and treat source provenance as optional metadata so every plan executor receives the same flat graph contract regardless of how the plan was authored.

**Goal**: Operators and maintainers can trust stored plan artifacts to be executable, provenance-aware when metadata is present, and independent from preset composition semantics at runtime.

**Independent Test**: Can be fully tested by reviewing the execution tool and plan contract and validating that it defines flattened plan artifacts, invalid unresolved include entries, optional source provenance, provenance validation, authoring-origin-neutral DAG semantics, and runtime invariants that keep provenance out of executable logic.

**Acceptance Scenarios**:

1. **Given** a plan is produced from preset expansion, manual authoring, or another plan-producing tool, **When** the plan is stored for execution, **Then** the plan contains only executable nodes, edges, policies, artifacts, and tool contracts.
2. **Given** a stored plan artifact contains an unresolved preset include entry, **When** plan validation evaluates the artifact, **Then** validation rejects the artifact before execution.
3. **Given** a plan node includes source provenance, **When** the plan contract is reviewed or validated, **Then** the provenance is treated as optional traceability metadata with documented valid fields and no executable semantics.
4. **Given** a plan node has absent source provenance, **When** the plan is validated, **Then** the absence of provenance does not make the otherwise valid execution node invalid.
5. **Given** a plan node claims malformed preset provenance, **When** the plan is validated, **Then** validation rejects the malformed metadata without changing the executable meaning of valid nodes.
6. **Given** a runtime executor consumes a stored plan artifact, **When** execution begins, **Then** the executor follows the flat node-and-edge graph and does not apply nested preset semantics or provenance-based logic.
7. **Given** downstream MoonSpec, implementation notes, verification, commit text, or pull request metadata are generated for this work, **When** traceability is reviewed, **Then** the Jira issue key MM-386 remains present.

### Edge Cases

- A source document named by the Jira brief, `docs/Tasks/PresetComposability.md`, is absent in the current checkout; the preserved MM-386 Jira brief and current `docs/Tasks/SkillAndPlanContracts.md` are the active sources for this story.
- A plan may be produced by manual authoring, preset expansion, or another plan-producing tool; all origins must produce the same execution-facing graph shape.
- Source provenance may be absent; absence must remain valid when the executable plan node is otherwise valid.
- Source provenance may be present but structurally invalid; invalid provenance must be rejected without making provenance executable behavior.
- A stored artifact may contain unresolved include objects; those objects must fail validation before any executor treats them as plan nodes.

## Assumptions

- The selected runtime mode means the contract document is treated as runtime source requirements for product behavior, even though this story primarily updates canonical documentation.
- `docs/Tasks/SkillAndPlanContracts.md` is the canonical active documentation target for executable tool, plan artifact, validation, and execution semantics.
- This story does not require changing executable plan validation code unless planning discovers implementation drift from the documented runtime contract.

## Source Design Requirements

- **DESIGN-REQ-001**: Source "Plan node examples" requires optional source metadata with `binding_id`, `include_path`, `blueprint_step_slug`, and `detached` fields. Scope: in scope. Maps to FR-004, FR-005, FR-007.
- **DESIGN-REQ-019**: Source "DAG semantics" requires manual authoring, preset expansion, and other plan-producing tools to produce the same flattened node-and-edge graph. Scope: in scope. Maps to FR-008, FR-010.
- **DESIGN-REQ-020**: Source "SkillAndPlanContracts preset expansion boundary" requires preset composition to be an authoring concern and the plan contract to define only the flattened execution contract after expansion. Scope: in scope. Maps to FR-001, FR-002, FR-010.
- **DESIGN-REQ-021**: Source "PlanDefinition production rules" requires executable plan nodes only and forbids include objects in stored plan artifacts. Scope: in scope. Maps to FR-003, FR-006.
- **DESIGN-REQ-025**: Source "Plan validation" requires validation to reject unresolved preset includes and structurally invalid claimed preset provenance while allowing absent provenance. Scope: in scope. Maps to FR-005, FR-006, FR-007.
- **DESIGN-REQ-026**: Source "Execution invariants" requires nested preset semantics to be absent at runtime and provenance to never be executable logic. Scope: in scope. Maps to FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system contract MUST state that preset composition is an authoring concern completed before stored plan artifact execution.
- **FR-002**: The plan contract MUST define stored plan artifacts as flattened execution contracts containing executable nodes, edges, policies, artifacts, and tool contracts only.
- **FR-003**: Plan production rules MUST state that executable plan nodes are the only valid stored plan nodes and unresolved include objects are invalid stored plan artifact content.
- **FR-004**: Plan node examples MUST show optional source provenance metadata for `binding_id`, `include_path`, `blueprint_step_slug`, and `detached`.
- **FR-005**: Plan validation rules MUST allow absent source provenance on otherwise valid plan nodes.
- **FR-006**: Plan validation rules MUST reject unresolved preset include entries before execution.
- **FR-007**: Plan validation rules MUST reject structurally invalid claimed preset provenance without changing executable plan semantics.
- **FR-008**: DAG semantics MUST state that manual authoring, preset expansion, and other plan-producing tools all produce the same flattened node-and-edge graph for execution.
- **FR-009**: Execution invariants MUST state that nested preset semantics do not exist at runtime and source provenance is never executable logic.
- **FR-010**: Runtime behavior MUST depend only on flattened nodes, edges, policies, artifacts, and tool contracts.
- **FR-011**: Canonical documentation updates MUST remain desired-state documentation and keep volatile migration or implementation tracking out of canonical docs.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-386` and the original Jira preset brief.

### Key Entities

- **Stored Plan Artifact**: The durable plan representation accepted by the runtime executor after authoring and expansion are complete.
- **Executable Plan Node**: A stored unit of work with a stable identifier, display title, executable tool selection, inputs, and dependency relationships.
- **Unresolved Include Entry**: A preset composition object that still requires authoring-time expansion and is invalid in stored plan artifacts.
- **Source Provenance Metadata**: Optional per-node traceability data that explains authoring origin without changing runtime execution behavior.
- **Flattened Execution Graph**: The node-and-edge DAG consumed by the executor regardless of whether the plan came from manual authoring, preset expansion, or another plan-producing tool.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Review of the canonical plan contract finds one explicit statement that preset composition is an authoring concern and stored plans are flattened execution contracts after expansion.
- **SC-002**: Review of PlanDefinition rules finds unresolved include objects explicitly invalid in stored plan artifacts.
- **SC-003**: Review of plan node examples finds all four optional provenance fields: `binding_id`, `include_path`, `blueprint_step_slug`, and `detached`.
- **SC-004**: Review of validation rules confirms all three provenance states are covered: absent provenance allowed, valid provenance accepted, and structurally invalid claimed provenance rejected.
- **SC-005**: Review of DAG semantics confirms at least three plan origins produce the same flattened graph: manual authoring, preset expansion, and other plan-producing tools.
- **SC-006**: Review of execution invariants confirms zero nested preset semantics and zero provenance-based executable logic at runtime.
- **SC-007**: All six in-scope source design requirements map to at least one functional requirement, and MM-386 remains present in MoonSpec artifacts and verification evidence.
