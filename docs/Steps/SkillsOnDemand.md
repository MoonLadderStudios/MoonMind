# Skills On Demand

Status: Desired State
Owners: MoonMind Engineering
Last Updated: 2026-05-06
Canonical for: managed-agent requests for additional agent skills during execution
Related: `docs/Steps/SkillSystem.md`, `docs/Steps/StepTypes.md`, `docs/Tasks/SkillAndPlanContracts.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`

---

## 1. Purpose

This document defines the desired-state **Skills On Demand** extension to the MoonMind Agent Skill System.

The normal skill flow remains:

1. task and step intent selects the Skills MoonMind already knows are needed;
2. MoonMind resolves that intent into an immutable `ResolvedSkillSet` before runtime launch;
3. workflows and runtime requests carry compact refs to the resolved snapshot;
4. managed runtimes receive a compact activation summary and an active read-only projection at `.agents/skills`.

Skills On Demand adds one controlled capability:

> When the feature is enabled, a managed agent may ask MoonMind for additional Skill help during execution. MoonMind, not the agent, decides whether the request is allowed and produces any new resolved snapshot.

This is intended for cases where the initial workflow can predict the primary Skill, but the agent discovers during execution that another reusable instruction bundle would help.

Examples:

- a code implementation agent discovers the repo uses an unfamiliar framework and asks for a framework-specific review Skill;
- a docs-writing Skill asks whether a deployment-managed API-writing Skill exists;
- a PR fixer asks for an available CI-failure-triage Skill after reading failing test logs;
- a repo migration task asks for a repository-specific migration convention Skill that was not selected up front.

---

## 2. Relationship to `SkillSystem.md`

`docs/Steps/SkillSystem.md` remains canonical for:

1. what a Skill is;
2. how Skills differ from Tools;
3. source precedence;
4. policy gates;
5. immutable `ResolvedSkillSet` snapshots;
6. runtime materialization under `.agents/skills`.

This document defines an extension point that must preserve those invariants.

The managed agent does **not** directly fetch arbitrary Skill bodies, scan hidden catalogs, mutate `.agents/skills`, or re-resolve Skills locally.

Instead:

```text
agent asks -> MoonMind validates -> MoonMind resolves -> MoonMind materializes -> agent receives updated activation context
```

A runtime-side request is therefore an input to MoonMind resolution, not an alternative resolution system.

---

## 3. Desired-State Summary

Skills On Demand is controlled by a single global feature flag.

When disabled:

1. managed runtimes must not expose Skills On Demand commands;
2. any attempted Skills On Demand call must fail with `feature_disabled`;
3. no Skill catalog query results are returned to the agent;
4. no additional Skill snapshot is created from a runtime request.

When enabled:

1. managed agents may query MoonMind for Skill metadata;
2. managed agents may request that MoonMind add one or more Skills to the active execution context;
3. MoonMind applies the same source, version, runtime, and policy rules used by normal resolution;
4. approved requests create a new immutable resolved snapshot rather than mutating the previous one;
5. the runtime receives a compact activation update and, where supported, an updated `.agents/skills` projection;
6. all requests, denials, approvals, snapshot transitions, and materialization outcomes are auditable.

---

## 4. Feature Flag

### 4.1 Global flag

The first implementation uses one global boolean flag.

Suggested setting:

```py
skills_on_demand_enabled: bool = Field(
    False,
    validation_alias=AliasChoices(
        "MOONMIND_SKILLS_ON_DEMAND_ENABLED",
        "WORKFLOW_SKILLS_ON_DEMAND_ENABLED",
    ),
    description="Enable managed-agent Skills On Demand query/request capabilities.",
)
```

Suggested environment variable:

```text
MOONMIND_SKILLS_ON_DEMAND_ENABLED=false
```

Default should be `false` until the feature is intentionally enabled for a deployment.

### 4.2 Scope of the flag

The flag controls whether the functionality can be called at all.

If `false`, MoonMind treats both query and request calls as unavailable.

If `true`, MoonMind may expose the controlled commands to supported managed runtimes, but normal Skill resolution policy still applies.

This first version does not define per-user, per-workspace, per-skill, per-source, or per-runtime fetchability settings. Those may be added later without changing the basic request lifecycle.

### 4.3 Interaction with existing policy

The global flag is a feature gate, not a replacement for existing policy.

Even when enabled, MoonMind must still enforce:

1. source-kind restrictions;
2. repo Skill and local-only Skill permissions;
3. Skill allowlists or policy mode where configured;
4. runtime compatibility;
5. version pins;
6. selected Tool policy;
7. autonomy and approval policy;
8. checksum and artifact validation.

---

## 5. Terminology

### 5.1 Initial Skill Snapshot

The immutable `ResolvedSkillSet` selected before runtime launch through the normal Skill System.

### 5.2 On-Demand Skill Query

A managed-runtime request for metadata about available Skills.

Queries may return names, titles, descriptions, versions, source kind, tags, supported runtimes, and eligibility summaries.

Queries must not return full Skill bodies.

### 5.3 On-Demand Skill Request

A managed-runtime request asking MoonMind to add one or more Skills to the active execution context.

The request is not itself approval. MoonMind must validate and resolve it.

### 5.4 Derived Skill Snapshot

A new immutable `ResolvedSkillSet` produced from an approved on-demand request.

It should record the parent snapshot, requested Skills, requesting runtime, reason, policy result, and resulting active Skills.

### 5.5 Skills On Demand Command

A runtime-facing MoonMind control command exposed to a managed agent.

Suggested commands:

```text
moonmind.skills.query
moonmind.skills.request
```

These commands are not Agent Skills. They are not `AgentSkillDefinition` records. They must not appear inside a `ResolvedSkillSet` as active Skills.

---

## 6. Core Invariants

1. Skills On Demand is unavailable unless the global flag is enabled.
2. A managed agent may request Skills, but MoonMind resolves Skills.
3. The agent must not directly read from hidden Skill catalogs or bypass resolution.
4. Query results expose metadata only, not full Skill bodies.
5. Fetch/request results expose refs, activation summaries, and materialization status, not ungoverned bodies.
6. Existing snapshots are immutable.
7. Approved on-demand additions create a derived snapshot.
8. Source policy continues to apply.
9. Local-only Skills must not bypass deployment policy.
10. `.agents/skills` remains MoonMind-owned runtime projection state.
11. Runtime adapters must not independently broaden the active Skill set.
12. Workflow history carries compact refs, not large Skill bodies.
13. All on-demand requests must be auditable.
14. A denied on-demand request must not change the active snapshot.
15. A materialization failure must not partially activate a new Skill set.

---

## 7. User-Facing Behavior

### 7.1 When disabled

The runtime activation summary may include no mention of Skills On Demand, or it may explicitly say:

```text
Skills On Demand is disabled for this run. Use only the active Skills already available under .agents/skills.
```

The runtime must not expose the `moonmind.skills.query` or `moonmind.skills.request` command where command exposure is controllable.

If a runtime cannot hide commands and a call is attempted, MoonMind returns:

```json
{
  "status": "denied",
  "code": "feature_disabled",
  "message": "Skills On Demand is disabled for this deployment."
}
```

### 7.2 When enabled

The activation summary should include a compact note:

```text
Skills On Demand is enabled.
You may ask MoonMind for additional Skill metadata or request additional Skills when needed.
MoonMind must approve and resolve any requested Skill before it becomes active.
```

The note must also remind the agent that active Skill bodies remain under `.agents/skills` and that full Skill bodies should not be copied into responses unless specifically needed.

---

## 8. Runtime Commands

### 8.1 `moonmind.skills.query`

Purpose: discover whether MoonMind knows about relevant Skills.

Inputs:

```ts
type SkillsOnDemandQuery = {
  query: string;
  runtime_id?: string;
  current_snapshot_ref?: string;
  max_results?: number;
};
```

Output:

```ts
type SkillsOnDemandQueryResult = {
  status: "ok" | "denied";
  code?: string;
  results: SkillCatalogSearchResult[];
};

type SkillCatalogSearchResult = {
  name: string;
  title?: string;
  description?: string;
  latest_version?: string;
  source_kind: "built_in" | "deployment" | "repo" | "local";
  supported_runtimes?: string[];
  eligible: boolean;
  in_current_snapshot: boolean;
  eligibility_summary?: string;
};
```

Rules:

1. query returns metadata only;
2. results should be filtered to policy-eligible candidates where practical;
3. ineligible matches may be returned only when useful for diagnostics and must include `eligible: false`;
4. query must not return content refs that permit direct body reads;
5. query must be bounded and safe for workflow/activity payloads.

### 8.2 `moonmind.skills.request`

Purpose: ask MoonMind to add one or more Skills to the active runtime context.

Inputs:

```ts
type SkillsOnDemandRequest = {
  current_snapshot_ref: string;
  requested_skills: {
    name: string;
    version?: string;
  }[];
  reason?: string;
  runtime_id?: string;
  step_id?: string;
};
```

Output:

```ts
type SkillsOnDemandRequestResult = {
  status: "activated" | "denied" | "requires_approval" | "no_change";
  code?: string;
  message?: string;
  parent_snapshot_ref?: string;
  resolved_skillset_ref?: string;
  snapshot_id?: string;
  activation_summary?: string;
  materialization?: {
    mode: "prompt_bundled" | "workspace_mounted" | "hybrid" | "retrieval";
    visible_path?: string;
    manifest_ref?: string;
  };
};
```

Rules:

1. requested Skills are treated as selector intent;
2. MoonMind must not trust the request without policy validation;
3. approval may be added later, but the v1 feature may return only `activated`, `denied`, or `no_change`;
4. `requires_approval` is reserved for future use;
5. if every requested Skill is already active, return `no_change`;
6. if resolution succeeds, return compact refs and activation instructions;
7. if resolution fails, return a denial or structured error without changing the active snapshot.

---

## 9. Resolution Lifecycle

### 9.1 Initial launch

Before runtime launch, MoonMind follows normal Skill resolution:

1. collect task-level and step-level Skill selectors;
2. resolve the initial `ResolvedSkillSet`;
3. persist the manifest and Skill body artifacts;
4. materialize the active bundle;
5. launch the managed runtime with the active snapshot ref.

### 9.2 On-demand query

When the agent calls `moonmind.skills.query`:

1. MoonMind checks the global feature flag;
2. MoonMind identifies the current deployment, runtime, step, and active snapshot;
3. MoonMind searches eligible Skill metadata across allowed sources;
4. MoonMind returns bounded metadata results;
5. MoonMind records the query for observability.

### 9.3 On-demand request

When the agent calls `moonmind.skills.request`:

1. MoonMind checks the global feature flag;
2. MoonMind validates the request shape;
3. MoonMind loads the current active snapshot by ref;
4. MoonMind computes a new selector containing the currently active Skills plus requested additions;
5. MoonMind applies normal source, version, runtime, and policy gates;
6. MoonMind creates a derived immutable `ResolvedSkillSet` if the request is allowed;
7. MoonMind persists the derived manifest;
8. MoonMind materializes the derived active bundle;
9. MoonMind updates the managed runtime using the adapter's supported refresh mechanism;
10. MoonMind returns a compact activation summary to the agent.

### 9.4 Snapshot lineage

Derived snapshots should preserve lineage.

Suggested fields to add to `ResolvedSkillSet` or its manifest metadata:

```ts
type ResolvedSkillSetLineage = {
  parent_snapshot_id?: string;
  parent_manifest_ref?: string;
  created_by: "initial_resolution" | "skills_on_demand" | "operator_reresolution";
  requested_by?: "managed_agent" | "operator" | "system";
  request_reason?: string;
  requested_skills?: string[];
};
```

Lineage may live in the manifest artifact or `source_trace_ref` rather than in workflow history if the payload is large.

---

## 10. Materialization and Runtime Refresh

### 10.1 Preferred behavior

A derived snapshot should be fully materialized before the runtime is told it is active.

The runtime must never see a partially written `.agents/skills` tree.

Preferred sequence:

1. materialize derived snapshot into a new run-scoped backing store;
2. verify manifest and checksums;
3. atomically switch the runtime-visible projection where supported;
4. send the activation update to the managed agent.

### 10.2 First implementation behavior

The first implementation may avoid live mid-turn projection mutation.

Acceptable v1 behavior:

1. receive on-demand request;
2. resolve and materialize a derived snapshot;
3. send the updated activation summary on the next managed-session turn or controlled steer point;
4. require the agent to read newly active Skill files after the activation update.

This avoids races where the agent is reading `.agents/skills` while MoonMind changes the projection.

### 10.3 Retrieval mode

A future implementation may use `retrieval` materialization for very large Skill catalogs or advanced adapters.

Even in retrieval mode, the agent requests through MoonMind and receives governed refs or helper access. It must not be granted direct unrestricted catalog access.

---

## 11. External Agents

Skills On Demand is initially scoped to managed runtimes.

External agents may support it later only if the adapter can provide:

1. authenticated MoonMind-mediated control calls;
2. bounded metadata query results;
3. immutable snapshot refs;
4. governed materialization or prompt-bundled updates;
5. audit events equivalent to managed runtimes.

Until then, external agents use the initial resolved snapshot only.

---

## 12. Failure Behavior

Skills On Demand fails without changing the active snapshot when:

1. the global feature flag is disabled;
2. the command is called by an unsupported runtime;
3. the current snapshot ref is missing or invalid;
4. a requested Skill does not exist;
5. a pinned version cannot be resolved;
6. source policy forbids the requested Skill;
7. runtime compatibility validation fails;
8. a selected Skill would require disallowed Tools;
9. artifact reads or checksums fail;
10. materialization fails;
11. the runtime refresh mechanism fails.

Failure responses should include:

```ts
type SkillsOnDemandFailure = {
  status: "denied";
  code: string;
  message: string;
  current_snapshot_ref?: string;
  diagnostics_ref?: string;
};
```

Suggested codes:

```text
feature_disabled
unsupported_runtime
invalid_request
snapshot_not_found
skill_not_found
version_not_found
policy_denied
runtime_incompatible
tool_policy_denied
artifact_unavailable
checksum_mismatch
materialization_failed
runtime_refresh_failed
```

---

## 13. Observability and Audit

MoonMind should record one audit/observability event for each query and request.

Minimum query event fields:

```ts
type SkillsOnDemandQueryEvent = {
  event_type: "skills_on_demand.query";
  workflow_id: string;
  run_id?: string;
  step_id?: string;
  runtime_id?: string;
  current_snapshot_id?: string;
  query_hash: string;
  result_count: number;
  denied: boolean;
  denial_code?: string;
};
```

Minimum request event fields:

```ts
type SkillsOnDemandRequestEvent = {
  event_type: "skills_on_demand.request";
  workflow_id: string;
  run_id?: string;
  step_id?: string;
  runtime_id?: string;
  parent_snapshot_id?: string;
  requested_skills: string[];
  result: "activated" | "denied" | "requires_approval" | "no_change";
  result_code?: string;
  derived_snapshot_id?: string;
  manifest_ref?: string;
  diagnostics_ref?: string;
};
```

Do not store raw long natural-language query text in high-cardinality metrics. Store a hash in metrics and place detailed diagnostics in an artifact if needed.

---

## 14. Security Rules

1. Skills On Demand must not expose secrets.
2. Skill bodies must not be treated as secret storage.
3. Query results must not reveal hidden content bodies.
4. The managed agent must not receive direct database or artifact read capability for arbitrary Skill refs.
5. The request path must enforce the same policy as initial resolution.
6. Local-only Skills remain subject to deployment policy.
7. Repo Skills remain source inputs, not mutable runtime state.
8. Runtime refresh must not publish `.agents/skills` projection changes as repo-authored changes.
9. Denied requests must be visible enough for operators to understand why they were denied.

---

## 15. Implementation Notes

The current architecture already contains most of the required primitives:

1. `SkillSelector` models selector intent.
2. `AgentSkillResolver` resolves selector intent across built-in, deployment, repo, and local loaders.
3. `ResolvedSkillSet` models immutable active Skill snapshots.
4. `agent_skill.resolve` persists file-backed Skill content and resolved manifests.
5. `AgentSkillMaterializer` materializes a resolved snapshot for managed runtimes.
6. `AgentExecutionRequest.resolvedSkillsetRef` carries a compact snapshot ref into runtime execution.

Suggested implementation steps:

1. Add `skills_on_demand_enabled` to workflow or deployment settings.
2. Add schemas for query and request payloads.
3. Add activities:
   - `agent_skill.query_catalog`
   - `agent_skill.request_on_demand`
4. Register those activities in the Temporal activity catalog.
5. Add a managed-runtime control surface for `moonmind.skills.query` and `moonmind.skills.request`.
6. Update runtime instruction preparation to mention availability only when enabled.
7. Add derived snapshot lineage metadata.
8. Add materialization refresh support or a next-turn activation fallback.
9. Add unit tests for disabled, denied, no-change, activated, and materialization-failure paths.

The v1 implementation should not add per-skill fetchability, per-user permissions, or approval workflows unless required by another feature. The single global flag is the only new feature gate defined here.

---

## 16. Test Cases

### 16.1 Disabled feature

Given `MOONMIND_SKILLS_ON_DEMAND_ENABLED=false`, when a managed agent calls `moonmind.skills.query` or `moonmind.skills.request`, MoonMind returns `feature_disabled` and no snapshot is created.

### 16.2 Query metadata

Given the feature is enabled, when a managed agent queries for `python testing`, MoonMind returns bounded Skill metadata and no Skill bodies.

### 16.3 Request already active Skill

Given the requested Skill is already present in the active snapshot, MoonMind returns `no_change` and keeps the current snapshot ref.

### 16.4 Request allowed Skill

Given the feature is enabled and the requested Skill is policy-eligible, MoonMind creates a derived snapshot, materializes it, and returns an activation summary.

### 16.5 Request denied by policy

Given the feature is enabled but source or allowlist policy forbids the requested Skill, MoonMind returns `policy_denied` and keeps the current snapshot active.

### 16.6 Materialization failure

Given resolution succeeds but materialization fails, MoonMind returns `materialization_failed`, records diagnostics, and keeps the current snapshot active.

---

## 17. Future Extensions

Future versions may add:

1. per-skill `queryable` and `fetchable` metadata;
2. source-kind-specific query and fetch permissions;
3. per-user or per-workspace policy;
4. approval-required Skills;
5. cost or risk budgets for autonomous fetches;
6. semantic catalog search;
7. retrieval-mode Skill serving;
8. external-agent support;
9. UI controls for approving, denying, and inspecting on-demand requests.

These should extend the same core lifecycle rather than allowing agents to fetch Skill content directly.
