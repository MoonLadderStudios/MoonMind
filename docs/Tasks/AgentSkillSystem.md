# Agent Skill System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-30  
Related: `docs/Tasks/SkillAndPlanContracts.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Temporal/TemporalArchitecture.md`, `docs/UI/MissionControlArchitecture.md`, `AGENTS.md`  
Implementation tracking: `docs/tmp/AgentSkillSystemPlan.md`

---

## 1. Purpose

This document defines MoonMind's canonical design for **agent skills as deployment-scoped data**.

An **agent skill** in this document means a reusable instruction bundle that helps an agent perform work more effectively. Examples include:

- project conventions
- coding style guidance
- reusable review procedures
- troubleshooting playbooks
- workflow-specific operating instructions
- domain knowledge bundles

This document defines:

1. how agent skills are stored
2. how skill versions are tracked
3. how skill sources are merged
4. how tasks and steps select skills
5. how MoonMind resolves skills into immutable run snapshots
6. how runtimes receive the resolved skill set
7. how `.agents/skills` and `.agents/skills/local` are used in workspaces

This document does **not** redefine:

1. executable MoonMind tool contracts
2. plan DAG semantics
3. generic artifact storage behavior
4. provider-profile and auth semantics
5. runtime-specific launch internals beyond the skill materialization boundary

Those concerns are covered by the related docs linked above.

---

## 2. Summary

MoonMind must treat agent skills as a **first-class control-plane data system**, not as an incidental side effect of source-controlled markdown files.

The canonical design is:

1. MoonMind supports **built-in**, **deployment-stored**, **repo-checked-in**, and **local-only** agent skills.
2. The authoritative skill system is **deployment-backed and versioned**.
3. Repo folders such as `.agents/skills` and `.agents/skills/local` are valid inputs and workspace conventions, but they are **not the only source of truth**.
4. At run start, MoonMind resolves all applicable skill sources into an immutable **ResolvedSkillSet** snapshot.
5. Workflows and activities pass **refs** to that resolved snapshot, not large inline skill content.
6. Runtime adapters materialize that snapshot for the target runtime.
7. `.agents/skills` is the canonical workspace-facing path for the runtime-visible active skill set.
8. `.agents/skills/local` is the local-only overlay area for non-version-controlled skills and mirrors.

This gives MoonMind:

- consistent skill behavior across runtimes
- reproducible executions
- better auditability
- lower workflow-history pressure
- support for both deployment-managed and repo-local skill authoring

---

## 3. Terminology

## 3.1 Canonical distinction

MoonMind currently uses the word **skill** in more than one sense. This document locks the target-state distinction.

### ToolDefinition
An executable MoonMind capability.

Examples:

- `repo.apply_patch`
- `sandbox.run_tests`
- `plan.generate`

A `ToolDefinition` is part of the execution contract described in `docs/Tasks/SkillAndPlanContracts.md`. A tool is executed through a Temporal activity or child workflow boundary.

### AgentSkillDefinition
A reusable **instruction bundle** for an agent.

Examples:

- "MoonMind docs writer"
- "Repo coding conventions"
- "Unreal C++ review checklist"
- "PR reviewer"
- "Python test fixer"

An `AgentSkillDefinition` is **not** itself an executable Temporal tool.

### SkillSet
A named collection of agent skills or selection rules.

Examples:

- `deployment-default`
- `python-repo`
- `docs-writer`
- `unreal-cpp`

### ResolvedSkillSet
The immutable, exact set of agent skills selected for a specific run or step after all precedence and override rules have been applied.

### RuntimeCommandSelection

A `RuntimeCommandSelection` is a MoonMind-native request to invoke a
runtime-provided command inside an `agent_runtime` step.

Examples include:

- `review`

A runtime command is not:

- an `AgentSkillDefinition`
- a `SkillSet`
- a `ResolvedSkillSet` entry
- a `.agents/skills` bundle

UI surfaces may co-present runtime commands beside agent skills for convenience,
but the backend contract must keep them typed and separate. Runtime commands
belong to the agent-runtime execution contract, not to the Agent Skill System.

### Materialization
The runtime-facing rendering of a `ResolvedSkillSet` into one or more of:

1. workspace files
2. prompt-index content
3. retrieval manifests
4. compatibility links or runtime-specific views

## 3.2 Naming rule

The canonical rule is:

1. executable MoonMind "skills" belong to the **tool** system
2. agent instruction bundles belong to the **agent skill** system

MoonMind may retain historical compatibility names in code where needed, but new docs and new contracts should be explicit about which system is meant.

---

## 4. Goals and non-goals

## 4.1 Goals

The Agent Skill System must:

1. support deployment-scoped skill storage as MoonMind data
2. support built-in base skills shipped with MoonMind
3. support repo-checked-in and local-only repo skills as optional inputs
4. allow tasks and steps to select skill sets in a runtime-independent way
5. resolve all selected skills into an immutable snapshot before runtime launch
6. keep large skill bodies out of workflow history
7. allow runtime adapters to materialize skills differently while preserving one canonical resolved source
8. make skill usage observable in Mission Control
9. preserve reproducibility across retries, reruns, and audits
10. avoid mutating checked-in skill content in place during run execution

## 4.2 Non-goals

The Agent Skill System does not aim to:

1. make `.agents/skills` the sole authoritative storage location
2. require every runtime to consume skills in the same format
3. place full skill bodies in workflow payloads
4. conflate agent skills with executable tool contracts
5. allow silent, unaudited skill drift within a run
6. force a repo to adopt any specific checked-in skill layout beyond the documented conventions

Additional non-goal: modeling runtime-native commands as agent skills or as
members of a `ResolvedSkillSet`.

---

## 5. Core invariants

The following rules are fixed.

1. Agent skills are **data**, not executable tool contracts.
2. The deployment-backed skill catalog is the authoritative source of truth for managed skill storage.
3. `.agents/skills` is the canonical workspace-facing path for the runtime-visible active skill set.
4. `.agents/skills/local` is the local-only overlay path for non-version-controlled skills and mirrors.
5. Repo directories may contribute skills, but they do not replace the deployment-backed skill system.
6. Every run or step uses an immutable `ResolvedSkillSet`.
7. Workflows carry refs to skill snapshots, not large inline skill content.
8. Runtime adapters own the final materialization format for a given runtime.
9. MoonMind must not mutate checked-in user-authored skill files in place during run materialization.
10. Reruns must reuse the original resolved snapshot by default unless a caller explicitly requests re-resolution.
11. Skill bodies must never be treated as a safe place for secrets.
12. Local-only skills must not silently bypass deployment policy.

---

## 6. Skill sources

MoonMind supports multiple sources of agent skills.

## 6.1 Built-in base skills

MoonMind may ship a base catalog of reusable agent skills in the MoonMind source tree.

These are intended for:

- common workflows
- baseline project guidance
- built-in task patterns
- starter skills for fresh deployments

Built-in skills are versioned with MoonMind releases and may be imported into the deployment-visible catalog or treated as a read-only source during resolution.

## 6.2 Deployment-stored skills

Deployment-stored skills are the primary managed source of truth.

These are persisted by MoonMind in its own data systems and are intended for:

- organization-wide shared guidance
- deployment-specific conventions
- operator-managed reusable skills
- central review and governance

This is the canonical storage model for skills that MoonMind itself owns.

## 6.3 Repo checked-in skills

A target repository may define checked-in agent skills under the documented repo conventions.

These skills are useful for:

- repo-specific contribution guidance
- project-local operating instructions
- stable conventions intended to travel with the codebase

Repo checked-in skills are valid resolution inputs, but they are not the only source of truth.

## 6.4 Repo local-only skills

A target repository may define local-only agent skills under `.agents/skills/local`.

These are useful for:

- non-version-controlled local instructions
- developer-specific overlays
- local experimentation
- migration from older local-only skill flows

These skills are intentionally excluded from version control by default.

## 6.5 Explicit task or step overrides

A task or step may explicitly include, exclude, or pin skills or skill sets.

These selectors are part of execution intent and participate in resolution before runtime launch.

---

## 7. Source precedence and merge rules

## 7.1 Canonical precedence order

When multiple sources provide candidate skills, MoonMind resolves them in this order:

1. built-in base skills
2. deployment-stored skills
3. repo checked-in skills
4. repo local-only skills
5. explicit task or step overrides

Later layers may override earlier ones according to the collision rules below.

## 7.2 Collision rules

When more than one source provides a skill with the same canonical name:

1. a later-precedence source may override an earlier source by name
2. the resolved snapshot must record the winning source and version
3. if two candidates at the same precedence level conflict and no deterministic tie-breaker exists, resolution must fail
4. if a selector pins an explicit version that cannot be satisfied, resolution must fail
5. if policy forbids a source kind for the current run, candidates from that source are excluded before precedence is applied

## 7.3 Policy gates

Deployments may define policy that restricts:

1. whether repo skills are allowed
2. whether local-only skills are allowed
3. which built-in or deployment skills are eligible
4. whether step-level overrides are permitted
5. whether local-only skills may shadow deployment-managed skills

Policy is enforced during resolution, not after runtime launch.

---

## 8. Workspace path policy

## 8.1 Canonical path

`.agents/skills` is the canonical workspace-facing path for the active skill set visible to the runtime.

This path is the stable interface MoonMind should present to managed runtimes and to workspace-aware tooling.

## 8.2 Local-only path

`.agents/skills/local` is the canonical local-only overlay path.

This path is intended for:

- local-only developer skills
- non-version-controlled mirrors
- opt-in repo-local overlay behavior

It is not the authoritative durable source of truth for MoonMind-managed skill storage.

## 8.3 Active-set rule

The runtime-visible skill set is not a direct mutable merge into author-authored checked-in folders.

Instead, MoonMind resolves an immutable active skill snapshot for the run and exposes that snapshot through the canonical workspace-facing path.

The target-state behavior is:

1. MoonMind resolves the active skill set
2. MoonMind materializes it into an internal active directory or equivalent run-scoped location
3. `.agents/skills` points at or exposes that active set
4. optional runtime-specific compatibility links may also be created if needed

## 8.4 Mutation rule

MoonMind must not rewrite user-authored checked-in skill files in place as part of normal run materialization.

Checked-in skill folders are inputs to resolution, not mutable runtime state.

---

## 9. Data model

This section defines the canonical conceptual contracts. Concrete storage schemas may vary, but the logical model must remain aligned with these shapes.

## 9.1 AgentSkillDefinition

Represents the stable identity of an agent skill.

Suggested fields:

- `skill_id`
- `name`
- `title`
- `description`
- `source_kind`
- `visibility`
- `enabled`
- `default_supported_runtimes`
- `created_at`
- `updated_at`

## 9.2 AgentSkillVersion

Represents an immutable version of an agent skill body.

Suggested fields:

- `skill_id`
- `version`
- `content_ref`
- `format`
- `checksum`
- `supported_runtimes`
- `metadata`
- `created_at`
- `created_by`

Rules:

1. versions are immutable
2. editing a skill creates a new version
3. large bodies live in artifact/blob storage, not in workflow history

## 9.3 SkillSet

Represents a named collection or selection surface for agent skills.

Suggested fields:

- `skill_set_id`
- `name`
- `scope`
- `description`
- `enabled`
- `created_at`
- `updated_at`

## 9.4 SkillSetEntry

Represents one selection entry inside a `SkillSet`.

Suggested fields:

- `skill_set_id`
- `selector_type`
- `skill_name`
- `version`
- `include`
- `order`
- `conditions`

A `SkillSet` may contain exact pinned entries, version selectors, or future-compatible conditions.

## 9.5 ResolvedSkillSet

Represents the immutable skill snapshot used by a specific run or step.

Suggested fields:

- `snapshot_id`
- `deployment_id`
- `resolved_at`
- `skills[]`
- `manifest_ref`
- `source_trace`
- `resolution_inputs`
- `policy_summary`

## 9.6 RuntimeSkillMaterialization

Represents the runtime-facing rendering of a resolved skill snapshot.

Suggested fields:

- `runtime_id`
- `materialization_mode`
- `workspace_paths`
- `prompt_index_ref`
- `retrieval_manifest_ref`
- `metadata`

---

## 10. Versioning and immutability

## 10.1 Skill version immutability

Agent skill bodies are immutable by version.

Rules:

1. changing content creates a new version
2. prior versions remain available for audit and replay
3. content hashes and refs must remain stable

## 10.2 Snapshot immutability

Each `ResolvedSkillSet` is immutable.

Rules:

1. a run or step pins exact versions
2. retries within the same execution use the same snapshot
3. reruns reuse the same snapshot by default
4. a caller may explicitly request re-resolution, but that is a different action from a normal rerun

## 10.3 Artifact discipline

Large skill bodies, manifests, and materialization bundles must live in artifacts or equivalent blob storage.

Workflows carry only compact refs and metadata.

---

## 11. Selection model for tasks and steps

## 11.1 Canonical intent

Tasks and steps must be able to select agent skills without hard-coding runtime-specific behavior.

The canonical direction is:

- `task.skills`
- `step.skills`

These selectors are part of execution intent and are resolved before runtime launch.

## 11.2 Conceptual task shape

A task may express skill intent like this:

```json
{
  "task": {
    "skills": {
      "sets": ["deployment-default", "python-repo"],
      "include": [
        { "name": "moonmind-doc-writer", "version": "2.3.0" }
      ],
      "exclude": ["legacy-reviewer"],
      "materializationMode": "hybrid"
    }
  }
}
````

This shape is illustrative. The canonical API contract may define additional fields or tighten validation elsewhere.

## 11.3 Inheritance rules

The default inheritance model is:

1. `task.skills` defines the task-wide baseline
2. `step.skills` augments or overrides the task baseline for that step
3. a step may exclude inherited skills
4. absent `step.skills` means the step inherits the task baseline unchanged

## 11.4 Selection semantics

The selection system must support:

1. named skill sets
2. direct include lists
3. exclude lists
4. exact version pins
5. optional materialization preferences
6. explicit empty selection where policy allows it

---

## 12. Resolution lifecycle

## 12.1 Resolution timing

Skill resolution happens before runtime launch, at the activity boundary.

Workflows must not resolve large skill bodies inline in workflow code.

## 12.2 Resolution algorithm

The canonical resolution flow is:

1. collect selectors from the task and current step
2. identify the allowed source kinds under current policy
3. load candidate skills from built-in, deployment, repo checked-in, and local-only sources
4. apply precedence and collision rules
5. pin exact skill versions
6. write a resolved manifest artifact
7. return a compact `ResolvedSkillSet` reference for downstream execution

## 12.3 Provenance recording

The resolved snapshot must record enough provenance to answer:

1. which skills were selected
2. which versions won
3. which source each skill came from
4. which selectors and policies influenced the result

## 12.4 Failure behavior

Resolution must fail before runtime launch when:

1. a required skill is missing
2. a pinned version cannot be resolved
3. collision rules cannot produce a deterministic winner
4. policy forbids the requested source or override
5. a required skill is incompatible with the target runtime and no allowed fallback exists

---

## 13. Runtime materialization modes

## 13.1 Supported modes

The system supports the following conceptual materialization modes:

### `prompt_bundled`

The skill content or compact summaries are injected directly into the runtime prompt or initial instruction bundle.

Best suited for:

* smaller skill sets
* black-box external agents
* runtimes without strong workspace skill conventions

### `workspace_mounted`

The full resolved skill set is materialized into workspace files visible to the runtime.

Best suited for:

* managed coding runtimes
* larger skill sets
* file-aware agents

### `hybrid`

A compact prompt index is supplied, while full content is materialized into workspace files.

This is the recommended default for managed runtimes.

### `retrieval`

The runtime receives a manifest or helper path that allows on-demand retrieval of skill content.

This is a forward-compatible mode for larger catalogs or more advanced adapters.

## 13.2 Recommended defaults

The default posture is:

1. managed runtimes use `hybrid`
2. external black-box agents use `prompt_bundled` unless the provider supports a stronger bundle or retrieval path
3. adapter-specific behavior may refine delivery, but not the upstream resolved snapshot

---

## 14. Managed runtime workspace behavior

## 14.1 Canonical visible path

For managed runtimes, the active resolved skill set must be visible through `.agents/skills`.

This is the stable path MoonMind should rely on across managed runtime integrations.

## 14.2 Local overlay availability

`.agents/skills/local` remains the local-only overlay and input area.

Local-only skills may contribute to resolution if policy allows them, but the runtime-visible active set remains the resolved snapshot.

## 14.3 Active manifest

Managed runtimes should receive a compact active manifest or index that indicates:

1. which skills are active
2. where they are available
3. which skill set names or sources contributed to the resolved snapshot

## 14.4 Compatibility links

Runtime adapters may create additional compatibility links or mirrors for a specific runtime, but `.agents/skills` remains the canonical workspace-facing path.

---

## 15. Temporal boundaries

## 15.1 Workflow responsibilities

Workflow code remains deterministic and orchestration-only.

Workflow responsibilities include:

1. carrying selection intent
2. invoking activities to resolve and materialize skill snapshots
3. passing refs into runtime execution
4. recording snapshot metadata in execution state, outputs, or artifacts as appropriate

## 15.2 Activity responsibilities

Activities may perform the nondeterministic work required to:

1. load candidate skill sources
2. resolve precedence and policy
3. write the resolved manifest
4. materialize runtime-facing files
5. build prompt indexes or retrieval manifests

## 15.3 Payload discipline

Workflow payloads and history must stay small.

Do not put these directly into workflow history:

* full skill bodies
* large rendered bundles
* workspace copies of skill content
* large source traces

Use artifact refs and compact metadata only.

---

## 16. Interaction with `MoonMind.AgentRun`

The `MoonMind.AgentRun` system must treat agent skills as an input contract distinct from provider auth, runtime launch, or workspace hydration.

The canonical direction is:

1. the parent workflow resolves the applicable skill set
2. the child agent-run path receives a compact ref such as `resolved_skillset_ref`
3. the runtime adapter materializes that snapshot for the target runtime
4. the adapter may also generate a compact prompt index or summary
5. the underlying runtime sees a stable active skill view through `.agents/skills`

This keeps skill resolution centralized while allowing runtime-specific delivery.

---

## 17. Security and trust model

## 17.1 Untrusted content rule

Repo-provided and local-only skill content must be treated as potentially untrusted input.

MoonMind must not assume that repo skill content is safe simply because it lives in the workspace.

## 17.2 Secret handling rule

Agent skill bodies must not be used as a place to store secrets.

MoonMind must not:

1. encourage secret storage in skill content
2. log full skill bodies by default
3. materialize secrets into skill manifests for convenience

## 17.3 Policy enforcement

Deployments may restrict:

1. whether repo checked-in skills are allowed
2. whether local-only skills are allowed
3. whether deployment-managed skills are mandatory
4. whether task or step overrides are allowed
5. whether specific skills or skill sets are permitted for certain runtimes

## 17.4 Auditability

The system must preserve enough data to answer:

1. which skills were used
2. where they came from
3. which versions were selected
4. what the active manifest looked like
5. how the runtime received them

---

## 18. Observability and Mission Control

Mission Control should surface the skill system as a first-class part of execution context.

## 18.1 Submit-time visibility

Where applicable, operators should be able to see or select:

1. named skill sets
2. explicit includes and excludes
3. the intended materialization mode
4. whether repo and local overlays are enabled for the run

## 18.2 Detail-page visibility

Task detail should be able to show:

1. the resolved skill snapshot ID
2. the selected skill versions
3. the source provenance for each resolved skill
4. the materialization mode
5. the canonical runtime-visible path summary
6. artifact links for the resolved manifest or prompt index when appropriate

## 18.3 Debug surfaces

Advanced or debug views may additionally expose:

1. raw `resolved_skillset_ref`
2. raw manifest refs
3. source-trace details
4. adapter materialization metadata

---

## 19. Proposal, schedule, rerun, and replay semantics

## 19.1 Proposal semantics

When MoonMind creates a proposal that depends on skills, the proposal system should either:

1. persist explicit `task.skills` selectors, or
2. explicitly document that the proposal will inherit deployment defaults at promotion time

The preferred direction is to preserve explicit skill intent where it materially affects execution behavior.

## 19.2 Scheduled execution semantics

For scheduled work, the system must preserve skill intent clearly.

The recommended direction is:

1. store the task's skill selectors at schedule creation time
2. resolve the actual `ResolvedSkillSet` when the scheduled run starts
3. preserve enough metadata to explain how the scheduled execution's skill snapshot was chosen

If exact version pinning at schedule creation becomes necessary for certain workloads, that should be an explicit mode rather than silent default behavior.

## 19.3 Rerun semantics

The default rerun rule is:

1. reuse the original `ResolvedSkillSet`
2. do not silently re-resolve "latest" skills
3. require explicit operator intent for re-resolution

## 19.4 Continue-as-new and replay safety

Continue-as-new, retries, and other workflow continuation mechanics must preserve the same resolved snapshot unless an explicit new-resolution action is taken.

---

## 20. Current implementation snapshot

This document describes the canonical target-state design. The current implementation is only partially aligned.

## 20.1 Already aligned

1. MoonMind already distinguishes, at least in documentation, between executable tool contracts and agent instruction bundles.
2. `.agents/skills` and `.agents/skills/local` already exist as real workspace conventions.
3. MoonMind already prefers artifact-backed payload discipline for large execution context.

## 20.2 Partially aligned

1. shared skill materialization behavior exists in some runtime guidance and workspace conventions
2. local-only mirrors and adapter-visible skill paths already exist in some form
3. the runtime side of skill exposure is more advanced than the control-plane data model

## 20.3 Still missing

1. a fully modeled deployment-backed skill catalog
2. explicit `AgentSkillDefinition` and `ResolvedSkillSet` contracts
3. canonical task and step selectors for agent skills
4. full Mission Control support for selection and visibility
5. end-to-end audit and provenance surfaces
6. explicit policy enforcement across built-in, deployment, repo, and local sources

---

## 21. Document boundaries

Use this document for:

* the conceptual design of agent skills as deployment-scoped data
* source precedence
* snapshot resolution
* workspace path policy
* runtime materialization boundaries

Use related docs for:

* executable tool contracts: `docs/Tasks/SkillAndPlanContracts.md`
* control-plane task submission and task surfaces: `docs/Tasks/TaskArchitecture.md`
* managed and external runtime execution: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
* Temporal orchestration boundaries: `docs/Temporal/TemporalArchitecture.md`
* Mission Control surfaces: `docs/UI/MissionControlArchitecture.md`
* implementation phases and backlog: `docs/tmp/AgentSkillSystemPlan.md`

---

## 22. Locked decisions

This document locks the following design decisions.

1. Agent skills are distinct from executable MoonMind tools.
2. The authoritative managed storage model is deployment-backed.
3. `.agents/skills` is the canonical workspace-facing path for the active skill set.
4. `.agents/skills/local` is the local-only overlay path.
5. Repo skill folders are valid inputs, not the only source of truth.
6. Each run or step uses an immutable `ResolvedSkillSet`.
7. Runtime adapters own the final materialization format.
8. Workflows carry refs, not large skill bodies.
9. Reruns reuse the original resolved snapshot by default.
10. MoonMind must not mutate checked-in skill content in place during run materialization.

---

## 23. Summary

MoonMind should treat agent skills as a first-class data system with the same architectural discipline it applies to plans, artifacts, and workflow execution.

The canonical model is:

1. store skills as versioned data
2. allow repo and local overlays as inputs
3. resolve all applicable sources into an immutable active snapshot
4. materialize that snapshot for the target runtime
5. expose the active result through `.agents/skills`
6. keep `.agents/skills/local` as the local-only overlay path
7. preserve provenance, auditability, and replay safety

This approach gives MoonMind a consistent, deployment-aware skill system that works across managed and external runtimes without overloading repo folders or workflow payloads as the primary storage model.
