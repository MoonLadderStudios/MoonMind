# Managed and External Agent Execution Model

## Hybrid runtime capability ownership

An admitted runtime capability snapshot freezes three independent planes: canonical
agent identity and session continuity, repository workspace ownership, and host
realization. For profile-bound Omnigent, identity remains `external/omnigent` with
the `codex-native` harness. Omnigent owns session reattach evidence; MoonMind owns
the sandbox `WorkspaceLocator`, repository checkpoint capture, validation, and
restore. An `external_state_ref` proves session continuity only and cannot satisfy
workspace restore preflight. Recovery uses the versioned, digested snapshot admitted
with the run rather than rewriting it after registry or credential changes.

**Document Class:** Canonical declarative  
**Status:** Current  
**Owners:** MoonMind Platform  
**Last updated:** 2026-07-18  
**Authority:** Unified Temporal lifecycle and ownership model for true agent execution, including profile-bound Codex execution through Omnigent hosts

Implementation progress belongs in the roadmap, issues, and pull requests. This document defines durable product and runtime contracts.

The normal Workflow Create compilation and acceptance journey for **Codex via Omnigent** is specified by [`docs/Omnigent/CodexCreateToHostContract.md`](../Omnigent/CodexCreateToHostContract.md); this execution model remains authoritative for the shared runtime lifecycle.

## Related documents

- [`docs/Temporal/WorkflowExecutionProductModel.md`](./WorkflowExecutionProductModel.md)
- [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](./ActivityCatalogAndWorkerTopology.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](./WorkflowArtifactSystemDesign.md)
- [`docs/Temporal/ErrorTaxonomy.md`](./ErrorTaxonomy.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)
- [`docs/Omnigent/OmnigentAdapter.md`](../Omnigent/OmnigentAdapter.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](../Omnigent/OmnigentHostOAuth.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](../ManagedAgents/CodexCliManagedSessions.md)
- [`docs/ManagedAgents/ClaudeCodeManagedSessions.md`](../ManagedAgents/ClaudeCodeManagedSessions.md)

---

## 1. Objective and boundary

MoonMind treats a true agent runtime as a first-class durable execution lifecycle rather than a long-blocking model call. This document defines:

- the `MoonMind.AgentRun` child-workflow boundary;
- canonical agent request, handle, status, and result contracts;
- ownership shared by workflows, adapters, activities, runtime supervisors, and provider systems;
- the distinctions among external delegation, direct managed execution, and the profile-bound Omnigent hybrid lane;
- Provider Profile capacity and cooldown authority;
- workspace, Skill, artifact, event, checkpoint, cancellation, and cleanup behavior;
- deterministic and credentialed conformance evidence.

This document does not define the storage model or source precedence for Skills, generic one-shot Container Jobs, ordinary `mm.activity.llm` calls, or provider-specific API schemas.

A Docker-backed executable tool remains on the generic workload path unless the launched process is a true agent runtime represented by `MoonMind.AgentRun`.

---

## 2. Product and Temporal hierarchy

`MoonMind.UserWorkflow` is the root Workflow Execution. It owns product orchestration, Step ordering, compact Step status, cancellation propagation, and post-run handling. A Step that requires a true agent runtime starts one `MoonMind.AgentRun` child workflow.

```text
Workflow Execution: MoonMind.UserWorkflow
  -> ordinary Step activity
  -> MoonMind.AgentRun child workflow
       -> external provider, direct managed runtime, or Omnigent hybrid lane
  -> validation / publishing Step
```

Ownership is deliberate:

- the root workflow owns the Workflow Execution envelope and ordered Step ledger;
- `MoonMind.AgentRun` owns exactly one true agent execution lifecycle;
- adapters translate canonical contracts to provider/runtime operations;
- activities own side effects;
- Provider Profile Manager owns provider account capacity and cooldown;
- runtime stores and bridge stores own retry-safe external/process identity;
- the artifact system owns large inputs, outputs, logs, diagnostics, and evidence.

A runtime-specific process, container, session, or host id is never the product workflow identity.

---

## 3. Canonical contract rule

All agent-facing activities and adapters return canonical MoonMind contracts directly. Workflow code does not consume provider-shaped alternatives or reconstruct canonical objects from partial dictionaries.

The schema source of truth is `moonmind/schemas/agent_runtime_models.py`.

### 3.1 `AgentExecutionRequest`

Canonical fields include:

```text
agentKind
agentId
executionProfileRef
correlationId
idempotencyKey
instructionRef
inputRefs[]
expectedOutputSchema
workspaceSpec / workspaceLocator-bearing context
resolvedSkillsetRef
parameters
approvalPolicy
retryPolicy
timeoutPolicy
callbackPolicy
```

Large content is represented by artifact references. Credentials, raw provider tokens, daemon-visible paths, and mutable provider state do not belong in the request.

### 3.2 `AgentRunHandle`

A start-like operation returns the stable identity required for subsequent status, result, cancellation, or callback correlation. Provider-specific ids remain inside canonical metadata.

### 3.3 `AgentRunStatus`

Canonical states are:

```text
queued
awaiting_slot
launching
running
awaiting_callback
awaiting_feedback
awaiting_approval
intervention_requested
collecting_results
completed
failed
canceled
timed_out
```

`awaiting_slot` means a required execution resource, commonly Provider Profile capacity or machine capacity, has not yet been acquired. Metadata states the exact reason and authority rather than using a vague waiting state.

### 3.4 `AgentRunResult`

A terminal result contains compact fields such as:

```text
outputRefs[]
summary
metrics
diagnosticsRef
failureClass
providerErrorCode
retryRecommendation
metadata
```

Large streams, snapshots, files, reports, and diagnostics remain in artifacts. Rate-limit evidence preserves a stable classification, bounded attempt summaries, retry usefulness, and Provider Profile cooldown effects.

### 3.5 Idempotency

Every start-like side effect uses `idempotencyKey` or a deterministic execution tuple. Repeated activities for one logical request reuse durable provider/process/session/host identity rather than creating duplicate work.

---

## 4. Execution lanes

MoonMind supports three true-agent ownership patterns behind the same canonical contracts.

| Lane | Canonical identity | Who owns the live runtime | Who owns materialization and durable evidence |
| --- | --- | --- | --- |
| External delegation | `agentKind=external` | External provider | Provider adapter plus MoonMind artifacts/mappings |
| Direct managed runtime/session | `agentKind=managed` | MoonMind-supervised CLI/runtime | Managed adapter, supervisor, session store, artifacts |
| Profile-bound Omnigent hybrid | `agentKind=external`, `agentId=omnigent` | Stock Omnigent host/runner | MoonMind profile-bound host coordinator, bridge, artifacts |

The hybrid lane is intentionally not represented by a second `managed` Omnigent alias. Omnigent remains the live session provider, while MoonMind directly manages the host container and credential authority used by that external session.

---

## 5. External delegation lane

An external provider adapter is used when MoonMind delegates execution to a system it does not run.

Responsibilities include:

- translating `AgentExecutionRequest` to provider transport;
- supplying artifact exchange through references, presigned access, or provider bundles;
- delivering an immutable resolved Skill snapshot without re-resolving sources;
- correlating callbacks and protecting against replay;
- normalizing provider status into `AgentRunStatus`;
- fetching output and diagnostics into `AgentRunResult`;
- canceling remote work when supported;
- retaining provider-specific details only in canonical metadata and durable mapping rows.

The preferred lifecycle is callback-first. Providers without reliable callbacks use durable timers and short bounded status activities. A polling activity does not occupy a worker for the entire remote execution.

---

## 6. Direct managed lane

A direct managed adapter is used when MoonMind launches and supervises the runtime process or workflow-scoped runtime session itself.

Responsibilities include:

- resolving managed runtime and Provider Profiles;
- acquiring provider capacity;
- resolving the canonical workspace;
- materializing the immutable resolved Skill snapshot;
- launching and supervising the CLI/runtime process;
- recording logs and lifecycle state durably;
- supporting intervention, cancellation, timeout, and cleanup;
- returning canonical status and result contracts;
- releasing provider capacity only after the credential consumer is stopped.

Managed runtimes may maintain terminal loops, use persistent auth state, operate over a workspace for extended periods, emit incremental logs, and require approvals. They are launched asynchronously and supervised durably rather than modeled as one long model-call activity.

Direct Codex managed sessions remain compatibility substrate during the Codex-through-Omnigent cutover. They emit bridge-compatible evidence where required so Workflow Detail and downstream recovery do not depend on a permanent runtime-specific UI model.

---

## 7. Profile-bound Omnigent hybrid lane

### 7.1 Identity and topology

The canonical request remains:

```text
agentKind = external
agentId   = omnigent
executionProfileRef = <selected Codex OAuth Provider Profile>
```

The live session belongs to Omnigent, but MoonMind owns profile authorization and host lifecycle:

```text
MoonMind.AgentRun
  -> integration.omnigent.profile_bound_execute on the agent-runtime queue
      -> shared Provider Profile lease
      -> durable Omnigent host binding and host lease
      -> static Compose or deterministic on-demand host
      -> exact stock host registration and codex-native readiness
      -> bridge-authorized Omnigent session
      -> artifact/evidence harvest
      -> host cleanup
      -> Provider Profile release last
```

### 7.2 Why the identity stays external

The top-level identity describes the session and interaction provider, not only who issued `docker run`. Keeping `external/omnigent`:

- preserves one bridge, checkpoint, policy, metric, and UI identity;
- avoids aliases for static versus on-demand hosts;
- keeps the stock Omnigent session/resource protocol visible at the adapter boundary;
- lets host materialization evolve without changing workflow identity;
- distinguishes the Omnigent lane from direct Codex managed sessions.

### 7.3 Profile-bound coordinator responsibilities

The coordinator:

1. reserves or loads the durable bridge attempt envelope;
2. requires and validates `executionProfileRef`;
3. acquires the purpose-aware Provider Profile lease;
4. resolves the profile-bound host binding;
5. creates or reattaches the deterministic host lease;
6. persists profile authorization before host/session side effects become ambiguous;
7. prepares the static or on-demand host;
8. validates the exact credential generation and mount;
9. verifies Codex login state inside the exact host environment;
10. resolves exactly one online host advertising `codex-native`;
11. updates bridge authorization with the host identity;
12. creates or reattaches the session on that host;
13. persists session and first-message evidence before posting;
14. streams events and harvests terminal resources;
15. stops or drains the session and host;
16. releases Provider Profile capacity only after cleanup.

A caller cannot supply an arbitrary profile-bound host id, Docker volume, or credential. The coordinator injects the exact host and safe authorization envelope immediately before session creation.

### 7.4 Launch modes

The hybrid lane supports:

- **static Compose bootstrap**, using canonical `docker-compose.yaml` and the `omnigent-host-codex` profile; and
- **deterministic on-demand Docker**, using one lease-owned, run-dedicated stock host container.

Both modes share the binding, profile lease, host lease, exact registration, readiness, bridge, artifact, checkpoint, and cleanup contracts.

The desired product authority is a versioned host-mode selection compiled from the selected Omnigent agent profile and policy. `OMNIGENT_CODEX_HOST_LAUNCH_PROFILE` may seed a bootstrap default until that product surface is complete, but it is not workflow-authored authority and must not require manual `hostId` editing.

### 7.5 Image authority

Production policy and credentialed conformance prefer complete immutable `OMNIGENT_IMAGE_REF` and `OMNIGENT_HOST_IMAGE_REF` values. Legacy repository/tag pairs remain bootstrap-compatible, but a profile that requires digest-pinned published stock images fails closed when only mutable tags are available.

---

## 8. Provider Profile capacity and cooldown

Provider Profile Manager is authoritative for provider account capacity. Adapters, host repositories, Docker workers, and sessions may apply narrower limits, but they cannot create additional provider capacity.

For a mutable OAuth profile:

```text
direct runtime consumers
+ Omnigent host consumers
+ OAuth enrollment, repair, validation, reconnect, and disconnect consumers
<= max_parallel_runs
```

The first-party Codex and Claude OAuth contract fixes `max_parallel_runs = 1`.

Capacity rules:

- selection never silently changes the chosen profile;
- retry retains the same profile unless an explicit reroute policy authorizes a different selection before credential use;
- profile lease ownership is deterministic and purpose-aware;
- a host lease or machine-capacity token does not replace the profile lease;
- provider-attributed 429/quota evidence updates the selected profile's cooldown policy;
- profile capacity is released only after every credential consumer is stopped or safely reconciled.

---

## 9. Machine, host, session, and policy capacity

Execution may be constrained by several independent layers:

1. Provider Profile account capacity;
2. profile-bound host count;
3. sessions per host;
4. worker or Docker machine capacity;
5. image and runtime resource policy;
6. network and egress policy;
7. workspace and mount availability;
8. approval policy.

The status projection identifies the blocking layer. Counters are not conflated, and success at one layer does not bypass another.

For profile-bound Codex Omnigent execution, the initial safe topology is one profile lease, one host lease, one active host, and one active session.

---

## 10. Workspace authority

Durable workflow payloads use the canonical `WorkspaceLocator` discriminated union. Locators are compact identities, never raw host filesystem paths.

Only the owning worker resolves a locator. Resolution validates runtime and run identity, canonicalizes the path, performs root containment and symlink checks, and translates the approved path to the Docker daemon's namespace when required.

Rules:

- an external-state locator is artifact authority, not a local path;
- a managed-runtime locator must match the current runtime and AgentRun store identity;
- legacy `workspacePath` fields are compatibility inputs during the replay window and cannot create new authority;
- a provider/session workspace string is derived from the trusted resolution result;
- arbitrary absolute paths from workflow parameters are rejected;
- cleanup removes only state owned by the matching run or lease.

The generic Container Jobs plane owns reusable workspace-resolution and daemon-translation primitives. Long-lived Omnigent hosts reuse them where compatible while retaining separate host/session lease semantics.

---

## 11. Runtime filesystem and network policy

### 11.1 Direct managed runtimes

Direct managed runtimes receive a workflow-scoped workspace and artifact area, runtime-specific credential materialization, immutable Skill projection, and bounded temporary state. Runtime-owned environment values take precedence over untrusted passthrough values.

### 11.2 Profile-bound Omnigent hosts

The Codex host filesystem separates:

| State | Target | Rule |
| --- | --- | --- |
| Codex OAuth home | `/home/app/.codex` | Exclusive profile-bound read/write mount, generation-checked |
| Omnigent state | `/home/app/.omnigent` | Separate static-host or lease-owned state |
| Workflow workspace | `/workspaces/run` for on-demand | Resolved from canonical workspace authority |
| Resolved Skills | `/opt/moonmind-skills` | Immutable and read-only |
| Versioned tools | `/opt/moonmind-tools` | Pinned and read-only |
| Temporary storage | `/tmp` | Bounded and non-authoritative |
| Artifacts and caches | Explicit policy/gateway | Never conflated with credentials or host state |

On-demand hosts run as UID/GID `1000:1000` from `/home/app`, use a read-only root filesystem, bounded temporary storage, deterministic labels, and the policy-selected network.

A Docker network name is not proof of restricted egress. Any restricted-egress claim requires an enforced network, proxy, or firewall boundary. A policy that cannot be realized fails closed rather than selecting a broader network or different host mode.

---

## 12. Resolved Skill delivery

Skill source resolution and precedence occur before `MoonMind.AgentRun`. The child workflow and adapters consume an immutable `resolvedSkillsetRef`; they do not re-resolve repository, deployment, user, or built-in sources during retry.

Delivery patterns include:

- direct managed materialization into the runtime workspace or canonical Skill path;
- read-only projection into static or on-demand Omnigent hosts;
- compact bundles or provider-accessible artifacts for remote external systems.

Adapters provide transport and capability boundaries. They do not duplicate the semantic logic already defined by a resolved Skill.

Required host tools are capability-gated. A required CLI projection, authentication, repository access, or mutation authority is preflighted in both the selected host and the authoritative runner environment before the run claims that capability.

---

## 13. Artifact and evidence authority

The artifact system is authoritative for large and durable evidence. Runtime-local files, provider resources, and live streams are observations until copied or referenced through an approved artifact contract.

Every lane publishes as applicable:

- normalized and raw bounded event/log journals;
- initial and terminal snapshots;
- output and declared-output manifests;
- changed-file, workspace-file, diff, and session-file evidence;
- diagnostics with redaction and truncation metadata;
- provider/runtime ids as safe refs;
- policy, profile, workspace, Skill, and approval refs;
- checkpoint and external-state refs;
- cleanup and lease-release evidence.

Artifact persistence is authoritative. Live publication is secondary and must not prevent terminal completion or durable capture when a subscriber transport fails.

---

## 14. Observability and UI projection

`MoonMind.AgentRun` and its side-effecting boundaries emit durable lifecycle state suitable for Workflow Detail. The UI does not require a provider-specific dashboard to answer:

```text
Which agent and profile were selected?
Why is execution waiting?
Which runtime, host, or session was created?
Which policy and workspace authority applied?
Did credential, host, and harness readiness pass?
What events, tools, resources, and artifacts were produced?
Was cancellation or intervention accepted?
Were runtime resources cleaned and capacity released?
```

For Omnigent, the bridge attempt envelope exists before profile/host/session side effects so failed launches remain visible even when no upstream stream starts. Each lifecycle boundary records an explicit start followed by completed or failed evidence. Workflow Detail projects failure class, stable code, safe profile/host/lease ids, diagnostics links, cleanup result, Provider Profile release state, and recommended action even when the provider emitted zero events.

Direct Codex compatibility producers emit equivalent bridge-facing evidence during migration. Process-local live buffers may optimize delivery, but they are not the cross-process durability boundary.

---

## 15. Cancellation and intervention

Cancellation propagates from `MoonMind.UserWorkflow` to the active `MoonMind.AgentRun`. The child workflow makes a bounded best-effort call to the lane-specific control surface and records the outcome.

Control rules:

- external providers use supported remote cancel/stop operations;
- direct managed runtimes interrupt or terminate the supervised process/session;
- Omnigent uses typed interrupt/stop/terminate/harvest/host-cleanup operations authorized against the durable bridge and host lease;
- intervention and approval remain separate from passive log viewing;
- changing instructions, runtime, profile, or publish mode uses an explicit continuation or branch contract rather than mutating original input;
- cleanup continues after cancellation and provider capacity is released only when credential consumers are stopped.

A detached process or provider job is not considered ongoing MoonMind-managed work unless durable ownership and supervision explicitly continue.

---

## 16. Retry, replay, and reconciliation

### 16.1 Activity retry

An activity retry reuses the same canonical request and idempotency key. It inspects durable run/provider/session/host state before creating side effects.

### 16.2 Workflow replay

Changes that add, remove, or reorder workflow commands use Temporal patch/version markers or Worker Versioning so in-flight histories replay to the recorded command path.

### 16.3 Rerun and re-resolution

A rerun reuses the original immutable Skill and policy/profile snapshots by default. Explicit re-resolution is a distinct operator or workflow action.

### 16.4 Runtime reconciliation

Each lane reconciles its own side effects:

- external providers reconcile remote ids and callback state;
- direct managed runtimes reconcile process/container/session records and supervisors;
- Omnigent reconciles bridge attempts, first-message markers, profile bindings, host leases, deterministic containers, registered hosts, sessions, credential generations, and janitor work.

No lane silently creates replacement authority while the original may still be active.

---

## 17. Checkpoint capability layers

Checkpoint capabilities remain distinct:

- `session_state_checkpoint` preserves a provider/runtime session, thread, epoch, or external-state ref;
- `step_workspace_checkpoint_capture` captures the workspace owned by a completed Step Execution;
- `step_workspace_checkpoint_restore` materializes a declared compatible workspace checkpoint kind.

A session-state ref is not evidence of workspace capture or restore. `external_state_ref` can preserve Omnigent/provider continuity without being locally restorable.

For Omnigent, checkpoint identity may include profile, provider-lease, credential-generation, binding, host-lease, host, bridge-session, Omnigent-session, idempotency, first-message, workspace-locator, diagnostics, terminal, and artifact refs. Credentials and daemon paths are forbidden.

Recovery chooses:

- **live reattach** only when the original authority, generation, host, session, and first-message evidence remain valid; or
- **cold restore** by reacquiring the selected profile, creating new host/session authority, and materializing validated artifact-backed state.

A branch receives independent authority and never concurrently reuses the original mutable OAuth lease.

---

## 18. Error and rate-limit behavior

Failures are normalized into stable classes and bounded diagnostics. The responsible boundary records whether retry, reroute, reconnect, cleanup, janitor, or operator correction is useful.

Rules:

- profile selection and credential failures fail closed;
- missing host registration or required harness capability is configuration/integration failure, not a reason to select another host silently;
- provider 429/quota evidence updates the selected Provider Profile cooldown;
- cleanup failure remains visible and may keep capacity held until reconciliation;
- malformed or ambiguous first-message/session state blocks duplicate side effects;
- policy denial records the selected policy version and rationale;
- raw provider responses and command output are redacted before persistence.

---

## 19. Conformance evidence

Deterministic CI and credentialed live evidence are separate gates.

Deterministic CI proves schema, idempotency, lifecycle ordering, fake-server protocol behavior, artifact structure, cleanup selection, redaction, and replay behavior without claiming provider credentials were exercised.

Credentialed live conformance uses `tools/run_omnigent_live_conformance.py` with:

- digest-pinned `OMNIGENT_IMAGE_REF` and `OMNIGENT_HOST_IMAGE_REF` values;
- an already-enrolled OAuth profile;
- an operator-provisioned `MOONMIND_OMNIGENT_ACTION_COMMAND` that performs real provider actions;
- durable, scenario-bound, schema-versioned evidence refs;
- independent evidence resolution and secret scanning;
- exact workflow, run, profile, lease, host, session, image, architecture, capability, lifecycle, artifact, and cleanup identifiers;
- an isolated Compose project whose cleanup removes its containers and networks but never enrolled OAuth or unrelated volumes.

The repository semantic action backend is test infrastructure, not implicit live proof. Missing, malformed, opaque, mismatched, or bare-boolean evidence fails the live gate. Published stock-image proxy compatibility, static restart/replay, on-demand lifecycle, and failure-path scenarios are independently gateable.

---

## 20. Security invariants

- Workflow payloads carry refs and policy choices, not credentials or daemon authority.
- Provider Profile Manager remains authoritative for account capacity and credential ownership.
- Mutable OAuth profiles have one active consumer across every substrate and maintenance operation.
- Workspaces are resolved from `WorkspaceLocator` at the owning worker and containment-checked.
- Runtime credentials, host registration credentials, artifact access credentials, and provider OAuth are distinct authorities.
- No adapter silently substitutes profiles, credentials, models, networks, host modes, images, or less-constrained runtime policy.
- Required capabilities are preflighted before execution claims them.
- Large evidence is artifact-backed, bounded, and redacted.
- Cleanup is idempotent and removes only matching run/lease-owned resources.
- Provider capacity is released only after the credential consumer is stopped or safely reconciled.

---

## 21. Acceptance contract

A Step can start one `MoonMind.AgentRun` using any supported lane and receive the same canonical status, result, artifact, cancellation, retry, and checkpoint semantics without provider-specific workflow logic.

For profile-bound Codex Omnigent execution, a workflow selects `external/omnigent` plus a Codex OAuth Provider Profile; MoonMind compiles the host-mode and runtime policy, acquires all capacity, resolves a canonical workspace, materializes or validates exactly one stock compatible host, proves credential and `codex-native` readiness, authorizes one bridge session, posts the first message once, projects durable conversation and lifecycle evidence, harvests artifacts, cleans owned runtime state, and releases Provider Profile capacity last.

Static and on-demand Omnigent host modes remain interchangeable at the workflow contract boundary while preserving explicit policy, readiness, evidence, and cleanup differences. Direct Codex remains compatibility substrate until the Omnigent lane passes its deterministic and credentialed live cutover gates.
