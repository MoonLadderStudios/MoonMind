# Feature Specification: Skill Selection and Snapshot Resolution

**Feature Branch**: `[207-skill-selection-snapshot-resolution]`
**Created**: 2026-04-18
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-406 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira brief: docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md

# MM-406 MoonSpec Orchestration Input

## Source

- Jira issue: MM-406
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Skill Selection and Snapshot Resolution
- Labels: `moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-406 from MM project
Summary: Skill Selection and Snapshot Resolution
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-406 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-406: Skill Selection and Snapshot Resolution

Source Reference
- Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §10-§12
  - AgentSkillSystem §15
  - AgentSkillSystem §19
  - ManagedAndExternalAgentExecutionModel §2.5
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-019

User Story
As a task author, I can express task-wide and step-specific skill intent and have MoonMind resolve it into an immutable, artifact-backed snapshot before runtime launch, so retries, reruns, and audits reproduce the same skill set unless re-resolution is explicit.

Acceptance Criteria
- Given `task.skills` defines a baseline and `step.skills` excludes one inherited skill, when the step is prepared, then the resolved snapshot reflects the override without mutating task-level intent.
- Given a pinned skill version cannot be resolved, when resolution runs, then the workflow fails before runtime launch with an actionable validation error.
- Given a `ResolvedSkillSet` is produced, then workflow history contains only compact refs and metadata while large manifests, bodies, bundles, and source traces are artifact-backed.
- Given a retry, continue-as-new, or ordinary rerun occurs, then the same resolved snapshot is reused unless the caller explicitly requests re-resolution.

Requirements
- Collect task and step skill selectors before runtime launch.
- Resolve allowed source kinds through activity or service boundaries, not deterministic workflow code.
- Pin exact skill versions and write resolved manifest artifacts.
- Fail fast for missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, or runtime incompatibility.
- Preserve proposal, schedule, retry, rerun, and replay semantics for skill intent and resolved snapshots.

Relevant Implementation Notes
- Keep `.agents/skills` as the canonical active runtime-visible path for the resolved active snapshot.
- Treat checked-in repo skills and local-only skills as inputs to resolution, not as mutable runtime source-of-truth folders.
- Keep `.agents/skills/local` as a local-only overlay source.
- Do not embed large skill content in workflow history; workflows should carry compact refs and metadata while activities or services write artifact-backed manifests, bodies, bundles, and source traces.
- Skill source loading, resolution, manifest generation, and materialization belong at activity or service boundaries, not deterministic workflow code.
- Unsupported source kinds, missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, or runtime incompatibility should fail before runtime launch with actionable validation errors.

Verification
- Confirm task-level and step-level skill selector inputs are collected before runtime launch.
- Confirm step-level exclusions can override inherited task-level skill intent without mutating the task-level intent.
- Confirm pinned skill resolution failure stops before runtime launch with an actionable validation error.
- Confirm `ResolvedSkillSet` output stores large skill manifests, bodies, bundles, and source traces as artifact-backed data while workflow history retains only compact refs and metadata.
- Confirm retries, continue-as-new, and ordinary reruns reuse the same resolved snapshot unless explicit re-resolution is requested.
- Preserve MM-406 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-407 blocks this issue.
- MM-406 blocks MM-405."

## User Story - Resolve Skill Snapshots Before Runtime Launch

**Summary**: As a task author, I want task-wide and step-specific skill intent resolved into an immutable snapshot before runtime launch so retries, reruns, and audits reproduce the same skill set unless re-resolution is explicit.

**Goal**: MoonMind resolves applicable agent instruction bundles for each run or step before launching a managed or external runtime, records compact snapshot metadata, stores large resolved skill content in artifacts, and presents the same resolved active skill set to downstream adapters through the canonical workspace-facing path.

**Independent Test**: Can be tested by submitting task and step skill selections that include inheritance, exclusion, pinned versions, and policy-gated sources, then validating the resolved snapshot, artifact refs, workflow payload shape, and retry/rerun behavior without launching a full agent runtime.

**Acceptance Scenarios**:

1. **Given** `task.skills` defines a baseline and `step.skills` excludes one inherited skill, **When** the step is prepared for execution, **Then** the resolved snapshot reflects the override without mutating task-level intent.
2. **Given** a selected skill version is pinned but cannot be resolved, **When** snapshot resolution runs, **Then** MoonMind fails before runtime launch with an actionable validation error.
3. **Given** a `ResolvedSkillSet` is produced, **When** workflow payloads and execution metadata are inspected, **Then** workflow history contains only compact refs and metadata while large manifests, bodies, bundles, and source traces are artifact-backed.
4. **Given** a retry, continue-as-new, or ordinary rerun occurs, **When** MoonMind prepares skill context again, **Then** the same resolved snapshot is reused unless the caller explicitly requests re-resolution.
5. **Given** a runtime adapter launches a managed or external agent, **When** it receives the resolved skill context, **Then** it consumes immutable snapshot refs and does not independently re-resolve skill sources.

### Edge Cases

- A required skill is missing from all allowed sources.
- A pinned version exists in a disallowed source but not in any allowed source.
- Two allowed sources provide the same canonical skill name and precedence determines a winner.
- Source policy forbids repo or local-only skills that would otherwise match a selector.
- Large skill bodies, source traces, or materialized bundles exceed safe workflow payload size.
- A task is rerun after source skill content has changed since the original run.
- A workflow continues as new after snapshot resolution but before runtime launch.

## Assumptions

- The active runtime-visible path remains `.agents/skills`, with `.agents/skills/local` treated as a local-only overlay input rather than authoritative durable storage.
- The historical `docs/Tools/SkillSystem.md` source reference maps to the canonical current repo documents `docs/Tasks/AgentSkillSystem.md` and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`.
- The Jira dependency note for MM-407 is preserved as source context, but this spec covers only MM-406's selected single story.

## Source Design Requirements

- **DESIGN-REQ-006**: Source `docs/Tasks/AgentSkillSystem.md` sections 10 and 12. Each resolved skill set MUST be immutable, pin exact selected versions, and be created before runtime launch. Scope: in scope. Maps to FR-001, FR-003, FR-004, FR-005, and FR-006.
- **DESIGN-REQ-007**: Source `docs/Tasks/AgentSkillSystem.md` sections 10, 12, and 15. Large skill bodies, manifests, materialization bundles, and source traces MUST live in artifacts or equivalent blob storage while workflow payloads carry compact refs and metadata. Scope: in scope. Maps to FR-006 and FR-007.
- **DESIGN-REQ-008**: Source `docs/Tasks/AgentSkillSystem.md` section 11. Task-level and step-level skill selectors MUST support inheritance and explicit step overrides without mutating task-level intent. Scope: in scope. Maps to FR-001 and FR-002.
- **DESIGN-REQ-009**: Source `docs/Tasks/AgentSkillSystem.md` sections 12 and 15. Candidate source loading, policy filtering, resolution, manifest writing, and materialization MUST happen at activity or service boundaries, not deterministic workflow code. Scope: in scope. Maps to FR-004, FR-005, FR-006, and FR-008.
- **DESIGN-REQ-010**: Source `docs/Tasks/AgentSkillSystem.md` sections 12 and 19. Resolution MUST fail before runtime launch for missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, or runtime incompatibility, and reruns or continuations MUST reuse the original snapshot unless explicit re-resolution is requested. Scope: in scope. Maps to FR-005 and FR-009.
- **DESIGN-REQ-019**: Source `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` section 2.5. `MoonMind.AgentRun` and runtime adapters MUST consume immutable resolved skill refs and must not re-resolve skill sources ad hoc during retry, rerun, or launch. Scope: in scope. Maps to FR-006, FR-008, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST collect task-level and step-level agent skill selectors before preparing a runtime launch.
- **FR-002**: System MUST apply step-level skill overrides, including exclusions, to inherited task-level intent without mutating the original task-level intent.
- **FR-003**: System MUST resolve selected skills to exact immutable versions before runtime launch.
- **FR-004**: System MUST apply configured source policy before source precedence so disallowed built-in, deployment, repo, or local-only candidates cannot enter the active snapshot.
- **FR-005**: System MUST fail before runtime launch with an actionable validation error when required skills, pins, source policy, collisions, or runtime compatibility prevent deterministic resolution.
- **FR-006**: System MUST produce a compact `ResolvedSkillSet` reference and metadata for downstream workflow and adapter use.
- **FR-007**: System MUST store large resolved manifests, bodies, materialization bundles, and source traces outside workflow history as artifact-backed data.
- **FR-008**: System MUST perform source loading, policy filtering, manifest generation, and materialization through activity or service boundaries rather than deterministic workflow code.
- **FR-009**: System MUST reuse the same resolved snapshot across retries, continue-as-new, and ordinary reruns unless explicit re-resolution is requested.
- **FR-010**: System MUST ensure managed and external runtime adapters consume the immutable resolved snapshot refs without independently re-resolving skill sources.
- **FR-011**: System MUST expose the active resolved skill set through `.agents/skills` while keeping `.agents/skills/local` as a local-only overlay input.
- **FR-012**: System MUST preserve Jira issue key MM-406 and the canonical Jira preset brief in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Skill Selector**: Task-level or step-level declaration of requested agent instruction bundles, including includes, excludes, and pinned versions.
- **Source Policy**: The allowed skill source kinds and shadowing rules that determine which candidates may participate in resolution.
- **ResolvedSkillSet**: Immutable snapshot containing selected skills, versions, source provenance, compact metadata, and artifact refs.
- **Runtime Skill Materialization**: Runtime-facing rendering of a `ResolvedSkillSet` through workspace files, prompt bundles, retrieval manifests, or equivalent adapter-specific delivery.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A task with inherited skill intent and one step-level exclusion resolves to the expected active skill set in deterministic unit or workflow-boundary coverage.
- **SC-002**: A pinned missing skill version fails before runtime launch with an actionable validation error in deterministic coverage.
- **SC-003**: Boundary coverage proves workflow payloads carry only compact `ResolvedSkillSet` refs and metadata while large manifests, bodies, bundles, and source traces are artifact-backed.
- **SC-004**: Retry, continue-as-new, and rerun coverage proves the original resolved snapshot is reused unless explicit re-resolution is requested.
- **SC-005**: Adapter-boundary coverage proves runtime launch consumes resolved snapshot refs and does not perform ad hoc source re-resolution.
- **SC-006**: Source traceability checks confirm MM-406, DESIGN-REQ-006 through DESIGN-REQ-010, and DESIGN-REQ-019 remain present in MoonSpec artifacts and verification output.
