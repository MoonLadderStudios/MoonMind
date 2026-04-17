This version merges the current canonical agent-skill doc, the proposed runtime-delivery rules from `SkillInjection.md`, and the repo guidance in `AGENTS.md` into one canonical spec, while keeping the explicit boundary between executable tool contracts and agent instruction bundles. ([GitHub][1])

# Skill System

Status: Active
Owners: MoonMind Engineering / MoonMind Platform
Last Updated: 2026-04-17
Supersedes: `docs/Tasks/AgentSkillSystem.md`, `docs/Tools/SkillInjection.md`
Related: `docs/Tasks/SkillAndPlanContracts.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Temporal/TemporalArchitecture.md`, `docs/UI/MissionControlArchitecture.md`, `AGENTS.md`
Implementation tracking: `docs/tmp/AgentSkillSystemPlan.md`

---

## 1. Purpose

This document defines MoonMind’s canonical design for the **Skill System**.

MoonMind currently uses the word **skill** in more than one sense. This document is the single canonical place that:

1. locks the terminology boundary between executable tool “skills” and agent instruction-bundle skills
2. defines the canonical storage, selection, resolution, and versioning model for agent instruction-bundle skills
3. defines the canonical runtime materialization and injection model for managed runtimes
4. defines how `.agents/skills` and `.agents/skills/local` are used in workspaces
5. defines the invariants that all adapters, workflows, activities, and operator-facing surfaces must preserve

This document is intentionally broader than the previous `AgentSkillSystem.md`. It is the canonical doc for **skill-related architectural concerns as a whole**, while still preserving a strict boundary between:

1. executable MoonMind tool contracts
2. agent instruction bundles
3. runtime-native commands

This document does **not** redefine:

1. executable tool contract schemas or execution semantics
2. plan DAG semantics
3. generic artifact storage behavior
4. provider-profile and auth semantics
5. runtime launch internals beyond the skill materialization and injection boundary

Those concerns remain in the related docs listed above.

---

## 2. Summary

MoonMind must treat skills as a first-class architectural concern, but not as a single undifferentiated thing.

The canonical model is:

1. MoonMind distinguishes between **executable tool contracts** and **agent instruction-bundle skills**.
2. `docs/Tasks/SkillAndPlanContracts.md` remains canonical for executable tool contracts.
3. This document is canonical for the overall Skill System boundary and for the full lifecycle of agent instruction-bundle skills.
4. Agent instruction-bundle skills are deployment-scoped data, not incidental markdown files.
5. MoonMind supports **built-in**, **deployment-stored**, **repo-checked-in**, and **workspace-local overlay** agent skills, plus explicit task or step overrides.
6. At run start, MoonMind resolves all applicable sources into an immutable **ResolvedSkillSet** snapshot.
7. Workflows and activities carry refs to that snapshot, not large inline skill bodies.
8. Runtime adapters materialize that snapshot for the target runtime.
9. For managed runtimes, MoonMind uses one standard injection shape:

   1. full active skill bundle materialized into a run-scoped MoonMind-owned backing store
   2. compact activation summary included inline in runtime instructions
   3. active bundle projected to the runtime at `.agents/skills`
10. `.agents/skills` is the canonical runtime-visible path for the active resolved skill set.
11. `.agents/skills/local` is the canonical workspace-local overlay and input path.
12. Runtimes must not re-resolve or rediscover skills during execution.

This gives MoonMind:

* clear terminology boundaries
* consistent runtime behavior across adapters
* reproducible executions
* better auditability
* lower workflow-history pressure
* support for deployment-managed skills, repo-checked-in inputs, and workspace-local overlays without making repo folders the primary source of truth

---

## 3. Terminology

## 3.1 Canonical distinction

MoonMind uses the word **skill** in more than one sense. This document locks the distinction.

### ToolDefinition

An executable MoonMind capability.

Examples:

* `repo.apply_patch`
* `sandbox.run_tests`
* `plan.generate`

A `ToolDefinition` is part of the execution contract described in `docs/Tasks/SkillAndPlanContracts.md`. A tool is executed through a Temporal activity, child workflow, or equivalent execution boundary.

### AgentSkillDefinition

A reusable **instruction bundle** for an agent.

Examples:

* “MoonMind docs writer”
* “Repo coding conventions”
* “Unreal C++ review checklist”
* “PR reviewer”
* “Python test fixer”

An `AgentSkillDefinition` is **not** itself an executable Temporal tool.

### SkillSet

A named collection of agent skills or selection rules.

Examples:

* `deployment-default`
* `python-repo`
* `docs-writer`
* `unreal-cpp`

### ResolvedSkillSet

The immutable, exact set of agent skills selected for a specific run or step after all precedence, policy, and override rules have been applied.

### RuntimeCommandSelection

A `RuntimeCommandSelection` is a MoonMind-native request to invoke a runtime-provided command inside an `agent_runtime` step.

Examples include:

* `review`

A runtime command is not:

* an `AgentSkillDefinition`
* a `SkillSet`
* a `ResolvedSkillSet` entry
* a `.agents/skills` bundle

UI surfaces may co-present runtime commands beside agent skills for convenience, but backend contracts must keep them typed and separate.

### Materialization

The runtime-facing rendering of a `ResolvedSkillSet` into one or more of:

1. workspace files
2. prompt-index content
3. retrieval manifests
4. compatibility links
5. runtime-specific views

### Injection

The final runtime delivery step that makes the materialized active skill set visible and usable to the runtime.

For managed runtimes, injection includes:

1. projection of the active skill set to `.agents/skills`
2. compact inline activation summary
3. any adapter-specific compatibility links or views

### Projection

The adapter-owned mechanism used to expose the active backing store to the runtime-visible path.

Examples include:

* symlink
* bind mount
* overlay view
* staged copy-on-write view

The mechanism is not the contract. The visible behavior is the contract.

## 3.2 Naming rule

The canonical rule is:

1. executable MoonMind “skills” belong to the **tool** system
2. agent instruction bundles belong to the **agent skill** system
3. runtime-native commands belong to the **agent runtime execution** system

When this document uses the phrase **Skill System**, it means the overall architectural boundary and interaction between these concepts. Unless explicitly stated otherwise, the storage, selection, resolution, and materialization rules in this document apply to **agent skills**, not to executable tool contracts.

MoonMind may retain historical compatibility names in code where needed, but new docs and new contracts must be explicit about which meaning of “skill” is intended.

---

## 4. Goals and non-goals

## 4.1 Goals

The Skill System must:

1. define one canonical terminology boundary for all MoonMind “skill” concepts
2. support deployment-scoped skill storage as MoonMind data
3. support built-in base skills shipped with MoonMind
4. support repo-checked-in skills and workspace-local overlay skills as optional inputs
5. allow tasks and steps to select skill sets in a runtime-independent way
6. resolve all selected agent skills into an immutable snapshot before runtime launch
7. keep large skill bodies out of workflow history
8. allow runtime adapters to materialize and inject skills differently while preserving one canonical resolved source
9. make skill usage observable in Mission Control
10. preserve reproducibility across retries, reruns, and audits
11. avoid mutating checked-in skill content in place during run execution
12. standardize one predictable managed-runtime injection model
13. keep runtime-visible active paths predictable and easy for agents to inspect
14. reduce prompt bloat by keeping full skill bodies out of inline instruction payloads

## 4.2 Non-goals

The Skill System does not aim to:

1. make `.agents/skills` the sole authoritative storage location
2. require every runtime to consume skills in the same format
3. place full skill bodies in workflow payloads
4. conflate agent skills with executable tool contracts
5. model runtime-native commands as agent skills or `ResolvedSkillSet` members
6. allow silent, unaudited skill drift within a run
7. require the repo to permanently contain MoonMind-managed runtime state
8. support multiple first-class managed-runtime injection shapes as the default posture
9. make retrieval- or MCP-based skill loading part of the normal managed-runtime path
10. make per-skill leaf mounting the canonical managed-runtime design

---

## 5. Core invariants

The following rules are fixed.

1. Agent skills are **data**, not executable tool contracts.
2. The deployment-backed skill catalog is the authoritative source of truth for managed skill storage.
3. `.agents/skills` is the canonical workspace-facing path for the runtime-visible active skill set.
4. `.agents/skills/local` is the canonical workspace-local overlay path for non-version-controlled skills and mirrors.
5. Repo directories may contribute skills, but they do not replace the deployment-backed skill system.
6. Every run or step uses an immutable `ResolvedSkillSet`.
7. Workflows carry refs to skill snapshots, not large inline skill content.
8. Runtime adapters own the final materialization format for a given runtime, but must preserve the canonical visible behavior.
9. MoonMind must not mutate checked-in user-authored skill files in place during run materialization.
10. Reruns must reuse the original resolved snapshot by default unless a caller explicitly requests re-resolution.
11. Skill bodies must never be treated as a safe place for secrets.
12. Local-only skills must not silently bypass deployment policy.
13. Managed runtimes use one standard injection shape: **full active bundle on disk + compact activation summary inline + canonical projection at `.agents/skills`**.
14. The runtime-visible active tree contains only the selected active skills for the current run or step.
15. Unselected skills must not appear in the runtime-visible active tree.
16. Runtimes must not re-resolve or rediscover skills during execution.
17. Projection collisions or incompatible path states must fail before runtime launch.
18. The active backing store must be run-scoped and MoonMind-owned.
19. The runtime should see the full active skill root at `.agents/skills`, even when only one skill is selected.
20. Compatibility links may exist, but `.agents/skills` remains canonical.

---

## 6. Skill sources

MoonMind supports multiple sources of agent skills.

## 6.1 Built-in base skills

MoonMind may ship a base catalog of reusable agent skills in the MoonMind source tree.

These are intended for:

* common workflows
* baseline project guidance
* built-in task patterns
* starter skills for fresh deployments

Built-in skills are versioned with MoonMind releases and may be imported into the deployment-visible catalog or treated as a read-only source during resolution.

## 6.2 Deployment-stored skills

Deployment-stored skills are the primary managed source of truth.

These are persisted by MoonMind in its own data systems and are intended for:

* organization-wide shared guidance
* deployment-specific conventions
* operator-managed reusable skills
* central review and governance

This is the canonical storage model for skills that MoonMind itself owns.

## 6.3 Repo checked-in skills

A target repository may define checked-in agent skills under the documented repo conventions.

These skills are useful for:

* repo-specific contribution guidance
* project-local operating instructions
* stable conventions intended to travel with the codebase

Repo checked-in skills are valid resolution inputs, but they are not the only source of truth.

## 6.4 Workspace-local overlay skills

Workspace-local overlay skills are non-version-controlled skill inputs available in the current workspace or runtime environment.

They are not checked into the target repository and are not part of the authoritative deployment-backed skill catalog.

These are useful for:

* local experimentation
* operator-specific overlays
* migration from older workspace-local flows
* environment-scoped additions where policy allows them

These skills are intentionally excluded from version control by default. In managed-session environments, they are owned by the deployment, worker environment, or ephemeral workspace around the repo rather than by the target repo itself.

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
4. workspace-local overlay skills
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
2. whether workspace-local overlay skills are allowed
3. which built-in or deployment skills are eligible
4. whether step-level overrides are permitted
5. whether workspace-local overlay skills may shadow deployment-managed skills
6. whether deployment-managed skills are mandatory for certain runs
7. whether specific skills or skill sets are permitted for certain runtimes

Policy is enforced during resolution, not after runtime launch.

---

## 8. Workspace and runtime path policy

## 8.1 Canonical runtime-visible path

`.agents/skills` is the canonical workspace-facing path for the active skill set visible to the runtime.

This path is the stable interface MoonMind should present to managed runtimes and to workspace-aware tooling.

## 8.2 Workspace-local overlay path

`.agents/skills/local` is the canonical workspace-local overlay path.

This path is intended for:

* non-version-controlled workspace-local overlay skills
* non-version-controlled mirrors
* opt-in environment-scoped overlay behavior

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

## 8.5 Projection ownership rule

For the duration of a run, MoonMind owns the runtime-visible active projection at `.agents/skills`.

That ownership applies to the active projection boundary, not to the authorship of source inputs.

---

## 9. Data model

This section defines the canonical conceptual contracts. Concrete storage schemas may vary, but the logical model must remain aligned with these shapes.

## 9.1 AgentSkillDefinition

Represents the stable identity of an agent skill.

Suggested fields:

* `skill_id`
* `name`
* `title`
* `description`
* `source_kind`
* `visibility`
* `enabled`
* `default_supported_runtimes`
* `created_at`
* `updated_at`

## 9.2 AgentSkillVersion

Represents an immutable version of an agent skill body.

Suggested fields:

* `skill_id`
* `version`
* `content_ref`
* `format`
* `checksum`
* `supported_runtimes`
* `metadata`
* `created_at`
* `created_by`

Rules:

1. versions are immutable
2. editing a skill creates a new version
3. large bodies live in artifact/blob storage, not in workflow history

## 9.3 SkillSet

Represents a named collection or selection surface for agent skills.

Suggested fields:

* `skill_set_id`
* `name`
* `scope`
* `description`
* `enabled`
* `created_at`
* `updated_at`

## 9.4 SkillSetEntry

Represents one selection entry inside a `SkillSet`.

Suggested fields:

* `skill_set_id`
* `selector_type`
* `skill_name`
* `version`
* `include`
* `order`
* `conditions`

A `SkillSet` may contain exact pinned entries, version selectors, or future-compatible conditions.

## 9.5 ResolvedSkillSet

Represents the immutable skill snapshot used by a specific run or step.

Suggested fields:

* `snapshot_id`
* `deployment_id`
* `resolved_at`
* `skills[]`
* `manifest_ref`
* `source_trace`
* `resolution_inputs`
* `policy_summary`

## 9.6 RuntimeSkillMaterialization

Represents the runtime-facing rendering of a resolved skill snapshot.

Suggested fields:

* `runtime_id`
* `materialization_mode`
* `workspace_paths`
* `visible_path`
* `backing_path`
* `read_only`
* `prompt_index_ref`
* `retrieval_manifest_ref`
* `compatibility_paths`
* `activation_summary_ref`
* `metadata`

## 9.7 ActiveSkillManifest

Represents the runtime-visible index or manifest that describes the active projected skill set.

Suggested fields:

* `snapshot_id`
* `resolved_at`
* `visible_path`
* `backing_path`
* `read_only`
* `skills[]`
* `source_summary`
* `materialization_mode`
* `compatibility_paths`
* `metadata`

The canonical data contract matters more than the exact filename. Adapters may use a well-known manifest or index file so long as the runtime-visible projection remains discoverable and aligned with the resolved snapshot.

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

* `task.skills`
* `step.skills`

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
```

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
3. load candidate skills from built-in, deployment, repo checked-in, and workspace-local overlay sources
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

A compact prompt index or activation summary is supplied, while full content is materialized into workspace files.

This is the recommended default for managed runtimes.

### `retrieval`

The runtime receives a manifest or helper path that allows on-demand retrieval of skill content.

This is a forward-compatible mode for larger catalogs or more advanced adapters.

## 13.2 Recommended defaults

The default posture is:

1. managed runtimes use `hybrid`
2. external black-box agents use `prompt_bundled` unless the provider supports a stronger bundle or retrieval path
3. adapter-specific behavior may refine delivery, but not the upstream resolved snapshot

## 13.3 Managed-runtime standardization rule

Although the overall system supports multiple conceptual materialization modes, managed runtimes standardize on **one canonical injection shape**:

1. resolve the exact active skill set
2. materialize the full active bundle into a run-scoped MoonMind-owned backing store
3. project that bundle to `.agents/skills`
4. include a compact inline activation summary in the runtime instructions

This means managed runtimes do **not** treat multiple visible-path or rediscovery strategies as first-class normal-path alternatives.

---

## 14. Managed runtime injection contract

## 14.1 Canonical sequence

For managed runtimes, MoonMind should always perform the following sequence before execution begins:

1. **Resolve**
   Resolve task and step skill intent into an immutable `ResolvedSkillSet`.

2. **Materialize**
   Materialize the resolved active skill set into a run-scoped internal directory.

3. **Project**
   Expose that internal active directory at `.agents/skills` for the runtime.

4. **Activate**
   Include a compact activation summary in the step instructions telling the agent:

   1. which skills are active
   2. where they are available
   3. any must-follow rules that should be applied immediately
   4. which skill to read first when that matters

## 14.2 Canonical mental model

MoonMind should distinguish between three different things:

### 14.2.1 Skill inputs

These are the possible sources used during resolution, such as:

* deployment-managed skills
* built-in skills
* repo-checked-in skills
* workspace-local overlay skills

### 14.2.2 Active backing store

This is the run-scoped, MoonMind-owned, immutable materialization of the resolved active skill snapshot.

Representative backing path:

```text
/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>/
```

### 14.2.3 Runtime-visible active path

This is the path the agent sees and should rely on:

```text
<workspace>/repo/.agents/skills
```

The runtime-visible path is a projection of the backing store, not the authoritative storage model.

## 14.3 Runtime-visible filesystem shape

The active skill projection should look like this to the runtime:

```text
.agents/
  skills/
    _manifest.json
    pr-resolver/
      SKILL.md
      examples/
      templates/
    safe-python-edit/
      SKILL.md
```

Notes:

1. `SKILL.md` remains the primary entrypoint for a skill.
2. The active tree contains only the selected skills for the current run or step.
3. Unselected skills must not appear in the runtime-visible active tree.
4. The active tree is MoonMind-owned runtime state, even when some selected skills originated from repo inputs.
5. The manifest filename shown above is illustrative of the visible shape. The canonical contract is the manifest content and discoverability, not a single filename until repo-wide standardization is complete.

## 14.4 Backing-store requirements

The backing-store rules are fixed:

1. it must be run-scoped
2. it must be owned by MoonMind rather than by repo authors
3. it must be read-only to the runtime where practical
4. it must correspond exactly to one resolved snapshot

## 14.5 Projection rules

MoonMind may expose the active backing store at `.agents/skills` through any adapter-compatible mechanism, including:

* bind mount
* symlink
* overlay view
* copy-on-write staging created before the runtime starts

The mechanism is not the contract.

The contract is:

1. the runtime sees the active skill set at `.agents/skills`
2. the content corresponds exactly to the pinned resolved snapshot
3. the runtime is not expected to discover skills anywhere else
4. adapters may create compatibility links if needed, but `.agents/skills` remains canonical

## 14.6 Inline activation summary

The step instructions should always include a small activation block.

### 14.6.1 Required contents

The block should contain:

1. the active skill names
2. the visible path `.agents/skills`
3. any hard constraints that must be obeyed immediately
4. optional hints about which skill to read first

### 14.6.2 Example

```text
Active MoonMind skills for this step:
- pr-resolver: analyze review feedback, group findings, and propose minimal fixes
- safe-python-edit: make minimal safe Python changes and preserve behavior

Full skill content is available under .agents/skills.
Read .agents/skills/pr-resolver/SKILL.md before preparing the fix plan.

Hard rules:
- do not broaden scope beyond the reported issue
- prefer the smallest change that resolves the root cause
```

### 14.6.3 Size rule

The inline activation block should remain compact. It is not a duplicate full-body skill bundle.

## 14.7 Single-skill runs

When only one skill is selected, the active projection should still expose the full root:

```text
.agents/skills/
  _manifest.json
  pr-resolver/
    SKILL.md
```

MoonMind should not change the architectural model just because only one skill is active.

## 14.8 Collision and projection failure policy

Collision handling must be explicit and fail-fast.

### 14.8.1 Canonical rule

MoonMind owns the runtime-visible active projection at `.agents/skills` for the duration of the run.

### 14.8.2 What counts as a collision

A collision exists when MoonMind cannot safely expose the resolved active skill root at `.agents/skills`.

Examples:

* `.agents` exists as a file instead of a directory
* `.agents/skills` exists as a file instead of a directory or replaceable mount point
* adapter or filesystem restrictions prevent the active projection from being installed

### 14.8.3 Preferred behavior

1. materialize the active set outside the repo
2. project it at `.agents/skills`
3. do not mutate checked-in skill sources in place
4. fail before runtime launch if projection cannot be installed safely

### 14.8.4 Error behavior

Projection failures must surface a clear pre-launch error describing:

* the conflicting path
* the conflicting object kind
* the attempted projection action
* the suggested operator remediation

## 14.9 Repo-provided skill folders

Repo-provided skill folders remain valid inputs to the resolution process.

They are not the canonical runtime state.

If the repo contains:

```text
.agents/skills/
```

that content may be considered during resolution if policy allows it, but the runtime-visible active state still comes from the resolved snapshot projection.

This means:

1. checked-in repo skills may influence selection
2. selected repo skills may appear in the active projection
3. the original checked-in directory must not be mutated in place during runtime setup

## 14.10 Adapter responsibilities

Runtime adapters are responsible for the final projection mechanics, but they must preserve the same logical behavior.

Each adapter must:

1. consume a pinned resolved snapshot ref
2. materialize the active backing store exactly once per snapshot
3. expose the active set at `.agents/skills`
4. include the activation summary in the runtime instructions
5. avoid re-resolving or broadening the selected skill set

Adapters must not:

1. scan the repo for additional skills during execution
2. add unselected skills to the visible tree
3. silently fall back to a different visible path
4. rewrite checked-in skill inputs in place

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
5. build prompt indexes, activation summaries, or retrieval manifests
6. install the runtime-visible projection

## 15.3 Payload discipline

Workflow payloads and history must stay small.

Do not put these directly into workflow history:

* full skill bodies
* large rendered bundles
* workspace copies of skill content
* large source traces
* large activation payloads

Use artifact refs and compact metadata only.

---

## 16. Interaction with `MoonMind.AgentRun`

The `MoonMind.AgentRun` system must treat agent skills as an input contract distinct from provider auth, runtime launch, or workspace hydration.

The canonical direction is:

1. the parent workflow resolves the applicable skill set
2. the child agent-run path receives a compact ref such as `resolved_skillset_ref`
3. the runtime adapter materializes that snapshot for the target runtime
4. the adapter also generates a compact activation summary or prompt index as appropriate
5. the underlying runtime sees a stable active skill view through `.agents/skills`

This keeps skill resolution centralized while allowing runtime-specific delivery.

---

## 17. Security and trust model

## 17.1 Untrusted content rule

Repo-provided and workspace-local overlay skill content must be treated as potentially untrusted input.

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
2. whether workspace-local overlay skills are allowed
3. whether deployment-managed skills are mandatory
4. whether task or step overrides are allowed
5. whether specific skills or skill sets are permitted for certain runtimes

## 17.4 Runtime mutation rule

The active projection should be read-only to the runtime where practical.

The runtime must not be allowed to mutate the resolved active bundle as a way of changing policy or broadening the selected skill set.

## 17.5 Auditability

The system must preserve enough data to answer:

1. which skills were used
2. where they came from
3. which versions were selected
4. what the active manifest looked like
5. how the runtime received them
6. what activation summary or compact skill index the runtime was given

---

## 18. Observability and Mission Control

Mission Control should surface the Skill System as a first-class part of execution context.

## 18.1 Submit-time visibility

Where applicable, operators should be able to see or select:

1. named skill sets
2. explicit includes and excludes
3. the intended materialization mode
4. whether repo checked-in skills and workspace-local overlays are enabled for the run

## 18.2 Detail-page visibility

Task detail should be able to show:

1. the resolved skill snapshot ID
2. the selected skill versions
3. the source provenance for each resolved skill
4. the materialization mode
5. the canonical runtime-visible path summary
6. the active backing path summary
7. whether the projection was read-only
8. artifact links for the resolved manifest, active manifest, or prompt index when appropriate

## 18.3 Debug surfaces

Advanced or debug views may additionally expose:

1. raw `resolved_skillset_ref`
2. raw manifest refs
3. source-trace details
4. adapter materialization metadata
5. compatibility-path details
6. the exact activation summary delivered with the step

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

1. store the task’s skill selectors at schedule creation time
2. resolve the actual `ResolvedSkillSet` when the scheduled run starts
3. preserve enough metadata to explain how the scheduled execution’s skill snapshot was chosen

If exact version pinning at schedule creation becomes necessary for certain workloads, that should be an explicit mode rather than silent default behavior.

## 19.3 Rerun semantics

The default rerun rule is:

1. reuse the original `ResolvedSkillSet`
2. do not silently re-resolve “latest” skills
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
4. Shared skill materialization and adapter-visible active-path behavior already exist in some form.
5. The runtime side of skill exposure is already more concrete than the historical control-plane model.

## 20.2 Partially aligned

1. shared skill materialization behavior exists in some runtime guidance and workspace conventions
2. workspace-local mirrors and adapter-visible compatibility paths already exist in some form
3. managed-runtime projection behavior exists, but some manifest naming and adapter details are still transitional
4. the runtime side of skill exposure is more advanced than the fully modeled control-plane data model

## 20.3 Still missing

1. a fully modeled deployment-backed skill catalog
2. explicit persisted `AgentSkillDefinition` and `ResolvedSkillSet` contracts
3. canonical task and step selectors for agent skills in all relevant APIs
4. full Mission Control support for selection and visibility
5. end-to-end audit and provenance surfaces
6. explicit policy enforcement across built-in, deployment, repo checked-in, and workspace-local overlay sources
7. full standardization of the runtime-visible active manifest and adapter metadata surfaces

---

## 21. Test requirements

Changes to the Skill System must include tests at the real workflow, activity, or adapter boundary.

Minimum coverage should include:

1. single-skill active projection at `.agents/skills`
2. multi-skill active projection at `.agents/skills`
3. active backing-store materialization
4. activation-summary injection into the runtime instruction payload
5. collision failure when `.agents` or `.agents/skills` is incompatible
6. exact-snapshot reuse across retry or rerun
7. repo-skill input participation without in-place mutation
8. adapter behavior that does not rediscover or broaden the skill set during execution

If the change affects already-running workflow payloads or persisted history shapes, include in-flight compatibility coverage or explicit cutover notes.

---

## 22. Document boundaries

Use this document for:

* the terminology boundary between executable tool skills, agent skills, and runtime-native commands
* the conceptual design of agent skills as deployment-scoped data
* source precedence
* snapshot resolution
* workspace and runtime path policy
* managed-runtime materialization and injection rules
* projection and collision behavior
* replay, rerun, and audit semantics

Use related docs for:

* executable tool contracts: `docs/Tasks/SkillAndPlanContracts.md`
* control-plane task submission and task surfaces: `docs/Tasks/TaskArchitecture.md`
* managed and external runtime execution: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
* Temporal orchestration boundaries: `docs/Temporal/TemporalArchitecture.md`
* Mission Control surfaces: `docs/UI/MissionControlArchitecture.md`
* implementation phases and backlog: `docs/tmp/AgentSkillSystemPlan.md`

This document is intentionally the single canonical entrypoint for skill-related architecture. Separate runtime-injection docs should not duplicate these rules unless they are narrow adapter implementation notes.

---

## 23. Locked decisions

This document locks the following design decisions.

1. MoonMind uses the word “skill” in more than one sense, and those meanings must stay explicitly separated.
2. Executable tool contracts are distinct from agent instruction bundles.
3. Runtime-native commands are distinct from both.
4. The authoritative managed storage model for agent skills is deployment-backed.
5. `.agents/skills` is the canonical runtime-visible path for the active skill set.
6. `.agents/skills/local` is the workspace-local overlay path.
7. Repo skill folders are valid inputs, not the only source of truth.
8. Each run or step uses an immutable `ResolvedSkillSet`.
9. Managed runtimes use one standard injection shape: full active bundle in a run-scoped backing store, projected at `.agents/skills`, with a compact activation summary inline.
10. The full active root is projected at `.agents/skills` even when only one skill is selected.
11. Runtime adapters may create compatibility links, but `.agents/skills` remains canonical.
12. Runtimes must not re-resolve or rediscover skills during execution.
13. Workflows carry refs, not large skill bodies.
14. Reruns reuse the original resolved snapshot by default.
15. MoonMind must not mutate checked-in skill content in place during run materialization.
16. Projection collisions fail before runtime launch.

---

## 24. Summary

MoonMind should treat the Skill System as a first-class architectural boundary with the same discipline it applies to plans, artifacts, runtime execution, and operator-visible control-plane state.

The canonical model is:

1. distinguish executable tool contracts from agent instruction bundles
2. store agent skills as versioned data
3. allow repo-checked-in and workspace-local overlays as inputs
4. resolve all applicable sources into an immutable active snapshot
5. materialize that snapshot for the target runtime
6. expose the active result through `.agents/skills`
7. keep `.agents/skills/local` as the workspace-local overlay path
8. use one standard managed-runtime injection shape
9. preserve provenance, auditability, and replay safety

This gives MoonMind a consistent, deployment-aware, runtime-safe skill system that covers the full skill lifecycle without splitting core rules across multiple overlapping documents.

[1]: https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/docs/Tasks/AgentSkillSystem.md "raw.githubusercontent.com"
