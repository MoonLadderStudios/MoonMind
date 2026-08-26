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
**Last updated:** 2026-08-17
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

Runtime capability admission is closed over the launch mode. A one-shot process
may use only operations that can finish before that process exits. MoonMind must
reject or hide any tool whose successful contract requires a later callback when
the runtime has no durable same-session continuation owner. A tool's availability
is not evidence that the selected execution lane can deliver its result.

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
  -> MoonMind.OmnigentSession (deterministic canonical session identity)
      -> bounded, revision-fenced activities on the agent-runtime queue
          -> shared Provider Profile lease
          -> durable Omnigent host binding and host lease
          -> static Compose or deterministic on-demand host
          -> exact stock host registration and codex-native readiness
          -> bridge-authorized Omnigent session
          -> bounded event batches plus periodic authoritative snapshots
          -> artifact/evidence harvest and publication
          -> host cleanup
          -> Provider Profile release last
```

Newly admitted profile-bound sessions use this durable supervisor. Existing
AgentRun histories retain the legacy profile-bound activity behind a Temporal
patch boundary so replay does not transfer ownership in flight. The supervisor
input contains only immutable owner identities, a compiled-intent artifact ref
and digest, the initial turn-attempt identity, and frozen feature/compatibility
versions; provider content, credentials, and mutable host paths stay outside
workflow history.

New-session admission is a distinct bounded Activity behind its own replay
patch. The decision freezes the admitted feature generation and evaluates an
operator mode of `enabled`, `canary`, or `disabled`; canary selection uses an
exact AgentRun owner allowlist, and an optional execution-profile allowlist can
narrow either enabled mode. A configured generation mismatch fails closed.
The admission Activity is not re-evaluated by an already admitted child, so
disabling new selection routes only later AgentRuns to the legacy owner and
cannot disable replay, query, cancellation, cleanup, or historical reads for
an admitted session.

### 7.2 Why the identity stays external

The top-level identity describes the session and interaction provider, not only who issued `docker run`. Keeping `external/omnigent`:

- preserves one bridge, checkpoint, policy, metric, and UI identity;
- avoids aliases for static versus on-demand hosts;
- keeps the stock Omnigent session/resource protocol visible at the adapter boundary;
- lets host materialization evolve without changing workflow identity;
- distinguishes the Omnigent lane from direct Codex managed sessions.

### 7.3 Durable session supervisor responsibilities

The session supervisor drives the pure reconciliation contract and delegates
each side effect to a short, idempotent activity. It:

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
14. consumes provider events only in bounded batches and keeps a periodic
    authoritative snapshot deadline active while terminality is unresolved;
15. persists each decision and fenced logical command before execution;
16. harvests and publishes terminal evidence;
17. stops or drains the session and host in independently retryable phases;
18. releases Provider Profile capacity only after cleanup.

A caller cannot supply an arbitrary profile-bound host id, Docker volume, or credential. The coordinator injects the exact host and safe authorization envelope immediately before session creation.

The supervisor accepts reference-only signals for provider observations,
authorized continuation, cancellation/interruption, approval/intervention,
cleanup, operator reconciliation, callbacks, and host exit. Its compact query
projection reports owner identities, frozen feature and compatibility versions,
phase, revision and fencing generation, observation frontiers, decision and turn
counts, pending control intent, terminal status, and terminal evidence ref. It
never returns the compiled intent body, provider payloads, credentials, or host
paths.

Continue-As-New retains the same canonical workflow identity and carries only
immutable intent authority plus bounded revision/frontier/count summaries. A
rollover is requested before decision, observation, history-length,
history-segment-age, or turn-attempt thresholds can make the current history
unbounded. Provider and
command state remain in their durable stores and are reloaded after rollover.

Terminal projection distinguishes integration unavailability, execution
failure, delivery ambiguity, reconciliation quarantine, incomplete cleanup,
timeout, cancellation, and completion. A workflow deadline first performs a
fresh bounded event read and authoritative snapshot; timeout intent cannot
overwrite provider completion that this reconciliation proves.

An exhausted bounded phase is converted to typed, reference-only durable
failure evidence after a minimal authority read verifies the immutable child
identities and returns the current revision and supervisor fence. No exception
prose is placed in workflow history or canonical state. Failure and quarantine
decisions persist the terminal reason and re-enter the reconciler so evidence
harvest, provider stop, host stop, and lease release retain their normal
ordering. If cleanup itself exhausts, the primary execution result remains
authoritative, the session becomes `cleanup_incomplete`, and a cleanup evidence
ref plus the durable janitor owner is recorded in unclaimed cleanup authority.
That handoff remains fence-claimable for retry; Provider Profile capacity is
not released early.

### 7.4 Launch modes

The hybrid lane supports:

- **static Compose bootstrap**, using canonical `docker-compose.yaml` and the `omnigent-host-codex` profile; and
- **deterministic on-demand Docker**, using one lease-owned, run-dedicated stock host container.

Both modes share the binding, profile lease, host lease, exact registration, readiness, bridge, artifact, checkpoint, and cleanup contracts.

The desired product authority is a versioned host-mode selection compiled from
the selected Omnigent agent profile and policy. On-demand Docker is the normal
default because it adds no idle host container and can be realized from the
trusted worker. Static Compose remains an explicit advanced choice for
deployments that need a long-lived host lifecycle. Neither path requires manual
`hostId` editing. `OMNIGENT_CODEX_HOST_LAUNCH_PROFILE`, when present, is only
host-substrate configuration after on-demand policy selection; it does not opt
the runtime in or choose the product policy.

### 7.5 Image authority

Operator configuration may use the stock repository/tag inputs, including
`latest`. During bootstrap or policy reconciliation MoonMind acquires those
images through its existing trusted Docker image boundary, resolves repository
digests, and stores only the exact digest references in the active immutable
launch policy. A mutable tag is input to reconciliation, never launch authority.
If a tag cannot be resolved and no safe local repository digest exists, policy
activation fails closed with image readiness evidence. Credentialed conformance
and release manifests still name the resolved immutable `OMNIGENT_IMAGE_REF`
and `OMNIGENT_HOST_IMAGE_REF` values explicitly.

Bootstrap-owned policy defaults advance through a new immutable version when
those resolved repository digests move; operator-authored defaults are never
rewritten by bootstrap. Registry refresh runs in the lifespan-owned background
reconciler; API startup readiness performs only bounded local inspection. A
server policy cutover uses the running Compose container's observed repository
digest, never a newly pulled tag whose container is not running yet. Bindings
with active host leases remain on their immutable prior authority and are
revisited after the lease drains. Binding cutover and lease acquisition lock the
same durable binding row. Live launch attestation requires the selected policy
reference to appear in the running image's observed repository digests. The
mutable Compose input recorded in `Config.Image` remains diagnostic
configuration, not runtime image authority.

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
- host-side Git commands trust only the exact resolved workspace for that command, so a repository handed between the worker and runtime UIDs remains usable without a global trust exemption;
- arbitrary absolute paths from workflow parameters are rejected;
- cleanup removes only state owned by the matching run or lease.

The generic Container Jobs plane owns reusable workspace-resolution and daemon-translation primitives. Long-lived Omnigent hosts reuse them where compatible while retaining separate host/session lease semantics.

---

## 11. Runtime filesystem and network policy

### 11.1 Direct managed runtimes

Direct managed runtimes receive a workflow-scoped workspace and artifact area, runtime-specific credential materialization, immutable Skill projection, and bounded temporary state. Runtime-owned environment values take precedence over untrusted passthrough values.

Managed sessions and profile-bound Omnigent hosts whose normalized
`requiredCapabilities` include `execution.fanout` receive a separate short-lived
execution fan-out bearer after policy authorization. The requirement is derived
automatically when a resolved Skill declares `sideEffect.kind: enqueue_children`,
so the supported batch path requires no operator permission toggle. That bearer is bound
to the parent Workflow Execution, agent run, runtime session, and runtime id; it
is not interchangeable with the container-job bearer or a user API token. The
execution API accepts the bearer only for idempotent task/workflow child
requests with `runtimeInheritance="caller"`, rejects schedule and direct-create
shapes, records the authoritative `parentWorkflowId`, and limits describe calls
to children of that parent. Restricted Omnigent egress additionally requires
the fan-out marker and bearer on the exact create and child-describe paths.
Profile-bound Omnigent hosts expose the bearer through a lease-owned read-only
file and pass only its non-secret selector into runner and login-shell
environments. Hosts without the requirement receive neither the bearer nor the
selector.

The Run workflow derives the mint authorization from immutable resolved-Skill
provenance and carries it in `stepExecution.skillSourcePolicy.executionFanout`.
Built-in and deployment-managed Skills are eligible; repo/local Skills and
top-level-only declarations are denied before runtime launch. The absent field
is a replay marker for already-scheduled launch payloads, not a current default.

The Run workflow derives the mint authorization from immutable resolved-Skill
provenance and carries it in `stepExecution.skillSourcePolicy.executionFanout`.
Built-in and deployment-managed Skills are eligible; repo/local Skills and
top-level-only declarations are denied before runtime launch. The absent field
is a replay marker for already-scheduled launch payloads, not a current default.

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

Terminal-contract validation occurs before MoonMind releases the workspace,
retry budget, credential lease, or cleanup authority. A successful process exit
with missing evidence is a recoverable execution failure, not completion. A
capable session receives bounded same-session continuation; a managed one-shot
runtime receives bounded replacement-process continuation over the same
authoritative workspace. Exhaustion routes to terminal checkpoint preservation
when repository mutation was authorized.

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

## 15.1 Execution budget

An agent run has one execution budget, resolved from `timeoutPolicy` by a single
authority and applied identically at both boundaries that can end a run for
taking too long: the `MoonMind.AgentRun` poll loop and the managed process
supervisor. The two deadlines cannot diverge, because the workflow publishes the
resolved budget back into the launch request and the supervisor resolves the
same values from it.

The budget has four fields:

| Field | Meaning |
|---|---|
| `timeout_seconds` | Base window. A run that cannot demonstrate progress ends here. |
| `max_timeout_seconds` | Hard ceiling. No observed progress extends a run past this. |
| `progress_stall_seconds` | How long observed progress may go stale before an over-base run is treated as stuck. |
| `execution_budget_mode` | Which decision the budget encodes: `progress_aware` or `flat`. |

Omitted fields resolve to kind-specific defaults. The ceiling defaults to a
multiple of the base window rather than an absolute constant, so an explicitly
requested tight budget keeps a tight ceiling.

A resolved budget is never internally contradictory. The stall window never
exceeds the base window, explicit or defaulted — a run is not given more time to
prove it is alive than it was given to finish, and a wider stall window would
otherwise erase the base window as a boundary, because a run's own start counts
as an observation. The base window is bound by the same absolute cap as the
ceiling, so a run can never be terminated for "reaching the maximum" before the
base window it declared has elapsed.

**Mode travels with the numbers.** A workflow history that predates
progress-awareness enforces a flat deadline, and it publishes that decision, not
just its base window. A callee that re-resolves the budget — the launch activity
does so on every launch and retry, including retries dispatched after the
progress-aware deployment — must reach the same decision. Publishing only the
base window let the supervisor derive a progress-aware ceiling the workflow never
granted: the workflow timed out at the base window and released the provider
slot while the supervisor carried the process on to that ceiling.

**Elapsed wall-clock alone is not evidence that a run is stuck.** A run is
terminated for exceeding its budget only when one of two things is true:

- the base window has elapsed **and** no progress has been observed within
  `progress_stall_seconds` (or no progress has ever been observed); or
- the hard ceiling has been reached.

A run that is still making observable progress when its base window elapses
continues until progress goes stale or the ceiling is reached. The two outcomes
are reported distinctly: a run stopped at the ceiling is never described as
having made no progress, because the operator responses differ — raise the
ceiling versus investigate a wedged runtime. The terminal result records the
budget the decision was made against and which of the two conditions ended it.

**Budget intervals are measured monotonically.** Elapsed and idle durations come
from a monotonic clock; wall-clock timestamps remain the run's persisted
evidence, because they must line up with file mtimes and operator timelines. A
host clock step must not be able to terminate a healthy run as over-budget, nor
let a wedged one outlive the deadline the other boundary is enforcing.

**A stall verdict is decided against fresh evidence, never cached evidence.**
Progress observations are sampled on a heartbeat cadence, so a verdict computed
from the cached observation can be a whole heartbeat interval stale, and the last
bounded poll wait can consume the entire known remaining budget. Before ending a
run for lack of progress, each boundary reconciles once against current evidence
— the supervisor samples CPU and workspace activity, the workflow performs one
final bounded status poll — and re-decides. A process that resumed work seconds
before its boundary is not killed for activity the boundary had simply not looked
for yet. The hard ceiling is exempt: it is enforced without reconciliation,
because no observed progress extends a run past it.

### Progress evidence

Progress is observed evidence, never a heuristic derived from elapsed time. The
supervisor takes the most recent of three independent signals and publishes it
as `last_log_at`, which the workflow reads through canonical status metadata:

- **runtime output** — bytes on stdout or stderr;
- **workspace mutation** — the newest meaningful file mtime under the run
  workspace;
- **process-tree CPU activity** — cumulative CPU time consumed by the supervised
  session.

No single signal is sufficient, which is why the newest of all three is
authoritative. A one-shot CLI launched in print mode emits nothing until its turn
completes, so output stays silent for the whole run. An agent running a test
suite or compiler writes only into ignored directories, so workspace mutation
goes quiet for tens of minutes. CPU activity covers both, including work done
only by descendants of the launched process, and distinguishes them from a
process blocked forever on a dead socket, awaiting input, or deadlocked, which
consumes none.

A runtime that livelocks does consume CPU and therefore reads as progress. That
is contained by the hard ceiling, which no amount of observed progress extends.
The same observation drives the no-progress watchdog, so the watchdog and the
budget cannot disagree about whether a run is progressing.

CPU accounting is cumulative across the session's lifetime, not across its
currently live processes. A descendant that exits keeps its consumed CPU in the
session total, so the total never falls. Without this, an agent running a
sequence of short-lived compilers or test processes reads as stalled while
working continuously, because each replacement has to re-earn the CPU its
predecessor took with it.

Callers that provision resources which must outlive the whole run — credential
lifetimes, broker sockets — size them from the ceiling, never from the base
window, plus a bounded allowance for launch preparation that precedes the
supervisor starting its clock. Sizing them from the base window would strand an
extended run without the authority it was granted; sizing them at exactly the
ceiling would expire them while the run is still legally executing.

**Only a boundary that can observe progress may spend the extension.** An
activity `ScheduleToClose` is sized from the ceiling when the workflow can
re-evaluate the budget while the activity runs. Where the workflow is blocked
awaiting a single long-running activity and therefore cannot observe progress or
re-decide, `ScheduleToClose` is the only deadline that exists, and it is sized
from the base window. Sizing it from the ceiling there would let a run that never
makes progress hold its provider slot for the full ceiling with no boundary able
to stop it.

---

## 16. Retry, replay, and reconciliation

### 16.1 Activity retry

An activity retry reuses the same canonical request and idempotency key. It inspects durable run/provider/session/host state before creating side effects.

Direct managed-session state is written through a monotonic revision compare-and-swap boundary. A provider turn is admitted while holding the state authority lock from the final locator/revision check through persistence of the provider's accepted turn identifier; concurrent control actions therefore cannot create an accepted but untracked turn. Longer provider observation remains outside the lock, and later publications succeed only when the persisted revision they read is still current. A concurrent clear, turn, or observer that advanced the revision makes a stale publication fail as a managed-session locator mismatch; the caller reloads the authoritative epoch, thread, and container locator before deciding whether to retry. An observer must never roll session state back to an older epoch or thread. When the controller deliberately replaces a missing, stale, or explicitly superseded container, it authorizes exactly that container transition while preserving the logical session, epoch, thread, workspace, and revision chain.

### 16.1.1 Terminal-contract continuation

Terminal-contract continuation is semantic recovery, not an Activity retry. It
reuses the immutable Skill, profile, policy, and workspace authority, carries a
bounded corrective instruction naming the missing evidence, and records each
attempt and outcome. Same-session continuation is preferred when the capability
snapshot supports it. Otherwise a directly managed one-shot lane may relaunch a
replacement process against the same run-owned workspace. No continuation may
silently create a fresh checkout or switch source authority.

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
When direct-runtime startup reconciliation proves that the recorded PID no
longer exists, the result carries a typed process-loss code. A one-shot lane may
perform one replacement attempt with the same immutable request, Provider
Profile, idempotency key, and authoritative workspace. The replacement inspects
local and remotely visible side effects before repeating work. A second process
loss is terminal and retains the workspace for checkpoint or operator recovery.

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

A Checkpoint Branch turn is launched by one durable MoonMind owner. Before any
lease, host, session, message, or repository side effect, it validates the
pinned source and checkpoint, parent-turn lineage, immutable instructions, git
binding, stored execution/profile/policy authority, and current credential
generation. It then persists stable Step Execution, Agent Run, bridge, and
workflow identities and dispatches the canonical profile-bound
`external/omnigent` request using `branch_from_checkpoint`. Retry and replay
reattach to those identities; terminal harvest records checkpoint, output,
publication, diagnostics, capture, and cleanup evidence before host and Provider
Profile authority are released in the normal release-last order.

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
