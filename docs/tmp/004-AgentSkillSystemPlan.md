# Agent Skill System Plan

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-03-30  
Canonical doc: `docs/Tasks/AgentSkillSystem.md`  
Related: `docs/Tasks/SkillAndPlanContracts.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Temporal/TemporalArchitecture.md`, `docs/UI/MissionControlArchitecture.md`, `AGENTS.md`

---

## 1. Purpose

This document tracks the phased implementation work required to make the Agent Skill System fully real in MoonMind.

The canonical design is defined in `docs/Tasks/AgentSkillSystem.md`. This plan exists to:

1. break the target-state design into implementation phases
2. identify the code, API, workflow, and UI work required
3. preserve a realistic migration order
4. keep temporary rollout tasks out of the canonical design doc

---

## 2. Current state snapshot

MoonMind is already partially aligned with the target direction, but the system is not yet end-to-end coherent.

### Already present

- `.agents/skills` and `.agents/skills/local` already exist as workspace conventions
- parts of the runtime side already assume a shared active skill view
- MoonMind already prefers artifact-backed execution context over large workflow payloads
- the docs already distinguish, at least partially, between executable tool contracts and agent instruction bundles

### Partially present

- shared-skill runtime behavior exists in some adapter and workspace conventions
- local-only mirror behavior exists, but is not yet governed by a fully modeled control-plane contract
- runtime-side skill exposure is ahead of the control-plane and storage model

### Missing

- a deployment-backed skill catalog
- canonical `AgentSkillDefinition`, `SkillSet`, and `ResolvedSkillSet` contracts in code
- end-to-end resolution of built-in, deployment, repo, and local skill sources
- task/step skill selectors in the canonical execution payload
- a formal materialization pipeline owned by activities
- Mission Control submit/detail support for agent skills
- clear rerun, proposal, and schedule semantics for skill snapshots
- complete observability and policy enforcement

---

## 3. End state

MoonMind should support the following target-state behavior:

1. agent skills are stored as versioned deployment data
2. built-in, deployment, repo, and local sources participate in skill resolution
3. tasks and steps can select skills and skill sets explicitly
4. MoonMind resolves all applicable skills into an immutable `ResolvedSkillSet`
5. workflows pass only refs to the resolved snapshot
6. runtime adapters materialize that snapshot for the target runtime
7. `.agents/skills` is the canonical runtime-visible path
8. `.agents/skills/local` remains the local-only overlay path
9. Mission Control can show selected skills, resolved versions, and source provenance
10. reruns reuse the original resolved snapshot by default unless explicit re-resolution is requested

---

## 4. Planning constraints

The implementation should follow these constraints.

1. **Canonical docs first.** The architecture must be clearly documented before implementation expands.
2. **No semantic overloading.** Agent instruction bundles and executable tool contracts must remain separate concepts.
3. **Artifact discipline.** Large skill bodies, manifests, and bundles must remain out of workflow history.
4. **Workflow determinism.** Resolution and materialization happen at activity boundaries, not in workflow code.
5. **Pre-release cleanup bias.** Because MoonMind is pre-release, prefer replacing ambiguous internal patterns instead of preserving unnecessary compatibility layers.
6. **Temporal safety.** Changes to workflow and activity payloads must remain safe for in-flight runs or be cut over explicitly.
7. **Stable workspace contract.** `.agents/skills` must remain the canonical workspace-facing active-skill path.
8. **No in-place mutation of user-authored checked-in skill files.**

---

## 5. Phase overview

Implementation should proceed in this order:

1. Phase 1 — terminology, contracts, and doc alignment
2. Phase 2 — deployment-backed storage and core data model
3. Phase 3 — resolution engine and immutable snapshot pipeline
4. Phase 4 — managed runtime materialization and adapter integration
5. Phase 5 — task, step, and workflow integration
6. Phase 6 — Mission Control surfaces and observability
7. Phase 7 — proposals, schedules, reruns, policy hardening, and cleanup

Each phase is expected to leave the system in a coherent intermediate state.

---

## 6. Phase 1 — terminology, contracts, and doc alignment

### Goal

Make the architecture unambiguous before deeper implementation begins.

### Deliverables

- canonical doc set aligned to the new model
- explicit terminology split between executable tools and agent skills
- stable path policy for `.agents/skills` and `.agents/skills/local`

### Tasks

- [x] Create `docs/Tasks/AgentSkillSystem.md` as the canonical design doc
- [x] Update `docs/Tasks/SkillAndPlanContracts.md` to explicitly distinguish `ToolDefinition` from `AgentSkillDefinition`
- [x] Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` to reference resolved skill snapshots and adapter-owned materialization
- [x] Update `docs/Temporal/TemporalArchitecture.md` to include agent skills as a first-class MoonMind domain concept
- [x] Update `docs/Tasks/TaskArchitecture.md` to reflect task/step skill selection and control-plane resolution
- [x] Update `docs/UI/MissionControlArchitecture.md` to reflect submit/detail visibility for skill selection and resolved snapshots
- [x] Update `docs/Temporal/WorkflowArtifactSystemDesign.md` to make skill-related artifacts first-class
- [x] Update `docs/Temporal/ActivityCatalogAndWorkerTopology.md` to define `agent_skill.*` activities and fleet routing
- [x] Update `docs/Tasks/TaskProposalSystem.md` to preserve explicit agent skill intent upon promotion
- [x] Update `AGENTS.md` so the existing shared-skills runtime notes match the canonical design
- [x] Optional: Update `docs/Temporal/WorkflowSchedulingGuide.md` and `docs/Temporal/TaskExecutionCompatibilityModel.md` to fully close the architecture loop
- [x] Remove or rewrite ambiguous doc language that uses “skill” interchangeably for tools and instruction bundles without qualification

### Exit criteria

- all relevant docs consistently distinguish tools from agent skills
- `.agents/skills` and `.agents/skills/local` are documented consistently
- no major canonical doc still implies that repo folders are the only source of truth

---

## 7. Phase 2 — deployment-backed storage and core data model

### Goal

Introduce a real managed skill catalog owned by MoonMind.

### Deliverables

- persistent storage for agent skills and versions
- basic skill set model
- source-kind and provenance model
- artifact-backed storage for skill bodies

### Tasks

- [x] Define concrete backend models for `AgentSkillDefinition`
- [x] Define concrete backend models for `AgentSkillVersion`
- [x] Define concrete backend models for `SkillSet`
- [x] Define concrete backend models for `SkillSetEntry`
- [x] Define source-kind enum or equivalent contract for `built_in`, `deployment`, `repo`, and `local`
- [x] Define supported format enum or equivalent for markdown or future bundle formats
- [x] Store skill bodies in artifact/blob storage rather than directly in workflow payloads
- [x] Add checksums or content digests for immutable version verification
- [x] Add migrations for the new storage tables and indices
- [x] Add service-layer validation for skill-name uniqueness and version immutability
- [x] Add policy fields or associated settings needed to gate repo and local skill sources
- [x] Add basic CRUD or internal service methods for deployment-stored skills
- [x] Add tests for immutable version creation, source-kind handling, and validation failures

### Exit criteria

- MoonMind can persist deployment-scoped agent skills and versions
- skill content is stored durably outside workflow history
- the storage model is strong enough to support later resolution work

---

## 8. Phase 3 — resolution engine and immutable snapshot pipeline

### Goal

Resolve all applicable skill inputs into a stable per-run or per-step snapshot.

### Deliverables

- resolution service
- precedence rules enforcement
- immutable `ResolvedSkillSet`
- artifact-backed manifest generation

### Tasks

- [x] Define concrete runtime contract for `ResolvedSkillSet`
- [x] Define concrete runtime contract for source provenance and resolution inputs
- [x] Implement built-in skill source loader
- [x] Implement deployment skill source loader
- [x] Implement repo checked-in skill source loader
- [x] Implement `.agents/skills/local` source loader
- [x] Implement precedence rules from the canonical doc
- [x] Implement collision detection and deterministic resolution failures
- [x] Implement policy enforcement for disallowed source kinds
- [x] Implement explicit include, exclude, and pinned-version selectors
- [x] Implement `task.skills` baseline resolution
- [x] Implement `step.skills` override and inheritance behavior
- [x] Generate explicit `ResolvedSkillSet` artifacts for runs and, where needed, for steps
- [x] Generate prompt-index artifacts and runtime materialization bundle artifacts based on the resolved snapshot
- [x] Apply correct artifact link types (e.g., input.manifest, input.instructions) for skill-related execution context
- [x] Apply proper retention and redaction defaults for all skill artifacts per `WorkflowArtifactSystemDesign.md`
- [x] Return compact refs and metadata rather than full bodies in workflow history
- [x] Add tests covering:
  - [x] source precedence
  - [x] collisions
  - [x] pinned version failures
  - [x] policy blocks
  - [x] step-level exclusions
  - [x] deterministic snapshot reuse across retries


### Exit criteria

- MoonMind can produce an immutable `ResolvedSkillSet` from all supported source kinds
- the result is artifact-backed and reproducible
- the output is ready for runtime materialization

---

## 9. Phase 4 — managed runtime materialization and adapter integration

### Goal

Make managed runtimes consume resolved skill snapshots consistently.

### Deliverables

- runtime materialization pipeline
- active skill workspace projection
- adapter integration with the resolved snapshot model

### Tasks

- [ ] Define concrete runtime contract for `RuntimeSkillMaterialization`
- [ ] Implement materialization activity or service for workspace-mounted skill content
- [ ] Implement materialization of a compact prompt index or summary for hybrid mode
- [ ] Implement the active manifest/index written alongside the active skill view
- [ ] Materialize the active run snapshot into a run-scoped location rather than mutating checked-in skill folders
- [ ] Expose the active set through `.agents/skills` as the immutable, canonical runtime-visible active path
- [ ] Preserve `.agents/skills/local` explicitly as a local-only overlay input path, not an active output
- [ ] Enforce the "do not mutate checked-in skill folders in place" constraint at the materialization boundary
- [ ] Produce adapter compatibility links (`skills_active` fallback) and prompt-index generations where historically required
- [ ] Update managed runtime adapters to accept `resolved_skillset_ref` or equivalent input
- [ ] Update managed runtime launch flows to consume the resolved active set and prompt index
- [ ] Ensure any runtime-specific compatibility links still preserve `.agents/skills` as the canonical path
- [ ] Add tests for:
  - [ ] `.agents/skills` active projection
  - [ ] non-mutation of checked-in skill files
  - [ ] hybrid materialization output
  - [ ] adapter consumption of resolved snapshot refs

### Exit criteria

- managed runtimes consume resolved skill snapshots through a stable active workspace path
- materialization is adapter-aware but based on a shared canonical snapshot
- no in-place mutation of checked-in skill content occurs during normal run setup

---

## 10. Phase 5 — task, step, and workflow integration

### Goal

Make agent skills part of the real execution contract.

### Deliverables

- canonical task/step skill selectors in backend contracts
- workflow/activity integration with resolution and materialization
- agent-run support for skill snapshot refs

### Tasks

- [ ] Add canonical `task.skills` handling to task submission models
- [ ] Add canonical `step.skills` handling to plan-node or step execution models
- [ ] Ensure `step.skills` correctly inherits from and overrides `task.skills`
- [ ] Add validation for invalid skill selectors during task submit or plan validation
- [ ] Add explicit `agent_skill.*` activity family for resolution vs materialization
- [ ] Route `agent_skill.materialize` and related preparation activities to `mm.activity.agent_runtime` or a capable preparation fleet
- [ ] Ensure the workflow explicitly propagates `resolved_skillset_ref` across activity boundaries
- [ ] Pass `resolved_skillset_ref` or equivalent through the `MoonMind.AgentRun` path
- [ ] Ensure workflow payloads carry refs and metadata only
- [ ] Ensure retries and continuation paths reuse the same resolved snapshot
- [ ] Add workflow-boundary tests for:
  - [ ] task-level skill selection
  - [ ] step-level override behavior
  - [ ] child workflow agent-run dispatch with skill snapshot ref
  - [ ] rerun behavior reusing the original snapshot by default

### Exit criteria

- agent skills are part of the real execution path
- both `MoonMind.Run` and `MoonMind.AgentRun` can carry resolved skill snapshot refs safely
- workflow and activity boundaries remain Temporal-idiomatic

---

## 11. Phase 6 — Mission Control surfaces and observability

### Goal

Expose the system clearly to operators.

### Deliverables

- submit-time skill selection surfaces
- detail-page visibility into resolved snapshots
- operator-visible provenance and materialization data

### Tasks

- [ ] Add submit-time layout/UX for agent skill selection (sets, includes, explicit excludes) without overloading default tabular data
- [ ] Add task detail surface for resolved skill snapshot visibility (provenance, snapshot ID, selected versions, source precedence)
- [ ] Implement compact provenance display for skill origins (e.g., distinguishing repo from built-in or default deployment scopes)
- [ ] Add proposal-review visibility showing either explicit skill selectors or explicit "inherited defaults" status
- [ ] Apply strict debug visibility rules for raw manifests, prompt indexes, and materialization refs
- [ ] Add API fields needed for the above surfaces
- [ ] Add redaction and access checks for debug metadata
- [ ] Add end-to-end tests for submit/detail rendering of skill data

### Exit criteria

- operators can see which skills were selected and which versions actually ran
- Mission Control can explain where resolved skills came from
- the UI reflects the canonical design rather than older ad hoc runtime behavior

---

## 12. Phase 7 — proposals, schedules, reruns, policy hardening, and cleanup

### Goal

Close the remaining semantic gaps and remove older ambiguity.

### Deliverables

- stable rerun and schedule semantics
- proposal integration
- policy hardening
- cleanup of superseded ambiguous paths and contracts

### Tasks

- [ ] Update proposal payload components to store and preserve `task.skills` and `step.skills` intent
- [ ] Add promotion-time verification/validation ensuring promotion does not silently drop or drift incompatible skill selectors
- [ ] Harden scheduled-run semantics matching tasks (i.e. strictly preserving selectors vs blindly resolving default states)
- [ ] Define Task Execution Compatibility Model updates reflecting how skill-selector payloads traverse versions
- [ ] Clean up all ambiguous "skill" terminology across older system interfaces, proposal schemas, and related legacy queues
- [ ] Ensure reruns reuse the original `ResolvedSkillSet` by default, skipping explicit re-resolution
- [ ] Add explicit re-resolution path only where intentionally supported
- [ ] Harden policy enforcement around repo and local source usage
- [ ] Add admin or operator controls for enabling or disabling repo/local skill sources
- [ ] Remove or rewrite ambiguous older internal terminology and ad hoc runtime-side assumptions
- [ ] Add compatibility or cutover handling for any in-flight workflow payload changes that affect already-running executions
- [ ] Add regression tests for:
  - [ ] proposal promotion semantics
  - [ ] scheduled run behavior
  - [ ] rerun default snapshot reuse
  - [ ] source-policy enforcement
  - [ ] in-flight safety for any changed Temporal-facing payloads

### Final Polish

- [ ] Update `README.md` with targeted public-facing descriptions of the Agent Skill System mechanics
- [ ] Ensure `AGENTS.md` and related contributor instructions remain 100% synchronized with the finalized architectural capabilities
- [ ] Verify overall cohesiveness between canonical specs, temporary tracker plans, and external documentation before release

### Exit criteria

- proposal, schedule, rerun, and policy behavior are all explicit and tested
- old ambiguous internal patterns are removed or fully superseded
- the system is coherent across docs, APIs, workflows, adapters, and UI

---

## 13. Recommended delivery order inside the codebase

The practical code-first sequence should be:

1. backend models and migrations
2. source loaders and resolution engine
3. resolved snapshot artifact generation
4. runtime materialization services
5. task and workflow contract integration
6. managed runtime adapter integration
7. Mission Control UI
8. proposal, schedule, and rerun hardening

This order minimizes churn by establishing the canonical core before surfacing it widely.

---

## 14. Test strategy

The implementation should include tests at multiple layers.

### Unit tests

- source loading
- precedence resolution
- collision failures
- policy gates
- version immutability
- materialization helpers

### Workflow-boundary tests

- `task.skills` and `step.skills` propagation
- resolution before runtime launch
- `MoonMind.AgentRun` carrying resolved snapshot refs
- retry and rerun behavior using pinned snapshots
- compatibility coverage when Temporal-facing payload shapes change

### End-to-end or integration tests

- managed runtime workspace exposure through `.agents/skills`
- non-mutation of checked-in skill folders
- Mission Control submit/detail behavior
- proposal and schedule semantics

---

## 15. Risks and watchpoints

The main risks are:

1. continuing to overload “skill” semantically in code or docs
2. letting runtime-side conventions outrun the control-plane model again
3. accidentally storing large skill bodies in workflow history
4. allowing local-only skills to bypass policy invisibly
5. mutating checked-in repo skill files during materialization
6. introducing workflow-payload changes that break in-flight runs
7. making reruns silently drift by re-resolving latest skills unintentionally

These risks should be checked explicitly during PR review.

---

## 16. Completion criteria

This plan is complete when all of the following are true:

1. the canonical docs are aligned
2. deployment-backed agent skill storage exists
3. built-in, deployment, repo, and local sources resolve through one engine
4. tasks and steps can select skills explicitly
5. each run uses an immutable `ResolvedSkillSet`
6. managed runtimes consume the active set through `.agents/skills`
7. `.agents/skills/local` remains the local-only overlay path
8. Mission Control can display selected and resolved skill information
9. rerun, proposal, and schedule semantics are explicit and tested
10. older ambiguous internal patterns have been removed or fully superseded

---

## 17. Suggested follow-on work after this plan

After the core system is complete, likely follow-on work includes:

1. richer retrieval-mode support for large skill catalogs
2. approvals or governance for deployment-stored skills
3. import/export tooling between repo and deployment catalogs
4. richer UI authoring for skills and skill sets
5. advanced analytics on which skills most improve run outcomes