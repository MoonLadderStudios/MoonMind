# Feature Specification: Managed Runtime Skill Projection

**Feature Branch**: `[208-managed-runtime-skill-projection]`
**Created**: 2026-04-18
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-407 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira brief:

# MM-407 MoonSpec Orchestration Input

## Source

- Jira issue: MM-407
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Managed Runtime Skill Projection
- Labels: `moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-407 from MM project
Summary: Managed Runtime Skill Projection
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-407 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-407: Managed Runtime Skill Projection

Source Reference
- Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §8
  - AgentSkillSystem §13-§16
  - SkillInjection §2-§16
  - ManagedAndExternalAgentExecutionModel §1-§2.5
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-016
  - DESIGN-REQ-017
  - DESIGN-REQ-021

User Story
As a managed runtime adapter, I can materialize a pinned skill snapshot into a run-scoped active backing store and expose exactly that selected set at .agents/skills with a compact activation summary, so agents see the expected path without MoonMind rewriting checked-in skill folders.

Acceptance Criteria
- Given a resolved snapshot with one skill, when the adapter prepares the runtime, then .agents/skills contains a full active root with _manifest.json and that skill's SKILL.md.
- Given a resolved snapshot with multiple skills, then only selected skills appear in the active projection and unselected repo skills are absent.
- Given a checked-in .agents/skills directory exists, then MoonMind may use it as a resolution input but does not rewrite it in place during runtime setup.
- Given .agents or .agents/skills is an incompatible file or unprojectable path, then preparation fails before runtime launch with path, object kind, attempted action, and remediation guidance.
- Given the runtime starts, then the instruction payload includes a compact activation summary and full skill bodies are available on disk, not duplicated inline.

Requirements
- Materialize the active skill bundle into a MoonMind-owned run-scoped backing directory exactly once per snapshot.
- Project the active backing store at .agents/skills for managed runtimes using adapter-compatible mechanics.
- Include only selected skills and a MoonMind-owned active manifest in the runtime-visible tree.
- Inject a compact activation summary naming active skills, visible path, hard rules, and first-read hints.
- Do not use retrieval-first loading, custom visible paths, or per-skill leaf mounting as the canonical managed-runtime path.

Relevant Implementation Notes
- Keep `.agents/skills` as the canonical runtime-visible path for the resolved active snapshot.
- Materialize the selected skill set into a MoonMind-owned run-scoped backing store before managed runtime launch.
- Expose exactly the selected active skill set at `.agents/skills`; unselected repo skills must be absent from the runtime-visible projection.
- Treat checked-in `.agents/skills` folders as resolution inputs only and do not rewrite them in place during runtime setup.
- Include a MoonMind-owned active manifest in the runtime-visible projection.
- Keep full skill bodies on disk and avoid duplicating large skill content inline in the instruction payload.
- Inject only a compact activation summary that names active skills, visible path, hard rules, and first-read hints.
- Fail before runtime launch when `.agents` or `.agents/skills` is an incompatible file or unprojectable path, with path, object kind, attempted action, and remediation guidance.

Verification
- Confirm a resolved snapshot with one skill materializes a full `.agents/skills` active root containing `_manifest.json` and that skill's `SKILL.md`.
- Confirm a resolved snapshot with multiple skills projects only selected skills and omits unselected repo skills.
- Confirm checked-in `.agents/skills` can be used as a resolution input without being rewritten in place during runtime setup.
- Confirm incompatible `.agents` or `.agents/skills` paths fail before runtime launch with actionable diagnostics.
- Confirm runtime instructions include a compact activation summary while full skill bodies are available on disk and not duplicated inline.
- Preserve MM-407 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-408 blocks this issue.
- MM-407 blocks MM-406."

## User Story - Project Active Skill Snapshot Into Managed Runtime

**Summary**: As a managed runtime adapter, I want a pinned skill snapshot projected into the canonical `.agents/skills` path so the launched agent sees exactly the selected skill set without MoonMind rewriting checked-in skill sources.

**Goal**: Managed runtime preparation exposes a run-scoped, immutable active skill projection at the stable workspace path agents already know, while retaining a MoonMind-owned backing store, compact activation metadata, and fail-fast diagnostics for unprojectable paths.

**Independent Test**: Can be fully tested by materializing one or more resolved skill snapshots into a temporary managed runtime workspace, then validating the visible `.agents/skills` tree, manifest, absent unselected skills, source-folder immutability, instruction summary, and failure behavior before any real provider runtime launches.

**Acceptance Scenarios**:

1. **Given** a resolved snapshot with one selected skill, **When** the managed runtime workspace is prepared, **Then** `.agents/skills` exposes a complete active root containing `_manifest.json` and that skill's `SKILL.md`.
2. **Given** a resolved snapshot with multiple selected skills and additional repo skills exist outside the snapshot, **When** the active projection is created, **Then** only selected skills appear under `.agents/skills` and unselected repo skills are absent.
3. **Given** a checked-in `.agents/skills` directory exists as a source input, **When** runtime materialization runs, **Then** MoonMind does not rewrite that checked-in folder in place and instead uses a MoonMind-owned run-scoped active backing store for the runtime-visible path.
4. **Given** `.agents` or `.agents/skills` is an incompatible file or otherwise unprojectable path, **When** managed runtime preparation attempts projection, **Then** preparation fails before runtime launch with the path, object kind, attempted action, and remediation guidance.
5. **Given** a runtime starts with an active snapshot, **When** the instruction payload is inspected, **Then** it includes a compact activation summary naming active skills, the visible path, hard rules, and first-read hints while full skill bodies remain available on disk and are not duplicated inline.

### Edge Cases

- A snapshot contains zero skills and still needs a stable empty active root or prompt summary.
- A stale `.agents/skills` symlink from an earlier preparation points at the wrong backing store.
- A checked-in `.agents/skills/local` overlay exists and must remain an input convention rather than the active runtime projection.
- A selected skill content artifact cannot be read before projection.
- The runtime-visible manifest and backing store disagree about selected skill names or versions.
- A workspace cannot create symlinks and must fail clearly rather than silently exposing the wrong source tree.

## Assumptions

- The historical `docs/Tools/SkillSystem.md` source reference maps to the canonical current repo documents `docs/Tasks/AgentSkillSystem.md` and `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`.
- The optional `SkillInjection` source section maps to the compact activation summary requirements in the MM-407 Jira brief and the canonical Agent Skill System path policy.
- MM-408 is preserved as dependency context from Jira links, but this spec covers only MM-407's selected single story.

## Source Design Requirements

- **DESIGN-REQ-005**: Source `docs/Tasks/AgentSkillSystem.md` section 8. The runtime-visible skill set MUST be the resolved active snapshot exposed through `.agents/skills`, not a mutable merge into user-authored checked-in folders. Scope: in scope. Maps to FR-001, FR-002, FR-003, and FR-008.
- **DESIGN-REQ-011**: Source `docs/Tasks/AgentSkillSystem.md` section 13. Managed runtimes SHOULD use hybrid materialization, combining a compact prompt index with full content materialized into workspace files. Scope: in scope. Maps to FR-004 and FR-009.
- **DESIGN-REQ-012**: Source `docs/Tasks/AgentSkillSystem.md` section 14. For managed runtimes, the active resolved skill set MUST be visible through `.agents/skills`, and the active manifest SHOULD identify active skills, where they are available, and contributing sources. Scope: in scope. Maps to FR-001, FR-002, FR-005, and FR-006.
- **DESIGN-REQ-013**: Source `docs/Tasks/AgentSkillSystem.md` section 14.2. `.agents/skills/local` MUST remain a local-only overlay input area and must not become the runtime-visible active set. Scope: in scope. Maps to FR-003 and FR-008.
- **DESIGN-REQ-014**: Source `docs/Tasks/AgentSkillSystem.md` section 14.4. Runtime-specific compatibility links MAY be created, but `.agents/skills` remains the canonical workspace-facing path. Scope: in scope. Maps to FR-001, FR-002, and FR-007.
- **DESIGN-REQ-015**: Source `docs/Tasks/AgentSkillSystem.md` section 15. Runtime-facing materialization MUST happen through activity or service boundaries and workflow payloads MUST keep large skill bodies out of history. Scope: in scope. Maps to FR-004, FR-009, and FR-010.
- **DESIGN-REQ-016**: Source `docs/Tasks/AgentSkillSystem.md` section 16. The agent-run path receives compact resolved skill refs and the runtime adapter materializes the snapshot so the underlying runtime sees a stable active skill view through `.agents/skills`. Scope: in scope. Maps to FR-001, FR-004, FR-005, and FR-009.
- **DESIGN-REQ-017**: Source `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` section 6.3. Managed runtime adapters are responsible for preparing local runtime context and materializing any active skill snapshot into the managed runtime environment before launch. Scope: in scope. Maps to FR-001, FR-004, FR-007, and FR-010.
- **DESIGN-REQ-021**: Source `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` sections 1 through 2.5. Managed runtime skill projection must preserve retry and rerun boundaries by treating the resolved snapshot as an input contract rather than re-resolving sources during runtime launch. Scope: in scope. Maps to FR-004 and FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose the run-scoped active resolved skill set at `.agents/skills` for managed runtimes.
- **FR-002**: System MUST project the active backing store through adapter-compatible mechanics so `.agents/skills` resolves to the selected active snapshot before runtime launch.
- **FR-003**: System MUST treat checked-in `.agents/skills` and `.agents/skills/local` folders as source inputs only and MUST NOT rewrite them in place during runtime setup.
- **FR-004**: System MUST materialize the selected skill snapshot into a MoonMind-owned run-scoped backing store exactly once for the snapshot before managed runtime launch.
- **FR-005**: System MUST include a MoonMind-owned `_manifest.json` in the runtime-visible active tree that lists active skills, versions, source kinds, visible path, backing path, and snapshot identity.
- **FR-006**: System MUST ensure only selected skills and the MoonMind-owned manifest appear in the active projection; unselected repo skills MUST be absent.
- **FR-007**: System MAY create runtime-specific compatibility links or mirrors, but those links MUST target the same active snapshot as `.agents/skills`.
- **FR-008**: System MUST fail before runtime launch when `.agents` or `.agents/skills` is an incompatible file, non-link directory, or otherwise unprojectable path, and the failure MUST include the path, object kind, attempted action, and remediation guidance.
- **FR-009**: System MUST provide a compact activation summary naming active skills, visible path, hard rules, and first-read hints without duplicating full skill bodies inline.
- **FR-010**: System MUST keep full skill bodies, bundles, and large manifests on disk or artifact-backed storage rather than embedding them in workflow history or instruction payloads.
- **FR-011**: System MUST preserve immutable resolved snapshot semantics across retries and reruns by materializing from the supplied snapshot input rather than ad hoc re-resolving skill sources at runtime launch.
- **FR-012**: System MUST preserve Jira issue key MM-407 and the canonical Jira preset brief in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Resolved Skill Snapshot**: Immutable selected skill set with snapshot identity, versions, provenance, and compact metadata.
- **Active Backing Store**: MoonMind-owned run-scoped directory containing the materialized selected skills and active manifest.
- **Runtime-Visible Projection**: The `.agents/skills` path exposed to managed runtimes, resolving to the active backing store.
- **Activation Summary**: Compact instruction payload section that tells the agent which skills are active, where to read them, and which path rules apply.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit coverage proves a one-skill snapshot produces `.agents/skills/_manifest.json` and `.agents/skills/<skill>/SKILL.md` before runtime launch.
- **SC-002**: Unit coverage proves a multi-skill snapshot exposes only selected skills under `.agents/skills` and omits unselected repo skills.
- **SC-003**: Boundary coverage proves checked-in skill source folders are not modified during runtime setup.
- **SC-004**: Failure coverage proves incompatible `.agents` or `.agents/skills` paths fail before runtime launch with path, object kind, attempted action, and remediation guidance.
- **SC-005**: Instruction coverage proves active skills, visible path, hard rules, and first-read hints appear in compact form without embedding full `SKILL.md` bodies inline.
- **SC-006**: Source traceability checks confirm MM-407 and DESIGN-REQ-005, DESIGN-REQ-011 through DESIGN-REQ-017, and DESIGN-REQ-021 remain present in MoonSpec artifacts and verification output.
