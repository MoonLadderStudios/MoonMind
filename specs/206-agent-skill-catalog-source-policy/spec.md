# Feature Specification: Agent Skill Catalog and Source Policy

**Feature Branch**: `206-agent-skill-catalog-source-policy`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**: User description:

```text
Jira issue: MM-405 from MM project
Summary: Agent Skill Catalog and Source Policy
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-405 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-405: Agent Skill Catalog and Source Policy

Source Reference
- Source Document: docs/Tasks/AgentSkillSystem.md
- Original Jira Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §1-§7
  - AgentSkillSystem §9
  - AgentSkillSystem §17
  - SkillAndPlanContracts §1.1
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004

User Story
As a MoonMind operator, I can rely on agent skills being modeled as deployment-scoped, versioned instruction data with explicit source precedence and policy gates, so managed runs do not confuse instruction bundles with executable tools or silently trust repo-local content.

Acceptance Criteria
- Given executable tools and agent instruction bundles share the historical word skill, when contracts are validated, then ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet remain typed separately.
- Given a deployment-stored skill is edited, when the change is saved, then a new immutable version is created instead of mutating the previous version.
- Given built-in, deployment, repo, and local-only candidates exist, when policy allows them, then deterministic precedence selects the winner and records source provenance.
- Given policy forbids repo or local-only skills, when those sources contain candidates, then they are excluded before precedence is applied and cannot silently affect the run.

Requirements
- Define or preserve concrete AgentSkillDefinition, AgentSkillVersion, SkillSet, and SkillSetEntry contracts with source kind and version metadata.
- Keep executable ToolDefinition and runtime-native command contracts separate from agent-skill instruction bundles in validation and docs.
- Apply deployment policy gates to repo and local-only skill sources before selection or materialization.
- Treat repo-provided and local-only skill content as potentially untrusted input.
- Preserve MM-405 in all downstream MoonSpec artifacts, verification output, commit text, and pull request metadata.

Relevant Implementation Notes
- The canonical agent-skill design lives in `docs/Tasks/AgentSkillSystem.md`; executable tool contracts live separately in `docs/Tasks/SkillAndPlanContracts.md`.
- Agent skills are reusable instruction bundles and are not executable Temporal tools.
- ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet should remain typed separately at validation and API boundaries.
- Built-in, deployment-stored, repo-checked-in, and local-only skill candidates should be handled as distinct sources with explicit provenance.
- Deployment policy should gate repo and local-only sources before selection, resolution, or materialization.
- Runtime-visible `.agents/skills` should represent the active resolved skill set for a run, while `.agents/skills/local` remains a local-only overlay input.
- Resolved skill sets should be immutable per run or step, and workflows should carry refs to snapshots rather than large skill bodies.
- Runtime adapters own materialization of the resolved skill snapshot for each target runtime.
- Checked-in skill folders should not be mutated in place during runtime setup.

Verification
- Verify executable tool contracts and agent-skill instruction-bundle contracts remain distinguishable in models, validation, tests, and docs touched by the change.
- Verify deployment-backed skill edits create immutable versions rather than mutating previous versions.
- Verify source precedence records provenance when built-in, deployment, repo, and local-only candidates are present.
- Verify policy-denied repo and local-only skills are excluded before precedence is applied.
- Verify trusted or untrusted skill source handling is covered at the workflow/activity or adapter boundary when runtime materialization is changed.
- Preserve MM-405 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Jira link metadata at fetch time indicates MM-405 is blocked by MM-406.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-405-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-405 from MM project
Summary: Agent Skill Catalog and Source Policy
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-405 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-405: Agent Skill Catalog and Source Policy

Source Reference
- Source Document: docs/Tasks/AgentSkillSystem.md
- Original Jira Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §1-§7
  - AgentSkillSystem §9
  - AgentSkillSystem §17
  - SkillAndPlanContracts §1.1
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004

User Story
As a MoonMind operator, I can rely on agent skills being modeled as deployment-scoped, versioned instruction data with explicit source precedence and policy gates, so managed runs do not confuse instruction bundles with executable tools or silently trust repo-local content.

Acceptance Criteria
- Given executable tools and agent instruction bundles share the historical word skill, when contracts are validated, then ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet remain typed separately.
- Given a deployment-stored skill is edited, when the change is saved, then a new immutable version is created instead of mutating the previous version.
- Given built-in, deployment, repo, and local-only candidates exist, when policy allows them, then deterministic precedence selects the winner and records source provenance.
- Given policy forbids repo or local-only skills, when those sources contain candidates, then they are excluded before precedence is applied and cannot silently affect the run.

Requirements
- Define or preserve concrete AgentSkillDefinition, AgentSkillVersion, SkillSet, and SkillSetEntry contracts with source kind and version metadata.
- Keep executable ToolDefinition and runtime-native command contracts separate from agent-skill instruction bundles in validation and docs.
- Apply deployment policy gates to repo and local-only skill sources before selection or materialization.
- Treat repo-provided and local-only skill content as potentially untrusted input.
- Preserve MM-405 in all downstream MoonSpec artifacts, verification output, commit text, and pull request metadata.

Relevant Implementation Notes
- The canonical agent-skill design lives in `docs/Tasks/AgentSkillSystem.md`; executable tool contracts live separately in `docs/Tasks/SkillAndPlanContracts.md`.
- Agent skills are reusable instruction bundles and are not executable Temporal tools.
- ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet should remain typed separately at validation and API boundaries.
- Built-in, deployment-stored, repo-checked-in, and local-only skill candidates should be handled as distinct sources with explicit provenance.
- Deployment policy should gate repo and local-only sources before selection, resolution, or materialization.
- Runtime-visible `.agents/skills` should represent the active resolved skill set for a run, while `.agents/skills/local` remains a local-only overlay input.
- Resolved skill sets should be immutable per run or step, and workflows should carry refs to snapshots rather than large skill bodies.
- Runtime adapters own materialization of the resolved skill snapshot for each target runtime.
- Checked-in skill folders should not be mutated in place during runtime setup.

Verification
- Verify executable tool contracts and agent-skill instruction-bundle contracts remain distinguishable in models, validation, tests, and docs touched by the change.
- Verify deployment-backed skill edits create immutable versions rather than mutating previous versions.
- Verify source precedence records provenance when built-in, deployment, repo, and local-only candidates are present.
- Verify policy-denied repo and local-only skills are excluded before precedence is applied.
- Verify trusted or untrusted skill source handling is covered at the workflow/activity or adapter boundary when runtime materialization is changed.
- Preserve MM-405 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Jira link metadata at fetch time indicates MM-405 is blocked by MM-406.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

Single-story runtime feature request. The brief defines one operator-facing behavior slice: agent-skill catalog and source-policy contracts must model instruction bundles separately from executable tools, preserve immutable versioned skill data, and enforce source precedence and policy gates.

## User Story - Agent Skill Catalog and Source Policy

**Summary**: As a MoonMind operator, I want agent skills modeled as deployment-scoped, versioned instruction data with explicit source precedence and policy gates so managed runs do not confuse instruction bundles with executable tools or silently trust repo-local content.

**Goal**: Operators can trust that managed runs use clearly typed, policy-filtered, provenance-recorded agent-skill selections without mutating skill versions or treating instruction bundles as executable tools.

**Independent Test**: Create or inspect skill catalog entries, skill-set selections, and mixed built-in, deployment, repo, and local-only candidates under both allowed and denied policy settings. The story passes when contracts keep executable tools, runtime commands, agent skills, skill sets, and resolved skill sets distinct; deployment-backed edits create immutable versions; allowed candidates resolve with source provenance; and denied repo or local-only sources cannot affect the resolved run input.

**Acceptance Scenarios**:

1. **Given** executable tools and agent instruction bundles both use the historical word skill, **when** contracts are validated, **then** ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet remain typed separately.
2. **Given** a deployment-stored agent skill exists, **when** an operator saves an edit to it, **then** the system creates a new immutable version instead of mutating the previous version.
3. **Given** built-in, deployment, repo, and local-only skill candidates exist and policy allows them, **when** skill selection is resolved, **then** deterministic precedence selects the winner and records source provenance.
4. **Given** policy forbids repo or local-only skill sources, **when** those sources contain skill candidates, **then** they are excluded before precedence is applied and cannot silently affect the run.
5. **Given** a run or step receives a resolved agent-skill set, **when** the runtime adapter materializes it, **then** the runtime-visible skill data comes from the resolved snapshot rather than mutating checked-in skill folders in place.

### Edge Cases

- A repo-checked-in skill has the same name as a deployment-stored skill.
- A local-only skill is present while local-only sources are disabled by policy.
- An existing run is retried after a newer deployment-backed skill version is created.
- A runtime command is displayed near agent skills but must not be stored as an agent skill or resolved skill-set member.
- A skill source contains untrusted or malformed metadata.

## Assumptions

- Runtime mode is required; this story must be validated through system behavior, not documentation-only edits.
- The canonical runtime source requirements are `docs/Tasks/AgentSkillSystem.md` and `docs/Tasks/SkillAndPlanContracts.md`.
- Existing checked-in repo skill folders and local-only overlays are valid inputs, not authoritative durable storage.
- The Jira dependency note for MM-406 is preserved as source context but does not broaden this spec beyond MM-405's selected single story.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: MM-405 brief, Acceptance Criteria; `docs/Tasks/AgentSkillSystem.md` sections 1-3; `docs/Tasks/SkillAndPlanContracts.md` section 1.1): Executable tools, runtime commands, agent skill definitions, skill sets, and resolved skill sets remain typed separately. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-002** (Source: MM-405 brief, Acceptance Criteria; `docs/Tasks/AgentSkillSystem.md` sections 2, 4, and 6): Deployment-stored agent skills are versioned, and edits create new immutable versions rather than mutating previous versions. Scope: in scope. Maps to FR-003 and FR-004.
- **DESIGN-REQ-003** (Source: MM-405 brief, Acceptance Criteria; `docs/Tasks/AgentSkillSystem.md` sections 2, 5, and 6): Built-in, deployment, repo, and local-only skill candidates resolve through deterministic precedence and record source provenance when policy allows them. Scope: in scope. Maps to FR-005, FR-006, and FR-007.
- **DESIGN-REQ-004** (Source: MM-405 brief, Acceptance Criteria and Requirements; `docs/Tasks/AgentSkillSystem.md` sections 5 and 7): Policy gates exclude forbidden repo and local-only skill sources before selection or materialization, and repo/local skill content is treated as potentially untrusted. Scope: in scope. Maps to FR-008, FR-009, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST represent executable ToolDefinition contracts separately from agent instruction bundle contracts.
- **FR-002**: System MUST represent runtime-native commands separately from AgentSkillDefinition, SkillSet, and ResolvedSkillSet entries.
- **FR-003**: System MUST represent deployment-stored agent skills with stable definition identity and version metadata.
- **FR-004**: System MUST create a new immutable version when a deployment-stored agent skill is edited, preserving previous versions unchanged.
- **FR-005**: System MUST support built-in, deployment-stored, repo-checked-in, and local-only agent-skill source kinds as distinct candidate sources.
- **FR-006**: System MUST apply deterministic source precedence when multiple allowed candidates match the same skill selection.
- **FR-007**: System MUST record source kind and selected version provenance for resolved agent-skill entries.
- **FR-008**: System MUST apply deployment policy gates to repo and local-only skill sources before selection, resolution, or materialization.
- **FR-009**: System MUST exclude policy-denied repo or local-only skill candidates from resolved skill sets.
- **FR-010**: System MUST treat repo-provided and local-only skill content as untrusted input during validation and materialization.
- **FR-011**: System MUST expose a resolved agent-skill set for a run or step as an immutable snapshot or reference, not as mutable checked-in source content.
- **FR-012**: System MUST preserve MM-405 and the original Jira preset brief in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **AgentSkillDefinition**: A reusable instruction-bundle definition with identity, source kind, metadata, and version history.
- **AgentSkillVersion**: An immutable version of a deployment-stored agent skill.
- **SkillSet**: A named collection of agent-skill selections or rules.
- **SkillSetEntry**: A member or selector inside a skill set, including source and version targeting information where applicable.
- **ResolvedSkillSet**: The immutable, exact run or step skill selection after policy gates and precedence resolution.
- **Source Policy**: The rules that allow or deny skill sources before selection, resolution, and materialization.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Contract validation distinguishes ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet in 100% of covered contract cases.
- **SC-002**: Editing a deployment-stored agent skill preserves 100% of previously existing versions and creates a distinct new version.
- **SC-003**: Source precedence verification resolves 100% of mixed built-in, deployment, repo, and local-only candidate cases to the expected candidate when all relevant sources are policy-allowed.
- **SC-004**: Policy verification excludes 100% of repo and local-only candidates when those source kinds are denied.
- **SC-005**: Provenance verification records source kind and selected version metadata for 100% of resolved skill-set entries in covered cases.
- **SC-006**: Runtime materialization verification confirms resolved skill snapshots are exposed without mutating checked-in skill folders in 100% of covered adapter-boundary cases.
- **SC-007**: Verification evidence preserves MM-405 and the original Jira preset brief as the source for the feature.
