# Feature Specification: Disabled Skills On Demand Controls

**Feature Branch**: `315-disabled-skills-on-demand-controls`
**Created**: 2026-05-08
**Status**: Draft
**Input**: Trusted Jira preset brief for MM-612 from `/work/agent_jobs/mm:dbba0e4a-6d65-495d-935e-d128cd7379e3/artifacts/jira/MM-612-moonspec-orchestration-input.md`. Preserve `MM-612` and the original preset brief for final verification.

Preserved source Jira preset brief: `MM-612` from the trusted Jira preset brief handoff, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response synthesized into `/work/agent_jobs/mm:dbba0e4a-6d65-495d-935e-d128cd7379e3/artifacts/jira/MM-612-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-612` under `specs/`, so `Specify` is the first incomplete stage.
Runtime intent: Jira Orchestrate always runs as a runtime implementation workflow. Source design references in the brief are treated as runtime source requirements.

## Original Preset Brief

````text
# MM-612 MoonSpec Orchestration Input

## Source

- Jira issue: MM-612
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add disabled-by-default Skills On Demand controls
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-612 from MM project
Summary: Add disabled-by-default Skills On Demand controls
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Steps/SkillsOnDemand.md
Source Title: Skills On Demand
Source Sections:
- 1. Purpose
- 3. Desired-State Summary
- 4. Feature Flag
- 7.1 When disabled
- 9.1 Initial launch

Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-011
- DESIGN-REQ-012

As a MoonMind operator, I want Skills On Demand controlled by a global disabled-by-default setting so managed agents cannot discover or request additional Skills unless the deployment intentionally enables the capability.

Acceptance Criteria
- The default setting is false and supports `MOONMIND_SKILLS_ON_DEMAND_ENABLED` and `WORKFLOW_SKILLS_ON_DEMAND_ENABLED` aliases.
- When disabled, query and request calls return status `denied`, code `feature_disabled`, and no Skill catalog results.
- No derived `ResolvedSkillSet` is created when the flag is disabled.
- Runtime activation text does not expose Skills On Demand commands when command exposure is controllable, or reports disabled when hiding is not possible.

Requirements
- Add a namespaced global feature flag with deterministic default false behavior.
- Gate all Skills On Demand command paths before catalog lookup or resolution.
- Preserve the normal initial skill resolution path and compact active snapshot refs.

Relevant Source Design Notes
- `docs/Steps/SkillsOnDemand.md` section 1 defines Skills On Demand as a controlled extension to the normal Agent Skill System: task and step intent still select the initial skills, MoonMind resolves an immutable `ResolvedSkillSet` before runtime launch, workflows and runtime requests carry compact refs, and managed runtimes receive a compact activation summary plus an active read-only `.agents/skills` projection.
- Section 3 requires the disabled state to prevent runtime command exposure, deny attempted calls with `feature_disabled`, return no Skill catalog query results, and avoid creating additional Skill snapshots.
- Section 4 defines the first implementation as one global boolean flag with default `false`, exposed through `MOONMIND_SKILLS_ON_DEMAND_ENABLED` and `WORKFLOW_SKILLS_ON_DEMAND_ENABLED`. The flag gates whether query/request functionality can be called at all and does not replace existing skill source, version, runtime, or policy checks.
- Section 7.1 allows disabled runtime activation text either to omit Skills On Demand entirely or to state that it is disabled for the run. Where runtime command exposure is controllable, the runtime must not expose `moonmind.skills.query` or `moonmind.skills.request`; otherwise attempted calls must return `{"status":"denied","code":"feature_disabled","message":"Skills On Demand is disabled for this deployment."}`.
- Section 9.1 preserves the initial launch lifecycle: collect task-level and step-level Skill selectors, resolve the initial `ResolvedSkillSet`, persist manifest and Skill body artifacts, materialize the active bundle, and launch the managed runtime with the active snapshot ref.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-612 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
````

<!-- Moon Spec specs contain exactly one independently testable user story. Use /moonspec-breakdown for technical designs that contain multiple stories. -->

## User Story - Disabled Skills On Demand Control

**Summary**: As a MoonMind operator, I want Skills On Demand to be controlled by a global disabled-by-default setting so managed agents cannot discover or request additional Skills unless the deployment intentionally enables the capability.

**Goal**: A deployment that has not opted into Skills On Demand keeps managed agents limited to their initially selected active Skills, denies any on-demand query or request attempt with a clear disabled result, and preserves normal initial Skill resolution behavior.

**Independent Test**: Configure a deployment with the Skills On Demand setting unset or explicitly disabled, launch or simulate a managed runtime with an initial active Skill snapshot, and verify that on-demand query and request attempts are denied without catalog results or new snapshots while the original active Skill snapshot remains available.

**Acceptance Scenarios**:

1. **Given** the Skills On Demand setting is unset, **When** a managed runtime is prepared, **Then** Skills On Demand is treated as disabled and no on-demand capability is enabled by default.
2. **Given** the setting is explicitly disabled through either supported configuration name, **When** a managed runtime attempts to query additional Skill metadata, **Then** the query is denied with status `denied`, code `feature_disabled`, and no catalog results.
3. **Given** the setting is explicitly disabled through either supported configuration name, **When** a managed runtime requests additional Skills, **Then** the request is denied with status `denied`, code `feature_disabled`, and no derived active Skill snapshot is created.
4. **Given** command exposure can be controlled for a runtime, **When** the runtime activation context is prepared while Skills On Demand is disabled, **Then** the runtime does not expose on-demand query or request commands.
5. **Given** command exposure cannot be fully hidden for a runtime, **When** the runtime activation context is prepared while Skills On Demand is disabled, **Then** the activation text clearly states that Skills On Demand is disabled for the run.
6. **Given** Skills On Demand is disabled, **When** the normal initial managed runtime launch path resolves and exposes preselected active Skills, **Then** that initial Skill snapshot remains available through the normal activation context and compact references.

### Edge Cases

- Missing, blank, or unrecognized Skills On Demand configuration is treated the same as disabled, with no permissive fallback.
- If both supported configuration names are present, the deployment resolves one deterministic disabled or enabled state according to the product's established configuration precedence.
- A denied query or request must not partially create, persist, or expose a new active Skill snapshot.
- Runtimes that cannot hide commands still produce the same denial response for attempted calls.

## Assumptions

- The supported configuration names are operator-facing contract values and are therefore included in the specification despite being technical-looking strings.
- Existing configuration precedence rules apply when multiple configuration sources provide a Skills On Demand value.
- This story covers disabled-by-default control behavior only; broader enabled-mode Skill discovery, approval, audit, and materialization behavior remains governed by later source-design sections outside this selected story.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Steps/SkillsOnDemand.md` section 1 Purpose, lines 13-24; section 9.1 Initial launch, lines 336-342): MoonMind MUST preserve normal initial Skill selection, immutable active snapshot resolution, compact snapshot references, and managed runtime activation while adding the controlled Skills On Demand extension. Scope: in scope. Maps to FR-005, FR-006.
- **DESIGN-REQ-011** (Source: `docs/Steps/SkillsOnDemand.md` section 3 Desired-State Summary, lines 64-71; section 7.1 When disabled, lines 201-218): When Skills On Demand is disabled, managed runtimes MUST NOT expose on-demand commands where controllable, attempted calls MUST be denied with `feature_disabled`, query results MUST be empty, and no additional Skill snapshot may be created. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-006.
- **DESIGN-REQ-012** (Source: `docs/Steps/SkillsOnDemand.md` section 4 Feature Flag, lines 84-119): Skills On Demand MUST be governed by one global boolean feature gate with default disabled behavior, supported names `MOONMIND_SKILLS_ON_DEMAND_ENABLED` and `WORKFLOW_SKILLS_ON_DEMAND_ENABLED`, and continued enforcement of existing Skill source and policy rules. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-005.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide one global Skills On Demand control that defaults to disabled when no operator setting is supplied.
- **FR-002**: The system MUST accept `MOONMIND_SKILLS_ON_DEMAND_ENABLED` and `WORKFLOW_SKILLS_ON_DEMAND_ENABLED` as supported operator-facing names for the same global control.
- **FR-003**: When Skills On Demand is disabled, any managed-runtime request to query additional Skill metadata MUST return status `denied`, code `feature_disabled`, and no Skill catalog results.
- **FR-004**: When Skills On Demand is disabled, any managed-runtime request to add Skills MUST return status `denied`, code `feature_disabled`, and MUST NOT create, persist, or activate a derived Skill snapshot.
- **FR-005**: When Skills On Demand is disabled, runtime activation MUST avoid exposing on-demand query or request commands where command exposure is controllable.
- **FR-006**: When command exposure cannot be fully hidden, runtime activation MUST clearly communicate that Skills On Demand is disabled and attempted on-demand calls remain denied.
- **FR-007**: Disabled Skills On Demand behavior MUST NOT change normal initial Skill selection, immutable active snapshot resolution, compact snapshot references, or initial runtime activation for preselected Skills.
- **FR-008**: The system MUST continue to enforce existing Skill source, runtime compatibility, and policy rules; the global control only determines whether on-demand query and request paths are callable.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-612` and the original Jira preset brief.

### Key Entities

- **Skills On Demand Control**: The global operator setting that determines whether managed runtimes may use on-demand Skill query and request capabilities.
- **Managed Runtime Activation Context**: The operator-visible and runtime-visible startup context that tells a managed agent which Skills are active and whether any on-demand capability is available.
- **On-Demand Skill Query**: A managed-runtime attempt to discover additional Skill metadata beyond the initial active Skill set.
- **On-Demand Skill Request**: A managed-runtime attempt to add one or more Skills after initial launch.
- **Active Skill Snapshot**: The immutable set of Skills resolved before runtime launch or, when allowed in later stories, derived from an approved on-demand request.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a deployment with no Skills On Demand setting supplied, 100% of observed managed-runtime on-demand query and request attempts are denied with `feature_disabled`.
- **SC-002**: For each supported configuration name, an explicit disabled value produces the same denial behavior for query and request attempts.
- **SC-003**: Disabled on-demand query attempts return zero Skill catalog results.
- **SC-004**: Disabled on-demand request attempts create zero derived active Skill snapshots.
- **SC-005**: Normal initial active Skill snapshot availability remains unchanged in disabled deployments.
- **SC-006**: Traceability review confirms `MM-612`, the preserved preset brief, and all in-scope `DESIGN-REQ-*` mappings remain present in MoonSpec artifacts and final verification evidence.
