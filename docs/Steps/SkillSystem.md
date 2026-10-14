# Skill System

Covers: agent skills, Skill steps, skill-set resolution, runtime skill materialization, `.agents/skills` path policy
Related: `docs/Steps/StepTypes.md`, `docs/Workflows/SkillAndPlanContracts.md`, `docs/Workflows/WorkflowArchitecture.md`, `docs/Workflows/RequiredCapabilities.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/UI/WorkflowConsoleArchitecture.md`

---

## 1. Purpose

This document describes the MoonMind **Agent Skill System**: what a Skill is
and how it differs from a Tool, how Skill steps are authored and validated, how
skill sources are stored, merged, and resolved into immutable snapshots, how
managed and external runtimes receive those snapshots, and how `.agents/skills`
and `.agents/skills/local` are used.

`docs/Steps/StepTypes.md` describes the product-facing Step Type taxonomy; this
document describes what the `skill` Step Type means for agent skills. Executable
Tool contracts and plan execution are in `docs/Workflows/SkillAndPlanContracts.md`,
and `MoonMind.AgentRun` is in
`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`. Preset expansion,
artifact storage internals, provider-profile/auth semantics, and runtime launch
internals beyond the skill materialization boundary are out of scope.

---

## 2. Summary

MoonMind has three user-facing Step Types:

```text
Tool   -> run a typed operation directly
Skill  -> ask an agent to perform work using reusable behavior and context
Preset -> insert a reusable authoring template that expands into concrete steps
```

A **Tool** is a typed executable operation. A **Skill** is agent-facing behavior, instruction, and runtime context used for open-ended work. A **Preset** is an authoring template.

Skills are not a subtype of Tool. Skill steps execute by launching or
configuring an agent runtime, not by invoking a `ToolDefinition`; they may use
Tools internally as governed capabilities. At run or step start, MoonMind
resolves skill intent from built-in, deployment, repo, and local sources into
an immutable `ResolvedSkillSet`, and workflows carry compact refs to it. Managed
runtimes receive a compact activation summary inline plus a full read-only
active bundle at the materialized `visiblePath`, and runtime adapters do not
rediscover, broaden, or re-resolve Skills during execution. A native host is
selected from trusted resolved-skill evidence, never from canonical skill name
or publish mode alone, and is valid only when it executes the exact portable
semantic entrypoint from that resolved content.

### Native implementation contracts

Skills may declare a portable semantic implementation contract and supported
hosts independently of their publish metadata. The resolved entry preserves the
winning source, content ref, digest, contract, supported hosts, and native-host
policy. A native binding may supply execution substrate, but the resolved Skill
bundle remains the sole semantic authority. The binding executes a portable
entrypoint from that exact resolved content and does not reproduce the Skill's
data collection, classification, ordering, retry, remediation, or completion
logic in MoonMind code.

Allowed native responsibilities are limited to capabilities that cannot live in
portable Skill content without violating platform boundaries: immutable
resolution and materialization, credential delivery, workspace isolation,
process supervision, durable scheduling, timeout and cancellation enforcement,
approval enforcement, log and artifact transport, and validation or publication
of the Skill's declared terminal evidence. Durability is not permission to move
semantic decisions out of the Skill. A Temporal workflow may durably invoke the
same portable entrypoint at Activity boundaries without becoming a second
implementation of it.

Later-precedence repo or local content with the same skill name remains different
content and receives no built-in native privileges by name. When a compatible
binding cannot execute the exact resolved implementation, the runtime records an
explicit portable-host decision or rejects the step before launch with a policy
diagnostic. It does not substitute other Skill content or a parallel built-in
implementation.

A valid native binding can name both the irreducibly native capability it
supplies and the portable semantic entrypoint from the resolved Skill that it
executes. Cross-host conformance tests add defense in depth, but matching tests
do not make duplicated semantic implementations acceptable.

---

## 3. Terminology

- **Step Type** — the product-facing discriminator for how a step is
  configured: `tool | skill | preset`. Every authored step has exactly one.
- **Tool** — a typed, schema-backed, policy-checked operation MoonMind can run
  directly (for example `jira.transition_issue`, `repo.apply_patch`,
  `sandbox.run_tests`), represented by a `ToolDefinition` and executed through
  the tool execution system.
- **Tool step** — a step with `type: "tool"` that runs a selected
  `ToolDefinition` with validated inputs. It fits work that is explicit,
  bounded, and expressible as a known operation.
- **Skill** — a reusable agent-facing behavior, instruction bundle, execution
  mode, or operating procedure (for example Code Implementation, Jira Triage,
  Repo Coding Conventions). A Skill is not a `ToolDefinition`.
- **Skill step** — a step with `type: "skill"` that asks an agent to perform
  open-ended work using a selected Skill, instructions, context, runtime
  settings, and allowed Tools. It compiles into an `AgentExecutionRequest`, a
  managed runtime session, or another agent-runtime plan node, never into a
  direct Tool invocation merely because the word "skill" appears.
- **AgentSkillDefinition** — the deployment-catalog representation of a
  reusable Skill: named instruction data such as instructions, operating rules,
  prompt fragments, input schema, output expectations, runtime compatibility
  metadata, tool-allowance hints, and supporting files. It is not an executable
  Temporal Tool.
- **AgentSkillContent** — an immutable content-addressed revision of a
  definition, evidenced by `content_ref` and `content_digest`. The Skill is
  still selected by name.
- **SkillSet** — a named collection of Skills or selection rules (for example
  `deployment-default`, `python-repo`).
- **ResolvedSkillSet** — the immutable, exact set of Skill names and content
  evidence selected for a run or step after policy, precedence, inheritance,
  and overrides are applied.
- **Runtime command** — a runtime-native command such as `review` selected
  inside an agent runtime step. It belongs to the agent-runtime execution
  contract, is validated by the owning runtime adapter, and is never a Tool,
  Skill, SkillSet, or member of a `ResolvedSkillSet`.
- **Preset** — a reusable authoring template that expands into concrete Tool
  and/or Skill steps before submission; it is not executable at runtime.

## 4. Separation Rules

A Skill step may use Tools internally, but Tools are allowed capabilities, not
children of the Skill definition. A Tool step is not a Skill step merely
because it invokes an LLM or agent-related activity, a Skill step is not
resolved from the executable Tool registry, and runtime adapters do not treat
runtime commands as Skills.

### 4.1 Compatibility terminology

Some contracts still contain older terms: `tool.type = "skill"`,
`SkillDefinition` as an alias for `ToolDefinition`, `SkillInvocation` as an
alias for `Step`, `selectedSkill`, and `selectedSkillArgs`. Authoring surfaces,
docs, and API contracts use `ToolDefinition` for executable tools,
`AgentSkillDefinition` for agent skills, `type: "tool"` / `type: "skill"` for
steps, and `runtimeSelection.kind = "agent_skill"` only where an internal
runtime-selection adapter needs that shape.

---

## 5. Core Invariants

1. Agent skills are first-class MoonMind data, not executable Tool contracts.
2. Skill steps are first-class executable Step Types that run through agent-runtime orchestration, not the Tool registry.
3. The deployment-backed skill catalog is the authoritative managed storage model; repo and local folders contribute inputs but are not the only source of truth.
4. Every run or step uses an immutable `ResolvedSkillSet` when Skill content participates, and workflows carry refs to it, not large skill bodies.
5. Runtime adapters own final materialization for their runtime while preserving the resolved snapshot.
6. Managed runtimes see the active selected skill set at the materialized `visiblePath`; `.agents/skills` remains the repo-authored source path and optional safe alias, and `.agents/skills/local` is the local-only overlay input path.
7. MoonMind does not mutate checked-in skill files in place during runtime materialization (§12.3 notes one exception).
8. Reruns reuse the original resolved snapshot by default; re-resolution is a separate operator action.
9. Skill bodies are not a secret store.
10. Local-only Skills are subject to deployment policy like any other source.

---

## 6. Skill Sources

- **Built-in Skills** ship with MoonMind releases and cover common workflows,
  baseline guidance, and starter defaults. They may be imported into the
  deployment catalog or treated as a read-only source during resolution.
- **Deployment-stored Skills** are the primary managed source of truth, used
  for deployment-wide conventions and centrally reviewed reusable Skills. Each
  has immutable content evidence and stable provenance.
- **Repo checked-in Skills** live under `.agents/skills` and carry
  repo-specific guidance that travels with the repository. They are resolution
  inputs, not mutable runtime state.
- **Repo local-only Skills** live under `.agents/skills/local` for
  developer-specific overlays and experiments. They are excluded from version
  control by default and participate only if deployment policy allows them.

### 6.1 Explicit Workflow and Step Selectors

A workflow execution or step may explicitly include or exclude Skills or
SkillSets; these selectors participate in resolution before runtime launch.
Selectors identify Skills by canonical skill name only. They do not carry semantic skill versions; resolved content refs and digests provide the evidence for the exact instruction content used.

---

## 7. Source Precedence and Merge Rules

### 7.1 Precedence

When multiple sources provide candidate Skills, MoonMind resolves them in this order:

1. built-in Skills,
2. deployment-stored Skills,
3. repo checked-in Skills,
4. repo local-only Skills,
5. explicit workflow or step overrides.

Later layers may override earlier layers where policy allows.

### 7.2 Collisions and Policy

A later-precedence source may override an earlier one by canonical name, and
the snapshot records the winning source and content evidence. If two
candidates at the same precedence level conflict with no deterministic
tie-breaker, resolution fails. Candidates from a source kind that policy
forbids are excluded before precedence applies.

Deployments may restrict whether repo or local-only Skills are allowed, which
built-in or deployment Skills are eligible, whether step-level overrides are
permitted, whether local-only Skills may shadow deployment-managed ones, which
Skills are allowed per runtime, which Tools a Skill step may expose, and
whether a Skill may be used in unattended or high-autonomy modes. Policy is
enforced during resolution and validation, not after runtime launch.

---

## 8. Data Model

The Pydantic models in `moonmind/schemas/agent_skill_models.py` are the
authoritative shapes. Logically:

- **AgentSkillDefinition** — stable identity: name, title, description,
  source kind (`builtin`, `deployment`, `repo`, `local`), enabled flag,
  input/output contract refs, and default runtime and tool hints. A descriptive
  `skill_kind` is not a dispatch mechanism.
- **AgentSkillContent** — immutable `content_ref`, `content_digest`, and
  format; large bodies live in artifact/blob storage.
- **SkillSet** — include, exclude, or conditional selection entries at system,
  deployment, repo, task, or step scope.
- **ResolvedSkillSet** — `snapshot_id`, `resolved_at`, selected
  `ResolvedSkillEntry` list, `manifest_ref`, source trace, resolution inputs,
  and policy summary.
- **ResolvedSkillEntry** — skill name, format, content ref and digest,
  provenance, required Skills and capabilities, publication and side-effect
  metadata, terminal and implementation contracts, and selection reason.
- **RuntimeSkillMaterialization** — runtime id, materialization mode (§13),
  workspace paths, and prompt-index or retrieval manifest refs.

---

## 9. Skill Step Authoring Contract

### 9.1 Shape

Illustrative Skill step:

```json
{
  "id": "implement-issue",
  "title": "Implement Jira issue",
  "type": "skill",
  "skill": {
    "id": "code.implementation",
    "inputs": {
      "repository": "MoonLadderStudios/MoonMind",
      "issueKey": "PROJ-123",
      "instructions": "Implement the issue and prepare a pull request."
    },
    "skillSets": ["deployment-default", "repo-default"],
    "include": [{ "name": "repo-coding-conventions" }],
    "exclude": ["legacy-reviewer"],
    "allowedTools": ["repo.apply_patch", "sandbox.run_tests"],
    "runtime": {
      "mode": "codex_cli",
      "materializationMode": "hybrid"
    }
  }
}
```

Whatever the exact field nesting, Skill inputs validate against the Skill
contract, content selection resolves through the Agent Skill System (not the
Tool registry), allowed Tools are governed separately from Skill identity, and
runtime preferences are validated against compatibility and policy.

### 9.2 Skill Step vs Agent Skill Bundle

A Skill step may select one primary `AgentSkillDefinition`, supporting
SkillSets, explicit includes and excludes, and runtime and Tool policy. The
primary Skill describes agent behavior; the resolved snapshot describes the
exact instruction bundles and supporting content made active for the runtime.

### 9.2.1 Required Supporting Skills

A Skill may declare other top-level Skills that must be active when it is
selected. This is expressed with the Agent Skills-compatible `metadata` field:

```yaml
metadata:
  required-skills: "fix-comments fix-ci fix-merge-conflicts"
```

The value is a whitespace-delimited list of valid Agent Skills names.
Resolution expands requirements transitively before runtime launch, and fails
before launch if a required Skill is missing, forbidden by policy, or explicitly
excluded. Required Skills remain independently callable top-level Skills.

### 9.3 Workflow-level Skill Intent

A workflow execution may define baseline Skill intent inherited by Skill steps,
carried on the `task` payload node as `task.skills` with the same `sets`,
`include`, `exclude`, and `materializationMode` fields as step-level selection.

### 9.4 Inheritance and Validation

`task.skills` defines the workflow-wide baseline. `step.skill` may add,
override, or exclude workflow-level intent, and a step with no step-level
selectors inherits the baseline. A step may opt out of inherited optional
Skills where policy allows; policy may also require mandatory Skills that
cannot be excluded.

A Skill step is valid only when the selected Skill and requested names
resolve, inputs validate against the Skill's input contract, the target runtime
is compatible, required repository/project/artifact context is present,
selected Tools are allowed and available, approval and autonomy settings are
enforceable, policy allows the selected source kinds, and resolution can
produce an immutable `ResolvedSkillSet`.

---

## 10. Resolution Lifecycle

### 10.1 Timing and Algorithm

Skill resolution happens before runtime launch: at workflow submission, during
step preparation, or in a dedicated resolution activity. Workflow code does not
resolve large Skill bodies inline, because those bodies would then enter
workflow history.

Resolution collects workflow- and step-level selectors, identifies the target
runtime and execution mode, loads deployment policy and the allowed source
kinds, loads candidates from built-in, deployment, repo, and local sources,
applies precedence and collision rules, validates runtime compatibility and
Tool policy, records the selected content refs and digests, writes a resolved
manifest artifact (and a compact prompt index when needed), and returns a
compact `ResolvedSkillSet` ref.

### 10.2 Failure Behavior

Resolution fails before runtime launch when a required or requested Skill
cannot be resolved, collisions cannot be resolved deterministically, policy
forbids a requested source or override, a Skill is incompatible with the
runtime and no allowed fallback exists, source content cannot be read, a body
or manifest digest does not match, or a selected Tool is not allowed for the
Skill step.

Checked-in skill sources are plain files. Externally sourced skills (for
example the MoonSpec bundle) are vendored into `.agents/skills` as real files
by their sync tool, so resolution never depends on submodule checkout state or
filesystem symlink support. Resolution fails with an actionable error when
required source content cannot be read.

### 10.3 Provenance

The resolved snapshot records which Skills and content evidence were selected,
where each came from, which selectors and policies influenced the result,
which Skills were inherited or excluded, and which materialization mode was
selected.

---

## 11. Content Evidence, Immutability, and Replay

Agent Skill content refs and digests are immutable; changing content creates
new evidence under the same canonical Skill name. A `ResolvedSkillSet` is also
immutable: a run or step carries an exact snapshot ref, retries and
continue-as-new reuse it, reruns reuse the original by default, and
re-resolution is explicit and creates a new snapshot.

Full Skill bodies, large bundles, resolved manifests, prompt indexes, source
traces, and materialization bundles live in artifacts or equivalent blob
storage. Workflow history carries compact refs and bounded metadata only.

---

## 12. Workspace Path Policy

### 12.1 Visible Path

For managed runtimes, the active resolved Skill set is visible at the
materialized `visiblePath`, a MoonMind-owned run-scoped directory.
`.agents/skills` is the repo-authored source path and, when it can be installed
safely, a compatibility alias for the active set (§14.4).

### 12.2 Local Overlay and Active Set

`.agents/skills/local` is a local-only source input, not the active runtime
projection. The runtime-visible active tree is a projection of the resolved active snapshot, not a mutable merge of checked-in repo folders.

### 12.3 Mutation Rule

MoonMind does not rewrite checked-in user-authored Skill files in place during run materialization. Repo Skill folders are inputs to resolution; the runtime active tree is produced from the resolved snapshot.

One exception exists in `moonmind/services/skill_materialization.py`: when the
workspace already has a repo-authored `.agents/skills` directory, the
materializer adds a `_shared` support entry (symlink or copy) inside it if one
is not already present.

---

## 13. Runtime Materialization Modes

The system supports several delivery modes because runtimes differ:

| Mode | Delivery | Fits |
| --- | --- | --- |
| `prompt_bundled` | Skill content or compact summaries inline in the instruction payload | small Skill sets, external agents without filesystem access, prompt-only providers |
| `workspace_mounted` | full Skill content materialized into workspace files | managed coding runtimes, larger bundles, file-aware agents |
| `hybrid` | compact activation summary or prompt index inline, full content on disk | **default** for managed coding runtimes |
| `retrieval` | a manifest or helper path for on-demand retrieval | large catalogs or advanced adapters; not the default |

---

## 14. Managed Runtime Injection Model

For managed coding runtimes, MoonMind uses one injection model: a compact
activation summary inline, plus a read-only full active Skill bundle at the
materialized `visiblePath`, backed by a run-scoped MoonMind-owned store.

### 14.1 Managed Runtime Sequence

Before execution begins, MoonMind resolves Skill intent into an immutable
`ResolvedSkillSet`, materializes the full active bundle into a run-scoped
backing store, exposes it at the materialized `visiblePath` (aliased at
`.agents/skills` when safe), includes a compact activation summary in the
runtime instructions, and launches the runtime with refs to the snapshot and
materialization metadata.

### 14.2 Active Backing Store

The backing store is MoonMind-owned (not repo-author-owned), run-scoped,
read-only to the runtime where practical, corresponds exactly to one resolved
snapshot, and contains only the selected Skills and MoonMind-owned metadata. A
representative path is
`/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>/`.

Snapshot IDs and resolved Skill names are single filesystem components, never
paths. Artifact admission and materialization reject empty, dot, traversal,
absolute, or separator-bearing identities before filesystem changes. Temporal
workflow/activity IDs containing separators produce a deterministic hashed
snapshot ID; ordinary safe IDs remain stable. Backing-store preparation rejects
symlinked directory parents rather than following them outside the run root.

### 14.3 Runtime-Visible Shape

The active tree looks like:

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

`SKILL.md` is the primary Skill entrypoint and `_manifest.json` is
MoonMind-owned metadata. Only selected Skills appear; unselected repo Skills do
not. A single-Skill run still projects the full active root.

### 14.4 Projection Mechanics

MoonMind materializes the active backing store in a MoonMind-owned run-scoped
path. It may expose that store at `.agents/skills` only when doing so cannot
mask repo-authored skill source files. Otherwise the runtime activation summary
points to the actual run-scoped active path. The mechanism (bind mount,
symlink, overlay view, copy-on-write staging, or adapter-specific projection)
is not the contract. The contract is that the runtime sees exactly the resolved
snapshot at the materialized `visiblePath` and is not expected to discover
Skills anywhere else; `.agents/skills` is an optional compatibility alias, and
projection fails before runtime launch if a requested alias cannot be installed
safely.

#### 14.4.1 Projection Strategy Order

Managed runtime adapters choose the first projection strategy that preserves
the publishable repository workspace without changing checked-in Skill source
files:

1. **Runtime namespace projection**: bind mount or overlay the active backing
   store at `.agents/skills` inside the launched runtime container, PTY, or
   process namespace. This is preferred because the runtime sees the canonical
   path while the repo checkout remains unchanged.
2. **Run-scoped visible path**: instruct the agent to read the active backing
   store from the MoonMind-owned materialized `visiblePath` when a repo-authored
   `.agents/skills` directory exists in the publishable checkout.
3. **Isolated execution workspace**: run the agent in a prepared staging
   workspace where `.agents/skills` can be owned by MoonMind, while publishing
   code changes back to the canonical checkout through controlled sync rules.
4. **Symlink projection**: create or update `.agents/skills` only when that path
   is absent, already a MoonMind-owned projection link, or the workspace is an
   explicitly disposable non-publishing clone.
5. **Transactional preserve-and-link fallback**: this is a disposable-workspace
   fallback only, not the normal managed runtime path. It creates a projection
   lease, moves the source only after active materialization succeeds, restores
   the original tree in a `finally` path before verification, publish, or
   workflow completion, and excludes projection-only changes from publication.

Adapters do not replace a tracked or repo-authored `.agents/skills` directory
with a symlink in the publishable checkout, because that would publish deletion
or symlink changes for repo Skill sources. Any transient fallback is runtime
state, not user-authored work.

### 14.5 Inline Activation Summary

Every managed runtime Skill step includes a compact activation block naming the
active Skills, the actual materialized `visiblePath`, first-read hints, hard
rules to apply immediately, and optional Tool or autonomy constraints. Example:

```text
Active MoonMind skills for this step:
- code-implementation: implement the requested change and prepare reviewable output
- repo-coding-conventions: follow repository style and contribution rules

Full skill content is available under /work/agent_jobs/job_123/runtime/skills_active/rss_123.
Read /work/agent_jobs/job_123/runtime/skills_active/rss_123/code-implementation/SKILL.md before preparing the plan.

Hard rules:
- do not broaden scope beyond the requested issue
```

The activation summary is not a duplicate full-body Skill bundle. It protects the
immutable active snapshot and its helper selection, not repository source from
requested authoring. Agents may read and edit checked-in Skill source when the
task requires it, following the repository's source and projection ownership.
Materialization preserves those sources, and source edits do not replace the
snapshot used by the current run. Externally owned bundles still need their own
publication authority, so a consumer change does not reference an unpublished
submodule commit.

### 14.6 Active Manifest

Each active projection includes `_manifest.json`, for example:

```json
{
  "snapshot_id": "rss_123",
  "visible_path": "/work/agent_jobs/job_123/runtime/skills_active/rss_123",
  "read_only": true,
  "skills": [
    {
      "name": "code-implementation",
      "source_kind": "deployment",
      "content_ref": "art:sha256:...",
      "content_digest": "sha256:..."
    }
  ]
}
```

The manifest is for observability and adapter introspection; the agent primarily reads `SKILL.md`.

### 14.7 Collision Policy

MoonMind owns the runtime-visible active `visiblePath` for the duration of the run.

A collision exists when MoonMind cannot safely expose the active root through a
requested compatibility alias such as `.agents/skills`. A checked-in
`.agents/skills` directory is not by itself a collision; it is a normal
source-input shape, and becomes one only if the selected adapter has no safe
way to expose the active snapshot without mutating that directory in the
publishable workspace.

#### 14.7.1 Collision Decision Table

| Existing path | Handling | Terminal? |
| --- | --- | --- |
| `.agents` missing | Create adapter parent and project active root. | No |
| `.agents` is a directory and `.agents/skills` missing | Project active root. | No |
| `.agents/skills` is a MoonMind-owned symlink to the same backing store | Reuse it. | No |
| `.agents/skills` is a MoonMind-owned stale symlink | Replace it with the current active projection and record the replacement. | No |
| `.agents/skills` is a checked-in directory | Leave it untouched and use the MoonMind-owned run-scoped active `visiblePath`; use transactional preserve-and-link only in an explicitly disposable workspace. | No |
| `.agents` is a file | Fail before runtime launch with actionable diagnostics. | Yes |
| `.agents/skills` is a file | Fail before runtime launch with actionable diagnostics. | Yes |
| `.agents/skills` is an external or unknown symlink | Replace only if ownership can be proven; otherwise fail before runtime launch. | Conditional |
| Filesystem or adapter cannot mount, overlay, stage, or link safely | Fail before runtime launch with actionable diagnostics. | Yes |

Projection failures report the conflicting path, conflicting object kind,
attempted projection action, selected strategy, and remediation guidance.

#### 14.7.2 Invariants Checked Before Launch

Beyond the contract in §14.4: repo-authored Skill input directories are not
lost or published as deletions or symlinks (a transient preserve-and-link
fallback keeps a run-scoped preserved copy, restores on projection failure, and
is excluded from publication); the runtime is never pointed at an alternate
visible path as a silent fallback; and the projection decision and any
collision remediation are recorded in runtime metadata or diagnostics.

The projection is verified inside the runtime, not only on the worker that
materialized it, because a container runtime can be attached to a directory
that exists and is readable yet holds none of the projection. The
runtime-side check reads `_manifest.json`, and a failed check names which
invariant failed rather than reporting one undifferentiated error.

### 14.8 Adapter Responsibilities

A managed runtime adapter consumes a pinned resolved snapshot ref, materializes
the active backing store once per snapshot where practical, exposes it at the
`visiblePath`, injects the activation summary, and preserves snapshot identity
in logs, artifacts, and observability metadata. It does not scan the repo for
additional Skills during execution, re-resolve sources, add unselected Skills,
silently fall back to a different visible path, treat runtime commands as
Skills, or mutate the active bundle as a way of changing policy. Publication
excludes runtime projection artifacts, so changes caused only by Skill
projection are never committed.

---

## 15. External Agent Skill Delivery

External agents may lack managed-runtime filesystem semantics, so they receive
resolved Skill context through compact prompt bundles, uploaded bundle
artifacts, presigned manifest or bundle URLs, provider-specific translations,
or summaries plus retrieval links. External adapters still consume the
immutable resolved snapshot without re-resolving or broadening it, preserve
provenance, keep full Skill bodies out of workflow history, and map delivery
metadata into canonical `AgentExecutionRequest` fields or adapter metadata.

---

## 16. Interaction with `MoonMind.AgentRun`

A Skill step that requires true agent execution compiles into an agent runtime
execution path. `MoonMind.UserWorkflow` owns orchestration and step order and
resolves Skills before launch; it passes compact refs to `MoonMind.AgentRun`,
which consumes an `AgentExecutionRequest`; the runtime adapter materializes or
translates the snapshot so the agent sees a stable Skill view; final outputs are
stored as artifacts and structured results. The request carries skill context as refs (resolved skill set, materialization
mode, prompt index, manifest, policy summary). `MoonMind.AgentRun` does not
independently re-resolve Skill sources.

---

## 17. Tool Access Inside Skill Steps

A Skill step may need Tools (read issue details, apply patches, run tests,
create a pull request). Tool access is governed separately from Skill identity:
selecting a Skill does not grant all Tools; allowed Tools are declared,
inherited, or policy-derived; and Tool use remains subject to authorization,
worker capability, security, and approval rules. A Tool invoked by an agent is
still a Tool with ordinary Tool outputs and artifact refs, and the choice of
which Tool to use belongs to the Skill step/runtime policy, not the Tool
registry.

Skill side-effect metadata may contribute a normalized Required Capability
without becoming an authorization grant itself. Specifically,
`sideEffect.kind: enqueue_children` derives `execution.fanout` during backend
normalization. Policy authorizes and materializes the scoped runtime
capability before launch; the Skill author and operator do not manage a separate
permission checkbox. See
[Required Capabilities](../Workflows/RequiredCapabilities.md#75-executionfanout).

GitHub action requirements are distinct from tool readiness. Skill-owned Auto
publication involving a GitHub source, `gh`, or explicit GitHub action metadata
declares `metadata.publish.githubOperations`; other GitHub effects
may declare `metadata.sideEffect.githubOperations`. These lists use the existing
repository operation vocabulary (`read`, `write`, `branch_write`, `lock`,
`review_request`, `merge_request`). Generic agent requests use
`parameters.githubOperations`. Requirements compose with resolved Skill metadata;
an empty or narrower caller list cannot remove a Skill's required operation.
Neither declaration creates or expands a connection grant. Auto remains
provider-neutral: a local or other-provider publisher with no GitHub intent
neither needs these declarations nor acquires GitHub authority. Explicit GitHub
metadata and malformed signals still require validation in every context.

Resolved Skill entries preserve publication and side-effect metadata from the
selected content, including deployment artifacts. Run forwards it under a replay
gate. The known `merge_pull_request` side effect additionally requires
`merge_request` for the Skill's `finishMode: merge` (including its documented
default), and does not require it for `fix_only`. Invalid or contradictory
metadata and undeclared mutation intent are rejected before binding a host.
`enqueue_children` alone does not require a GitHub write. Older GitHub-relevant
Auto snapshots without enough action metadata require a corrected request; they
never receive inferred broader permissions. Retained work remains available.

Generic Omnigent plans compose the selected resolved Skill metadata and authored
action declarations through this same canonical reader before freezing
collaboration authority. At credential use, the current agent's required actions
have to remain within that frozen snapshot, and the current connection and
assignment have to still admit its full operation set. Source reads, trusted native review,
and publisher destination operations retain their distinct roles. Conflicting
workspace and runtime repository projections fail before credentials or host
mutation; equivalent GitHub slug/URL spellings retain one canonical target for
preflight and publication.


---

## 18. Security and Trust

Repo and local-only Skills are potentially untrusted input; living in a
workspace does not make Skill content safe. Skill bodies are not a place for
secrets: MoonMind does not log full Skill bodies by default, materialize raw
credentials into Skill manifests, or pass secrets through Skill content.
Deployment policy gates Skills as described in §7.2. Managed runtime active
bundles are read-only where practical, and the active bundle is not a place to
change policy or record state.

---

## 19. Observability and Dashboard

The dashboard treats Skill context as first-class execution context. At submit
time, operators can see or select the primary Skill, SkillSets, explicit
includes and excludes, materialization mode, whether repo and local overlays
are enabled, Tool availability, and runtime compatibility warnings. Workflow and
step detail shows the Skill step identity, resolved snapshot ID, selected
Skill names and content evidence, source provenance, materialization mode,
active visible path, backing store summary, activation summary, manifest or
prompt-index artifact links, and projection failures. Advanced views may expose
raw `resolved_skillset_ref` and manifest refs, source traces, policy summaries,
materialization metadata, and adapter-specific compatibility links.

---

## 20. Schedule, Rerun, and Replay Semantics

Scheduled work stores Skill intent at schedule creation and resolves the actual
`ResolvedSkillSet` when each run starts, unless the schedule explicitly carries
a resolved snapshot ref; snapshot reuse is an explicit mode, not the silent
default. Reruns reuse the original `ResolvedSkillSet` and do not silently
re-resolve "latest" Skills; re-resolution requires explicit operator intent.
Retries and continue-as-new preserve the same snapshot unless a new resolution
is explicitly requested.
