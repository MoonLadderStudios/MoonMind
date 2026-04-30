# Skill System

Status: Desired State
Owners: MoonMind Engineering
Last Updated: 2026-04-29
Canonical for: agent skills, Skill steps, skill-set resolution, runtime skill materialization, `.agents/skills` path policy
Related: `docs/Steps/StepTypes.md`, `docs/Tasks/SkillAndPlanContracts.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/UI/MissionControlArchitecture.md`, `AGENTS.md`

---

## 1. Purpose

This document defines the desired-state MoonMind **Agent Skill System**.

It is the canonical document for:

1. what a MoonMind Skill is,
2. how Skills differ from Tools,
3. how Skill steps are authored and validated,
4. how reusable agent skill definitions are stored and versioned,
5. how built-in, deployment, repo, and local skill sources are merged,
6. how task and step skill intent resolves into immutable snapshots,
7. how runtimes receive those snapshots,
8. how `.agents/skills` and `.agents/skills/local` are used,
9. how runtime skill injection works for managed and external agents.

This document consolidates the design previously split between:

- `docs/Tasks/AgentSkillSystem.md`
- `docs/Steps/SkillInjection.md`

`docs/Steps/StepTypes.md` remains the canonical document for the product-facing Step Type taxonomy. This document defines what the `skill` Step Type means for agent skills.

This document does **not** define:

1. executable Tool contracts,
2. plan DAG semantics,
3. Preset management and expansion,
4. generic artifact storage internals,
5. provider-profile or auth semantics,
6. runtime launch internals except for the skill materialization boundary.

Use `docs/Tasks/SkillAndPlanContracts.md` for executable tools and plan execution. Use `docs/Steps/StepTypes.md` for the Step Type authoring model. Use `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` for `MoonMind.AgentRun`.

---

## 2. Desired-State Summary

MoonMind has three user-facing Step Types:

```text
Tool   -> run a typed operation directly
Skill  -> ask an agent to perform work using reusable behavior and context
Preset -> insert a reusable authoring template that expands into concrete steps
```

A **Tool** is a typed executable operation. A **Skill** is agent-facing behavior, instruction, and runtime context used for open-ended work. A **Preset** is an authoring template.

The desired-state skill model is:

1. Skills are not a subtype of Tool.
2. Skill steps are executable Step Types, but they execute by launching or configuring an agent runtime, not by invoking a `ToolDefinition`.
3. Agent skill definitions are versioned data, not Temporal activity contracts.
4. A Skill step may use Tools internally, but those Tools are governed capabilities available to the agent, not the Skill itself.
5. MoonMind supports built-in, deployment-stored, repo-checked-in, and local-only agent skill sources.
6. At run or step start, MoonMind resolves skill intent into an immutable `ResolvedSkillSet`.
7. Workflows and runtime requests carry compact refs to resolved skill snapshots, not large skill bodies.
8. Managed runtimes receive a compact activation summary inline and a full read-only active skill bundle projected at `.agents/skills`.
9. Runtime adapters consume the resolved snapshot; they must not rediscover, broaden, or re-resolve Skills during execution.
10. `.agents/skills/local` remains a local-only overlay input convention, not the active runtime projection.

---

## 3. Canonical Terminology

### 3.1 Step Type

A product-facing discriminator for how a step is configured.

Canonical values:

```text
tool | skill | preset
```

Every authored step has exactly one Step Type.

### 3.2 Tool

A typed, schema-backed, policy-checked operation MoonMind can run directly.

Examples:

- `jira.transition_issue`
- `github.create_pull_request`
- `repo.apply_patch`
- `sandbox.run_tests`
- `deployment.update_compose_stack`

A Tool is represented by a `ToolDefinition` and is executed through the tool execution system.

### 3.3 Tool Step

A step with `type: "tool"`.

A Tool step runs a selected `ToolDefinition` with validated inputs.

A Tool step is appropriate when the work is explicit, bounded, and can be expressed as a known operation.

### 3.4 Skill

A reusable agent-facing behavior, instruction bundle, execution mode, or operating procedure.

Examples:

- Code Implementation
- Jira Triage
- PR Review Fixer
- MoonMind Docs Writer
- Repo Coding Conventions
- Unreal C++ Review Checklist
- Python Test Fixer

A Skill is not a `ToolDefinition`.

### 3.5 Skill Step

A step with `type: "skill"`.

A Skill step asks an agent to perform open-ended work using a selected Skill, instructions, context, runtime settings, and allowed Tools.

A Skill step may compile into an `AgentExecutionRequest`, a managed runtime session, or another agent-runtime plan node. It does not compile into a direct Tool invocation merely because the word "skill" appears in the step.

### 3.6 AgentSkillDefinition

The deployment-catalog representation of a reusable Skill.

An `AgentSkillDefinition` is versioned data. It may contain:

- instructions,
- operating rules,
- prompt fragments,
- workspace guidance,
- input schema for Skill-step configuration,
- output expectations,
- runtime compatibility metadata,
- tool-allowance hints,
- examples, templates, or supporting files.

An `AgentSkillDefinition` is not an executable Temporal Tool.

### 3.7 AgentSkillVersion

An immutable version of an `AgentSkillDefinition`.

Editing Skill content creates a new version.

### 3.8 SkillSet

A named collection of Skills or selection rules.

Examples:

- `deployment-default`
- `docs-writer`
- `python-repo`
- `unreal-cpp`
- `jira-triage`

### 3.9 ResolvedSkillSet

The immutable, exact set of Skill versions selected for a specific run or step after all policy, precedence, inheritance, and override rules have been applied.

### 3.10 RuntimeCommandSelection

A runtime-native command selected inside an agent runtime step.

Example:

- `review`

A runtime command is not:

- a Tool,
- a Skill,
- an `AgentSkillDefinition`,
- a `SkillSet`,
- a member of a `ResolvedSkillSet`.

Runtime commands belong to the agent-runtime execution contract and are validated by the owning runtime adapter.

### 3.11 Preset

A reusable authoring template that expands into Tool and/or Skill steps.

A Preset is not normally executable at runtime. Preset expansion must produce concrete executable steps before submission unless a future linked-preset mode is explicitly introduced.

---

## 4. Separation Rules

The following rules are fixed.

1. **Tool** means typed executable operation.
2. **Skill** means agent-facing behavior or instruction data.
3. **Skill Step** means an executable agentic step selected by the user.
4. **AgentSkillDefinition** means versioned skill data in the skill catalog.
5. **Runtime command** means adapter-owned runtime behavior, not a Skill.
6. **Preset** means authoring template, not hidden runtime work.
7. A Skill step may use Tools internally, but Tools are allowed capabilities, not children of the Skill definition.
8. A Tool step must not masquerade as a Skill step merely because it invokes an LLM or agent-related activity.
9. A Skill step must not be resolved from the executable Tool registry.
10. A runtime adapter must not treat runtime commands as Skills or include them in a `ResolvedSkillSet`.

### 4.1 Legacy terminology

Older contracts may contain compatibility terms such as:

- `tool.type = "skill"`
- `SkillDefinition` as a legacy alias for `ToolDefinition`
- `SkillInvocation` as a legacy alias for `Step`
- `selectedSkill`
- `selectedSkillArgs`

These are compatibility terms only.

New authoring surfaces, docs, and API contracts should use:

- `ToolDefinition` for executable tools,
- `AgentSkillDefinition` for agent skills,
- `type: "tool"` for Tool steps,
- `type: "skill"` for Skill steps,
- `runtimeSelection.kind = "agent_skill"` only where an internal runtime-selection adapter needs that shape.

---

## 5. Core Invariants

1. Agent skills are first-class MoonMind data.
2. Agent skills are not executable Tool contracts.
3. Skill steps are first-class executable Step Types.
4. Skill steps execute through agent-runtime orchestration, not through the Tool registry.
5. The deployment-backed skill catalog is the authoritative managed storage model.
6. Repo and local folders may contribute skill inputs, but they are not the only source of truth.
7. Every run or step uses an immutable `ResolvedSkillSet` when Skill content participates.
8. Workflows carry refs to resolved snapshots, not large skill bodies.
9. Runtime adapters own final materialization for their runtime, but must preserve the resolved snapshot.
10. Managed runtimes see the active selected skill set at `.agents/skills`.
11. `.agents/skills/local` is the local-only overlay input path.
12. MoonMind must not mutate checked-in skill files in place during runtime materialization.
13. Reruns reuse the original resolved snapshot by default.
14. Explicit re-resolution is a separate operator action.
15. Skill bodies must not be used as a secret store.
16. Local-only Skills must not bypass deployment policy.

---

## 6. Skill Sources

MoonMind supports these agent skill sources.

### 6.1 Built-in Skills

Built-in Skills ship with MoonMind.

They are useful for:

- common workflows,
- baseline project guidance,
- built-in task patterns,
- starter deployment defaults,
- runtime-specific first-run guidance.

Built-in Skills are versioned with MoonMind releases and may be imported into the deployment catalog or treated as a read-only source during resolution.

### 6.2 Deployment-Stored Skills

Deployment-stored Skills are the primary managed source of truth.

They are useful for:

- organization-wide conventions,
- deployment-specific operating instructions,
- centrally reviewed reusable Skills,
- shared governance and auditability.

A deployment-stored Skill has immutable versions and stable provenance.

### 6.3 Repo Checked-In Skills

A repository may define checked-in Skills under documented repo conventions.

The canonical repo-facing path is:

```text
.agents/skills
```

Repo checked-in Skills are useful for:

- repo-specific contribution guidance,
- stable project conventions,
- instructions intended to travel with the repository.

Repo checked-in Skills are resolution inputs. They are not mutable runtime state.

### 6.4 Repo Local-Only Skills

A repository may define local-only Skills under:

```text
.agents/skills/local
```

Local-only Skills are useful for:

- developer-specific overlays,
- local experiments,
- non-version-controlled mirrors,
- migration from older local-only skill flows.

Local-only Skills are excluded from version control by default and may participate only if deployment policy allows them.

### 6.5 Explicit Task and Step Selectors

A task or step may explicitly include, exclude, or pin Skills or SkillSets.

Explicit selectors express execution intent and participate in resolution before runtime launch.

---

## 7. Source Precedence and Merge Rules

### 7.1 Precedence

When multiple sources provide candidate Skills, MoonMind resolves them in this order:

1. built-in Skills,
2. deployment-stored Skills,
3. repo checked-in Skills,
4. repo local-only Skills,
5. explicit task or step overrides.

Later layers may override earlier layers where policy allows.

### 7.2 Collision Rules

When more than one source provides a Skill with the same canonical name:

1. a later-precedence source may override an earlier source by name;
2. the resolved snapshot must record the winning source and version;
3. if two candidates at the same precedence level conflict and no deterministic tie-breaker exists, resolution fails;
4. if a selector pins a version that cannot be satisfied, resolution fails;
5. if policy forbids a source kind, candidates from that source are excluded before precedence applies.

### 7.3 Policy Gates

Deployments may restrict:

1. whether repo Skills are allowed,
2. whether local-only Skills are allowed,
3. which built-in or deployment Skills are eligible,
4. whether step-level overrides are permitted,
5. whether local-only Skills may shadow deployment-managed Skills,
6. which Skills are allowed for which runtimes,
7. which Tools a Skill step may make available to the agent,
8. whether a Skill may be used in unattended or high-autonomy modes.

Policy is enforced during resolution and validation, not after runtime launch.

---

## 8. Data Model

Concrete storage schemas may vary, but the logical model must preserve these contracts.

### 8.1 AgentSkillDefinition

Represents the stable identity of a Skill.

Suggested fields:

```ts
type AgentSkillDefinition = {
  skill_id: string;
  name: string;
  title: string;
  description?: string;
  source_kind: "builtin" | "deployment" | "repo" | "local";
  visibility: "system" | "deployment" | "repo" | "local";
  enabled: boolean;
  skill_kind?: "instruction_bundle" | "agent_behavior";
  input_schema_ref?: string;
  output_contract_ref?: string;
  default_supported_runtimes?: string[];
  default_allowed_tools?: string[];
  created_at: string;
  updated_at: string;
};
```

`skill_kind` is descriptive, not a dispatch mechanism. Even an `agent_behavior` Skill is not a `ToolDefinition`.

### 8.2 AgentSkillVersion

Represents an immutable version of Skill content.

Suggested fields:

```ts
type AgentSkillVersion = {
  skill_id: string;
  version: string;
  content_ref: string;
  format: "skill_md" | "markdown_bundle" | "json_manifest" | "mixed_bundle";
  checksum: string;
  supported_runtimes?: string[];
  metadata?: Record<string, unknown>;
  created_at: string;
  created_by?: string;
};
```

Rules:

1. versions are immutable;
2. editing content creates a new version;
3. large bodies live in artifact/blob storage;
4. workflow history carries refs, not bodies.

### 8.3 SkillSet

Represents a named collection or selection surface.

Suggested fields:

```ts
type SkillSet = {
  skill_set_id: string;
  name: string;
  scope: "system" | "deployment" | "repo" | "task" | "step";
  description?: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};
```

### 8.4 SkillSetEntry

Represents one selection entry inside a `SkillSet`.

Suggested fields:

```ts
type SkillSetEntry = {
  skill_set_id: string;
  selector_type: "include" | "exclude" | "pin" | "condition";
  skill_name?: string;
  version?: string;
  order?: number;
  conditions?: Record<string, unknown>;
};
```

### 8.5 ResolvedSkillSet

Represents the immutable Skill snapshot used by a specific run or step.

Suggested fields:

```ts
type ResolvedSkillSet = {
  snapshot_id: string;
  deployment_id: string;
  task_id?: string;
  step_id?: string;
  resolved_at: string;
  skills: ResolvedSkillEntry[];
  manifest_ref: string;
  source_trace_ref?: string;
  resolution_inputs_ref?: string;
  policy_summary?: Record<string, unknown>;
};
```

### 8.6 ResolvedSkillEntry

Suggested fields:

```ts
type ResolvedSkillEntry = {
  name: string;
  skill_id: string;
  version: string;
  source_kind: "builtin" | "deployment" | "repo" | "local";
  content_ref: string;
  checksum: string;
  visible_path?: string;
  supported_runtimes?: string[];
};
```

### 8.7 RuntimeSkillMaterialization

Represents the runtime-facing rendering of a resolved snapshot.

Suggested fields:

```ts
type RuntimeSkillMaterialization = {
  runtime_id: string;
  snapshot_id: string;
  materialization_mode: "prompt_bundled" | "workspace_mounted" | "hybrid" | "retrieval";
  visible_path?: string;
  backing_path?: string;
  manifest_ref?: string;
  prompt_index_ref?: string;
  retrieval_manifest_ref?: string;
  read_only?: boolean;
  metadata?: Record<string, unknown>;
};
```

---

## 9. Skill Step Authoring Contract

### 9.1 Desired Shape

The user-facing desired shape follows the Step Type model.

Illustrative shape:

```json
{
  "id": "implement-issue",
  "title": "Implement Jira issue",
  "type": "skill",
  "skill": {
    "id": "code.implementation",
    "version": "1.0.0",
    "inputs": {
      "repository": "MoonLadderStudios/MoonMind",
      "issueKey": "MM-123",
      "instructions": "Implement the issue and prepare a pull request."
    },
    "skillSets": ["deployment-default", "repo-default"],
    "include": [
      { "name": "repo-coding-conventions" }
    ],
    "exclude": ["legacy-reviewer"],
    "allowedTools": [
      "repo.apply_patch",
      "sandbox.run_tests",
      "github.create_pull_request"
    ],
    "runtime": {
      "mode": "codex_cli",
      "materializationMode": "hybrid"
    }
  }
}
```

The exact API may split selectors into `step.skill`, `step.agentSkills`, or another nested field. The semantic requirements are fixed:

1. the step is `type: "skill"`;
2. the selected Skill is not resolved from the Tool registry;
3. Skill inputs validate against the Skill contract;
4. Skill content selection resolves through the Agent Skill System;
5. allowed Tools are governed separately from Skill identity;
6. runtime preferences are validated against runtime compatibility and policy.

### 9.2 Skill Step vs Agent Skill Bundle

A Skill step may select:

1. one primary `AgentSkillDefinition`,
2. one or more supporting SkillSets,
3. explicit included Skills,
4. explicit excluded Skills,
5. runtime and Tool policy.

The selected primary Skill describes agent behavior. The resolved snapshot describes the exact instruction bundles and supporting content made active for the runtime.

### 9.3 Task-Level Skill Intent

A task may define baseline Skill intent inherited by Skill steps.

Illustrative shape:

```json
{
  "task": {
    "skills": {
      "sets": ["deployment-default"],
      "include": [
        { "name": "moonmind-doc-writer", "version": "2.3.0" }
      ],
      "exclude": [],
      "materializationMode": "hybrid"
    }
  }
}
```

### 9.4 Inheritance Rules

1. `task.skills` defines the task-wide baseline.
2. `step.skill` may add, override, or exclude task-level skill intent.
3. A step may explicitly opt out of inherited optional Skills where policy allows.
4. Absent step-level selectors means the step inherits the task baseline.
5. A step-level exact version pin overrides unpinned task-level selection if policy allows it.
6. Policy may require mandatory Skills that cannot be excluded.

### 9.5 Validation Rules

A Skill step is valid only when:

1. the selected Skill exists or can be resolved by documented `auto` semantics;
2. requested Skill versions can be resolved;
3. Skill inputs validate against the Skill's input contract;
4. the target runtime is compatible;
5. required repository, project, or artifact context is present;
6. selected Tools are allowed and available;
7. approval and autonomy settings are enforceable;
8. policy allows the selected source kinds;
9. resolution can produce an immutable `ResolvedSkillSet`.

---

## 10. Resolution Lifecycle

### 10.1 Resolution Timing

Skill resolution happens before runtime launch.

Resolution may happen:

1. at task submission,
2. during step preparation,
3. in a dedicated resolution activity,
4. during proposal promotion where exact execution intent is being frozen.

Workflow code must not resolve large Skill bodies inline.

### 10.2 Resolution Algorithm

Canonical flow:

1. collect task-level and step-level Skill selectors;
2. identify target runtime and execution mode;
3. load deployment policy;
4. determine allowed source kinds;
5. load candidate Skills from built-in, deployment, repo, and local sources;
6. apply source precedence and collision rules;
7. validate runtime compatibility;
8. validate allowed Tool policy;
9. pin exact Skill versions;
10. write a resolved manifest artifact;
11. write a compact prompt index when needed;
12. return a compact `ResolvedSkillSet` ref.

### 10.3 Failure Behavior

Resolution fails before runtime launch when:

1. a required Skill is missing;
2. a pinned version cannot be resolved;
3. collisions cannot be resolved deterministically;
4. policy forbids a requested source or override;
5. a Skill is incompatible with the runtime and no allowed fallback exists;
6. required source content cannot be read;
7. a Skill body or manifest checksum does not match;
8. a selected Tool is not allowed for the Skill step.

### 10.4 Provenance

The resolved snapshot must record enough provenance to answer:

1. which Skills were selected,
2. which versions were selected,
3. where each Skill came from,
4. which selectors influenced the result,
5. which policies allowed or denied candidates,
6. which Skills were inherited,
7. which Skills were excluded,
8. which runtime materialization mode was selected.

---

## 11. Versioning, Immutability, and Replay

### 11.1 Skill Version Immutability

Agent Skill versions are immutable.

Changing Skill content creates a new version.

### 11.2 Snapshot Immutability

A `ResolvedSkillSet` is immutable.

Rules:

1. a run or step pins exact Skill versions;
2. retries reuse the same snapshot;
3. reruns reuse the original snapshot by default;
4. re-resolution is explicit and creates a new snapshot;
5. continue-as-new preserves the existing snapshot unless explicitly changed.

### 11.3 Artifact Discipline

These must live in artifacts or equivalent blob storage:

- full Skill bodies,
- large Skill bundles,
- resolved manifests,
- prompt indexes,
- source traces,
- materialization bundles.

Workflow history carries compact refs and bounded metadata only.

---

## 12. Workspace Path Policy

### 12.1 Canonical Visible Path

For managed runtimes, the active resolved Skill set is visible at:

```text
.agents/skills
```

This path is the stable runtime-facing interface.

### 12.2 Local Overlay Path

The local-only overlay input path is:

```text
.agents/skills/local
```

It is used for local-only source input, not the active runtime projection.

### 12.3 Active-Set Rule

The runtime-visible `.agents/skills` tree is a projection of the resolved active snapshot.

It is not a mutable merge of checked-in repo folders.

### 12.4 Mutation Rule

MoonMind must not rewrite checked-in user-authored Skill files in place during run materialization.

Repo Skill folders are inputs to resolution. The runtime active tree is produced from the resolved snapshot.

---

## 13. Runtime Materialization Modes

The Agent Skill System supports multiple conceptual delivery modes because runtimes differ.

### 13.1 `prompt_bundled`

Skill content or compact summaries are included directly in the runtime instruction payload.

Best for:

- small Skill sets,
- external agents without filesystem access,
- providers with simple prompt-only execution.

### 13.2 `workspace_mounted`

Full Skill content is materialized into workspace files.

Best for:

- managed coding runtimes,
- larger Skill bundles,
- file-aware agents.

### 13.3 `hybrid`

A compact activation summary or prompt index is supplied inline, while full Skill content is available on disk.

This is the default for managed coding runtimes.

### 13.4 `retrieval`

The runtime receives a manifest or helper path for on-demand retrieval.

This is forward-compatible for large catalogs or advanced adapters, but it is not the default managed-runtime path.

---

## 14. Canonical Managed Runtime Injection Model

For managed coding runtimes, MoonMind uses one canonical injection model:

```text
compact activation summary inline
+
read-only full active Skill bundle projected at .agents/skills
+
run-scoped MoonMind-owned backing store
```

### 14.1 Managed Runtime Sequence

Before execution begins, MoonMind must:

1. resolve Skill intent into an immutable `ResolvedSkillSet`;
2. materialize the full active Skill bundle into a run-scoped backing store;
3. project that backing store at `.agents/skills`;
4. include a compact activation summary in the runtime instructions;
5. launch the runtime with refs to the resolved snapshot and materialization metadata.

### 14.2 Active Backing Store

The backing store is MoonMind-owned, run-scoped, and corresponds exactly to one resolved snapshot.

Representative path:

```text
/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>/
```

The exact path is implementation-specific.

Rules:

1. the backing store must be run-scoped;
2. it must be owned by MoonMind, not repo authors;
3. it should be read-only to the runtime where practical;
4. it must correspond exactly to one resolved snapshot;
5. it must contain only selected Skills and MoonMind-owned metadata.

### 14.3 Runtime-Visible Shape

The runtime-visible tree should look like:

```text
.agents/
  skills/
    _manifest.json
    code-implementation/
      SKILL.md
      examples/
      templates/
    repo-coding-conventions/
      SKILL.md
```

Rules:

1. `SKILL.md` remains the primary Skill entrypoint.
2. `_manifest.json` is MoonMind-owned metadata.
3. only selected Skills appear in the active tree.
4. unselected repo Skills must not appear.
5. a single-Skill run still projects the full active root.

### 14.4 Projection Mechanics

MoonMind may expose the active backing store at `.agents/skills` through:

- bind mount,
- symlink,
- overlay view,
- copy-on-write staging,
- adapter-specific projection.

The mechanism is not the contract.

The contract is:

1. the runtime sees the active Skill set at `.agents/skills`;
2. the content corresponds exactly to the resolved snapshot;
3. the runtime is not expected to discover Skills anywhere else;
4. projection must fail before runtime launch if it cannot be installed safely.

### 14.5 Inline Activation Summary

Every managed runtime Skill step should include a compact activation block.

Required contents:

1. active Skill names,
2. visible path `.agents/skills`,
3. first-read hints,
4. hard rules that must be applied immediately,
5. optional selected Tool or autonomy constraints.

Example:

```text
Active MoonMind skills for this step:
- code-implementation: implement the requested change and prepare reviewable output
- repo-coding-conventions: follow repository style and contribution rules

Full skill content is available under .agents/skills.
Read .agents/skills/code-implementation/SKILL.md before preparing the plan.

Hard rules:
- do not broaden scope beyond the requested issue
- prefer the smallest safe change
- keep full skill bodies on disk; do not duplicate them in responses
```

The activation summary is not a duplicate full-body Skill bundle.

### 14.6 Active Manifest

Each active projection includes `_manifest.json`.

Suggested fields:

```json
{
  "snapshot_id": "rss_123",
  "resolved_at": "2026-04-29T00:00:00Z",
  "visible_path": ".agents/skills",
  "backing_path": "/work/agent_jobs/job_123/runtime/skills_active/rss_123",
  "read_only": true,
  "skills": [
    {
      "name": "code-implementation",
      "version": "1.0.0",
      "source_kind": "deployment",
      "checksum": "sha256:..."
    }
  ],
  "source_summary": {
    "deployment": 1,
    "repo": 1,
    "local": 0
  }
}
```

The manifest is for observability and adapter introspection. The agent should primarily read `SKILL.md`.

### 14.7 Collision Policy

MoonMind owns the runtime-visible active projection at `.agents/skills` for the duration of the run.

A collision exists when MoonMind cannot safely expose the active root at `.agents/skills`.

Examples:

- `.agents` exists as a file;
- `.agents/skills` exists as a file;
- `.agents/skills` is an unreplaceable directory;
- a stale symlink points to an incompatible backing store;
- adapter or filesystem restrictions prevent projection.

Preferred behavior:

1. materialize the active set outside the repo;
2. project it at `.agents/skills`;
3. avoid mutating checked-in Skill sources;
4. fail before runtime launch if projection cannot be installed safely.

Projection failures must include:

- conflicting path,
- conflicting object kind,
- attempted projection action,
- remediation guidance.

### 14.8 Adapter Responsibilities

A managed runtime adapter must:

1. consume a pinned resolved snapshot ref;
2. materialize the active backing store exactly once per snapshot where practical;
3. expose the active set at `.agents/skills`;
4. inject the activation summary;
5. avoid re-resolving Skill sources;
6. avoid adding unselected Skills;
7. avoid rewriting checked-in Skill inputs;
8. preserve snapshot identity in logs, artifacts, and observability metadata.

A managed runtime adapter must not:

1. scan the repo for additional Skills during execution;
2. silently fall back to a different visible path;
3. treat runtime commands as Skills;
4. mutate the active bundle as a way of changing policy.

---

## 15. External Agent Skill Delivery

External agents may not have the same filesystem semantics as managed runtimes.

They may receive resolved Skill context through:

1. compact prompt bundles,
2. uploaded bundle artifacts,
3. presigned manifest or bundle URLs,
4. provider-specific translated representations,
5. compact summaries plus retrieval links where supported.

External adapters must still:

1. consume the immutable resolved snapshot;
2. avoid re-resolving sources;
3. preserve provenance;
4. avoid broadening the selected Skill set;
5. keep full Skill bodies out of workflow history;
6. map delivery metadata into canonical `AgentExecutionRequest` fields or adapter metadata.

---

## 16. Interaction with `MoonMind.AgentRun`

A Skill step that requires true agent execution compiles into an agent runtime execution path.

The desired flow is:

1. `MoonMind.Run` owns task-level orchestration and step order.
2. Skill resolution happens before runtime launch.
3. The parent workflow passes compact refs into the agent-run path.
4. `MoonMind.AgentRun` consumes an `AgentExecutionRequest`.
5. The runtime adapter materializes or translates the resolved snapshot.
6. The agent runtime receives a stable Skill view.
7. Final outputs are stored as artifacts and structured results.

Suggested `AgentExecutionRequest` extension fields:

```ts
type SkillContextFields = {
  resolved_skillset_ref?: string;
  skill_materialization_mode?: "prompt_bundled" | "workspace_mounted" | "hybrid" | "retrieval";
  skill_prompt_index_ref?: string;
  skill_manifest_ref?: string;
  skill_policy_summary?: Record<string, unknown>;
};
```

`MoonMind.AgentRun` must not independently re-resolve Skill sources.

---

## 17. Tool Access Inside Skill Steps

A Skill step may need Tools to complete work.

Examples:

- read issue details,
- apply patches,
- run tests,
- create a pull request,
- post a Jira comment.

Tool access inside a Skill step is governed separately from Skill identity.

Rules:

1. Skill selection does not automatically grant all Tools.
2. Allowed Tools must be declared, inherited, or policy-derived.
3. Tool use remains subject to authorization, worker capability, safety, and approval rules.
4. A Tool invoked by an agent is still a Tool.
5. Tool outputs remain Tool outputs and artifact refs.
6. Agent decisions about which Tool to use belong to the Skill step/runtime policy, not to the Tool registry.

---

## 18. Security and Trust

### 18.1 Untrusted Content

Repo and local-only Skills are potentially untrusted input.

MoonMind must not assume Skill content is safe because it lives in a workspace.

### 18.2 Secret Handling

Skill bodies must not contain secrets.

MoonMind must not:

1. encourage secret storage in Skills,
2. log full Skill bodies by default,
3. materialize raw credentials into Skill manifests,
4. pass secrets through Skill content for convenience.

### 18.3 Policy Enforcement

Deployments may require or restrict:

1. source kinds,
2. specific Skills,
3. specific SkillSets,
4. runtimes,
5. Tool availability,
6. approval modes,
7. autonomy levels,
8. local-only overlays.

### 18.4 Read-Only Active Bundles

Managed runtime active bundles should be read-only where practical.

The runtime must not mutate the active Skill bundle to change policy or record state.

---

## 19. Observability and Mission Control

Mission Control should treat Skill context as first-class execution context.

### 19.1 Submit-Time Visibility

Operators should be able to see or select:

1. primary Skill,
2. SkillSets,
3. explicit includes and excludes,
4. materialization mode,
5. whether repo overlays are enabled,
6. whether local overlays are enabled,
7. Tool availability for Skill steps,
8. runtime compatibility warnings.

### 19.2 Detail-Page Visibility

Task and step detail should show:

1. selected Skill step identity,
2. resolved snapshot ID,
3. selected Skill names and versions,
4. source provenance,
5. materialization mode,
6. active visible path,
7. backing store summary where relevant,
8. activation summary,
9. manifest or prompt-index artifact links,
10. projection failures.

### 19.3 Debug Visibility

Advanced views may expose:

1. raw `resolved_skillset_ref`,
2. raw manifest refs,
3. source traces,
4. policy summaries,
5. materialization metadata,
6. adapter-specific compatibility links.

---

## 20. Proposal, Schedule, Rerun, and Replay Semantics

### 20.1 Proposals

A proposal that depends on Skills must preserve execution intent.

Preferred behavior:

1. store executable Tool and Skill steps;
2. store explicit Skill selectors where they materially affect execution;
3. preserve preset provenance as metadata only;
4. avoid live preset lookup during promotion.

### 20.2 Scheduled Work

Scheduled work should store Skill intent at schedule creation.

When a scheduled run starts, MoonMind resolves the actual `ResolvedSkillSet` unless the schedule explicitly pins Skill versions.

Exact version pinning at schedule creation is an explicit mode, not the silent default.

### 20.3 Reruns

Default rerun behavior:

1. reuse the original `ResolvedSkillSet`;
2. do not silently re-resolve "latest" Skills;
3. require explicit operator intent for re-resolution.

### 20.4 Retries and Continue-as-New

Retries and continue-as-new preserve the same resolved snapshot unless a new resolution action is explicitly requested.

---

## 21. Validation and Test Requirements

Minimum validation coverage:

1. Skill step validates against Skill contract and runtime compatibility.
2. Tool step and Skill step cannot be mixed in one step payload.
3. Runtime commands cannot be added to `ResolvedSkillSet`.
4. Missing required Skill fails before runtime launch.
5. Forbidden source kind fails during resolution.
6. Version pin mismatch fails during resolution.
7. Local-only Skill use is policy-gated.
8. Selected Tools in a Skill step are policy-gated.
9. Rerun uses original snapshot by default.

Minimum managed-runtime materialization coverage:

1. one-Skill active projection at `.agents/skills`;
2. multi-Skill active projection at `.agents/skills`;
3. only selected Skills appear in the active projection;
4. `_manifest.json` is present and matches the resolved snapshot;
5. checked-in `.agents/skills` can be an input without being rewritten;
6. `.agents/skills/local` remains an input overlay, not the active projection;
7. incompatible `.agents` or `.agents/skills` fails before runtime launch;
8. activation summary is included inline;
9. full Skill bodies are on disk or artifact-backed storage, not duplicated inline;
10. retries and reruns preserve snapshot identity.

---

## 22. Documentation Boundaries and Cleanup

This document should be the single canonical desired-state document for the Agent Skill System.

Recommended doc cleanup:

1. Keep this document at `docs/Tasks/AgentSkillSystem.md`.
2. Replace `docs/Steps/SkillInjection.md` with a short redirect or remove it after references are updated.
3. Keep `docs/Steps/StepTypes.md` as the canonical Step Type taxonomy.
4. Keep `docs/Tasks/SkillAndPlanContracts.md` as the canonical Tool and Plan contract document.
5. Update `SkillAndPlanContracts.md` to clearly mark `tool.type = "skill"` as legacy/internal terminology, not the product-facing Skill Step.
6. Update any docs that say "Skill is a Tool" to instead say "Skill step may use Tools; Skills are agent-facing behavior/data."
7. Put rollout tasks, migration checklists, and implementation status in specs or tracking artifacts, not in this canonical desired-state doc.

Suggested redirect text for `docs/Steps/SkillInjection.md`:

```markdown
# Skill Injection

Status: Superseded

The desired-state skill injection contract has moved into
`docs/Tasks/AgentSkillSystem.md`.

Use `AgentSkillSystem.md` for:
- runtime skill materialization,
- `.agents/skills` path policy,
- activation summaries,
- active backing-store projection,
- managed and external runtime skill delivery.

This file is retained only for historical reference and should not be updated
with new design decisions.
```

---

## 23. Locked Decisions

1. Skills are separate from Tools.
2. Skill steps are first-class executable Step Types.
3. Agent skills are versioned data, not `ToolDefinition`s.
4. Skill steps execute through agent-runtime orchestration, not direct Tool registry dispatch.
5. A Skill step may use Tools internally under policy.
6. Runtime commands are not Skills.
7. Presets expand into concrete Tool and Skill steps before execution by default.
8. The deployment-backed skill catalog is the authoritative managed storage model.
9. Repo and local folders are valid inputs, not mutable runtime state.
10. Every run or step uses an immutable `ResolvedSkillSet`.
11. Workflows carry compact refs, not full Skill bodies.
12. Managed runtimes use compact inline activation plus full active bundle on disk.
13. `.agents/skills` is the canonical managed-runtime visible path.
14. `.agents/skills/local` is the local-only overlay input path.
15. MoonMind must not mutate checked-in Skill files during runtime materialization.
16. Runtimes must not re-resolve or broaden Skills during execution.
17. Reruns reuse the original resolved snapshot by default.
18. Explicit re-resolution creates a new snapshot.
19. Full Skill bodies, manifests, prompt indexes, and materialization bundles live outside workflow history.
20. Mission Control surfaces Skill selection, resolution, provenance, and materialization as first-class execution context.

---

## 24. Summary

MoonMind should treat Skills as a first-class agent behavior and instruction system, distinct from typed executable Tools.

The desired model is:

1. users author Tool, Skill, or Preset steps;
2. Skill steps select agent-facing behavior and context;
3. reusable agent Skills are stored as versioned data;
4. task and step selectors resolve into immutable snapshots;
5. managed runtimes receive compact activation instructions plus a full active bundle at `.agents/skills`;
6. external runtimes receive equivalent snapshot-derived context through provider-compatible delivery;
7. Tools remain governed operations that Skill steps may use under policy;
8. provenance, replayability, auditability, and path semantics remain stable across runs.

This gives MoonMind one declarative desired-state skill design without continuing to split core behavior across separate control-plane and injection documents.
