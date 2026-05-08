# Feature Specification: Refresh Managed Runtimes After Derived Skill Activation

**Feature Branch**: `318-refresh-managed-runtimes-after-derived-skill-activation`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-615 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-615 MoonSpec Orchestration Input

## Source

- Jira issue: MM-615
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Refresh managed runtimes after derived Skill activation
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/artifacts/moonspec-inputs/MM-615-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.
- Labels: moonmind-workflow-mm-7bdf3ad6-c14c-4add-bc67-7352bceee655

## Canonical MoonSpec Feature Request

Jira issue: MM-615 from MM project
Summary: Refresh managed runtimes after derived Skill activation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-615 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-615: Refresh managed runtimes after derived Skill activation

Source Reference
Source Document: docs/Steps/SkillsOnDemand.md
Source Title: Skills On Demand
Source Sections:
- 6. Core Invariants
- 7.2 When enabled
- 10. Materialization and Runtime Refresh
- 11. External Agents
- 14. Security Rules
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-010
- DESIGN-REQ-012
- DESIGN-REQ-014

As a managed runtime, I want MoonMind to materialize an approved derived Skill snapshot and provide a compact activation update only after the new bundle is ready so I never observe a partially active Skill set.

Acceptance Criteria
- Derived snapshots are fully materialized and verified before a runtime is told they are active.
- The runtime-visible projection is switched atomically where supported or deferred to a documented next-turn/controlled steer point.
- Activation results include compact activation_summary and materialization fields without large Skill bodies.
- Materialization_failed and runtime_refresh_failed outcomes preserve the current active snapshot and produce diagnostics.
- External agents are not exposed to Skills On Demand in v1 unless equivalent authenticated controls and governed materialization exist.

Requirements
- Extend AgentSkillMaterializer or adapter boundary behavior for derived snapshot refresh.
- Ensure runtime adapters cannot independently broaden active Skill sets.
- Keep repo-authored Skill sources and local-only overlays separate from MoonMind-owned runtime projection state.

## Relevant Jira Links From Trusted Issue Response

- MM-615 blocks MM-614: Resolve approved on-demand Skill requests into derived snapshots. Current linked status at fetch time: Done.
- MM-615 is blocked by MM-616: Record audit events and failure diagnostics for Skills On Demand. Current linked status at fetch time: Backlog.

## Relevant Implementation Notes

- Source design path: `docs/Steps/SkillsOnDemand.md`.
- Section 6 Core Invariants: Skills On Demand must be enabled, MoonMind resolves requested Skills, existing snapshots are immutable, `.agents/skills` remains runtime projection state, adapters must not broaden active Skill sets independently, workflow history carries compact refs, denied requests do not change active snapshots, and materialization failure must not partially activate a new Skill set.
- Section 7.2 When enabled: activation summaries should tell managed agents that Skills On Demand is enabled, that MoonMind must approve and resolve requested Skills before activation, and that full active Skill bodies stay under `.agents/skills`.
- Section 10 Materialization and Runtime Refresh: derived snapshots should be fully materialized and verified before a runtime is told they are active; v1 may defer live projection switching to the next managed-session turn or controlled steer point to avoid races.
- Section 11 External Agents: Skills On Demand v1 is scoped to managed runtimes; external agents require authenticated MoonMind-mediated controls, bounded metadata, immutable refs, governed materialization, and equivalent audit before exposure.
- Section 14 Security Rules: runtime refresh must not publish `.agents/skills` projection changes as repo-authored changes, hidden content and secrets must not be exposed, and request paths must enforce normal policy.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for managed runtime Skill activation refresh: materialize and verify approved derived Skill snapshots before exposing activation updates, preserve current active snapshots on materialization or refresh failure, keep runtime-visible Skill projections governed by MoonMind, and preserve MM-615 traceability.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing MoonSpec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Activation-Ready Derived Skill Snapshot Refresh

**Summary**: As a managed runtime, I want MoonMind to materialize an approved derived Skill snapshot and provide a compact activation update only after the new bundle is ready so that I never observe a partially active Skill set.

**Goal**: A managed runtime receives a compact, governed activation update only after MoonMind has fully materialized and verified a derived Skill snapshot, while the current active snapshot remains visible and unchanged whenever materialization or refresh fails.

**Independent Test**: Exercise the managed-runtime Skill activation refresh path with approved derived snapshots, materialization failures, runtime refresh failures, and external-agent contexts; verify that activation is announced only after a complete ready snapshot exists, failures preserve the current snapshot, activation output stays compact, and external agents receive no v1 Skills On Demand exposure.

**Acceptance Scenarios**:

1. **Given** an approved derived Skill snapshot has been resolved for a managed runtime, **When** MoonMind prepares runtime activation, **Then** MoonMind fully materializes and verifies the derived bundle before telling the runtime it is active.
2. **Given** the runtime projection can be switched atomically, **When** the derived snapshot is ready, **Then** the runtime-visible projection changes from the previous snapshot to the derived snapshot without exposing a partial Skill tree.
3. **Given** atomic projection switching is not available during the current turn, **When** the derived snapshot is ready, **Then** MoonMind defers activation to the next managed-session turn or controlled steer point and tells the runtime when to read the newly active Skill files.
4. **Given** materialization fails before activation, **When** MoonMind reports the result, **Then** the current active snapshot remains active and diagnostics identify a `materialization_failed` outcome without exposing large Skill bodies.
5. **Given** runtime refresh fails after materialization, **When** MoonMind reports the result, **Then** the current active snapshot remains active and diagnostics identify a `runtime_refresh_failed` outcome.
6. **Given** an external agent context requests Skills On Demand v1 activation, **When** MoonMind evaluates exposure, **Then** MoonMind does not expose on-demand activation unless equivalent authenticated controls and governed materialization exist.

### Edge Cases

- A runtime is reading `.agents/skills` while a derived snapshot becomes ready.
- Derived snapshot manifest or checksum verification fails after files are written to the backing store.
- Activation metadata is requested for a very large Skill bundle.
- A refresh update is retried after a transient runtime communication failure.
- Repo-authored Skill sources or local-only overlays appear in the workspace during refresh.

## Assumptions

- This story starts after an on-demand request has already been approved and a derived snapshot has been selected for activation; request approval and derived snapshot creation are covered by MM-614.
- Live mid-turn projection mutation is optional for v1; a next-turn or controlled steer-point activation is acceptable when atomic switching cannot be guaranteed.
- Existing managed-runtime activation summary and materialization concepts can be extended rather than replaced.
- Audit event persistence for this feature family is covered separately by MM-616, but this story must produce enough diagnostic data for that later audit path.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Steps/SkillsOnDemand.md` section 6, lines 181-195): Skills On Demand must preserve immutable snapshots, keep `.agents/skills` as MoonMind-owned runtime projection state, prevent adapters from independently broadening active Skill sets, keep workflow history compact, and avoid partial activation on materialization failure. Scope: in scope. Maps to FR-001, FR-002, FR-006, FR-007, FR-010.
- **DESIGN-REQ-002** (Source: `docs/Steps/SkillsOnDemand.md` section 7.2, lines 221-231): Enabled runtimes should receive compact activation guidance that MoonMind must approve and resolve Skills before activation and that active Skill bodies remain under `.agents/skills`. Scope: in scope. Maps to FR-004, FR-008.
- **DESIGN-REQ-003** (Source: `docs/Steps/SkillsOnDemand.md` section 10.1, lines 392-403): A derived snapshot should be fully materialized, manifest/checksum verified, projection-switched where supported, and only then announced to the managed agent. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-004** (Source: `docs/Steps/SkillsOnDemand.md` section 10.2, lines 405-416): v1 may avoid live mid-turn mutation by resolving and materializing a derived snapshot, then sending updated activation on the next managed-session turn or controlled steer point. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-005** (Source: `docs/Steps/SkillsOnDemand.md` section 10.3, lines 418-422): Retrieval-mode or large-catalog futures must still provide governed refs or helper access rather than direct unrestricted catalog access. Scope: in scope. Maps to FR-008, FR-010.
- **DESIGN-REQ-006** (Source: `docs/Steps/SkillsOnDemand.md` section 11, lines 426-438): External agents are out of v1 scope unless equivalent authenticated MoonMind-mediated controls, bounded metadata, immutable refs, governed materialization, and audit exist. Scope: in scope as an exclusion. Maps to FR-009.
- **DESIGN-REQ-007** (Source: `docs/Steps/SkillsOnDemand.md` section 12, lines 442-455): Skills On Demand failures, including materialization failure, must leave the active snapshot unchanged. Scope: in scope. Maps to FR-006, FR-007.
- **DESIGN-REQ-008** (Source: `docs/Steps/SkillsOnDemand.md` section 14, lines 536-544): Refresh must not expose secrets or hidden Skill bodies, grant arbitrary Skill ref reads, bypass policy, treat repo/local sources as mutable runtime state, or publish `.agents/skills` projection changes as repo-authored changes. Scope: in scope. Maps to FR-008, FR-010, FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST fully materialize a derived Skill snapshot into runtime-owned projection state before announcing it as active to a managed runtime.
- **FR-002**: System MUST verify the derived snapshot's manifest and content integrity before it can become runtime-visible as the active Skill set.
- **FR-003**: System MUST prevent managed runtimes from observing a partially written `.agents/skills` projection during derived snapshot activation.
- **FR-004**: System MUST send managed runtimes a compact activation update only after the derived snapshot is ready for use.
- **FR-005**: System MUST defer activation to the next managed-session turn or a controlled steer point when atomic runtime-visible projection switching is not available.
- **FR-006**: System MUST preserve the current active snapshot when derived snapshot materialization fails before activation.
- **FR-007**: System MUST preserve the current active snapshot when runtime refresh or activation update delivery fails.
- **FR-008**: Activation results MUST include compact activation summary and materialization status fields without embedding large Skill bodies, hidden Skill content, secrets, or unrestricted body-readable refs.
- **FR-009**: System MUST NOT expose Skills On Demand activation to external agents in v1 unless equivalent authenticated controls, bounded metadata, immutable refs, governed materialization, and audit controls exist.
- **FR-010**: Runtime adapters MUST NOT independently broaden active Skill sets or treat repo-authored Skill sources and local-only overlays as mutable runtime projection state.
- **FR-011**: Runtime refresh MUST NOT publish `.agents/skills` projection changes as repo-authored changes.
- **FR-012**: Materialization and runtime refresh failures MUST produce diagnostics that distinguish `materialization_failed` from `runtime_refresh_failed`.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-615` and the canonical Jira preset brief.

### Key Entities

- **Derived Skill Snapshot**: The approved immutable Skill set selected for activation after an on-demand request.
- **Runtime Projection**: The managed runtime-visible active Skill tree, conventionally exposed at `.agents/skills`, backed by MoonMind-owned materialization state.
- **Activation Update**: Compact runtime-facing metadata that identifies the active snapshot status, activation summary, materialization state, and any diagnostics without embedding Skill bodies.
- **Refresh Failure Diagnostic**: Safe structured evidence describing why materialization or runtime refresh did not activate the derived snapshot.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of successful derived snapshot activations announce activation only after materialization and verification have completed.
- **SC-002**: 100% of simulated partial-write or verification-failure cases keep the previous active snapshot visible to the managed runtime.
- **SC-003**: 100% of runtimes without atomic projection support receive activation only at the next managed-session turn or controlled steer point.
- **SC-004**: 100% of activation outputs include compact activation summary and materialization status data without embedding Skill bodies.
- **SC-005**: 100% of materialization and runtime refresh failure cases preserve the previous active snapshot and emit distinguishable diagnostics.
- **SC-006**: External-agent Skills On Demand activation exposure remains at 0 v1 paths unless equivalent authenticated controls and governed materialization are present.
- **SC-007**: Traceability review confirms `MM-615`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-008 remain preserved across MoonSpec artifacts and final verification evidence.
