# Managed and External Agent Execution Model

**Document Class:** Canonical declarative  
**Status:** Current  
**Owners:** MoonMind Platform  
**Last updated:** 2026-10-16
**Authority:** Unified Temporal lifecycle and ownership model for true agent execution: the Omnigent agent backend, the direct managed compatibility runtimes, and external delegation

## Related documents

- [`docs/Temporal/WorkflowExecutionProductModel.md`](./WorkflowExecutionProductModel.md)
- [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](./ActivityCatalogAndWorkerTopology.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](./WorkflowArtifactSystemDesign.md)
- [`docs/Temporal/ErrorTaxonomy.md`](./ErrorTaxonomy.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)
- [`docs/Workflows/GitHubIssueStatusStateMachineDesign.md`](../Workflows/GitHubIssueStatusStateMachineDesign.md)
- [`docs/UI/HarnessProfileBackendSelection.md`](../UI/HarnessProfileBackendSelection.md)
- [`docs/Omnigent/CodexCreateToHostContract.md`](../Omnigent/CodexCreateToHostContract.md) (the normal Create-to-host journey for Codex)
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
- the distinctions among the Omnigent lane, direct managed compatibility runtimes, and external delegation;
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
       -> Omnigent, direct managed runtime, or external provider
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

### Durable attempt completion

An admitted execution keeps its immutable request, admission epoch, profile
capacity, and session identity across Activity redelivery. A timeout is not
proof that the remote operation stopped. Revoked delivery ownership preserves
the session and its resources for the replacement owner; janitors take over an
active binding only after the authoritative Temporal execution is closed, and
missing ownership evidence preserves resources and reports the gap.

The runtime binding records immutable turn, compute, verification, saved-work,
and publication receipts. The resolved portable Skill owns remediation choices
and terminal verdicts; the generic host supplies bounded same-session delivery
and cumulative budgets, and evidence read retries launch no provider turn. A
Skill's explicit continuation stays intermediate until its declared terminal
evidence validates. When a same-session continuation reports the same progress
key, the Skill's recorded manual-review or failed disposition becomes terminal,
so the parent step does not repeat an attempt that cannot progress. Evidence
naming no execution is malformed and repaired by rerunning the Skill's result
helper; only evidence bound to a different execution is stale.

Save, restore, and publication deliveries share a database-owned finalization
lock and re-read committed receipts after acquiring it, without holding pool
connections while waiting. The publisher still reconciles remote branch state
and verifies the exact candidate head, including after a lost acknowledgement.
Verified primary completion survives auxiliary cleanup and issue-synchronization
failures, whose owners and pending evidence stay explicit.

Saved repository work uses the canonical bounded archive and checkpoint
contracts; selected unpublished Git ancestry travels in a scanned, bounded
bundle, because a branch name or a deleted workspace reference is insufficient.
Cold finalization restores the original admitted sandbox owner from verified
artifacts without reacquiring a provider host, validating immutable input and
plan references, manifest digests, repository head, and content before releasing
resume authority. An existing changed workspace is never overwritten.

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

`awaiting_slot` means a required execution resource, commonly Provider Profile capacity, host count, or a container-job slot, has not yet been acquired. Metadata states the exact reason and authority rather than using a vague waiting state.

Container-job requests carry explicit positive resource limits, and bootstrap
on-demand host policies use fixed stock limits (2 CPUs, 4 GiB). Historical
zero-valued documents and already-admitted legacy jobs stay decodable and run
under a deterministic fixed-resource successor rather than ending in a replan
message. [Docker Backend Service](../ManagedAgents/DockerBackendService.md#111-fixed-limits)
owns enforcement, prerequisites, and diagnostics.

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

### 3.6 Input materialization

Prepared attachments use the canonical `workflow` payload; retained Temporal inputs under `task` stay readable. Planning links each declared artifact to the actual workflow/run through the artifact owner before dispatch. External requests carry selected raw refs; managed requests carry their prepared-context contract. The host checks ownership and materializes bytes before launch. A caller-supplied path is never proof of materialization, and missing, foreign, or unreadable evidence prevents launch.

Declared recovery evidence follows the same rule: `gateResultRef` and `remainingWorkRef` are materialized through the artifact owner before the agent consumes them, including historical requests that did not repeat them in `inputRefs`. The host supplies `gateResultPath` and `remainingWorkPath` from that evidence (one file when both refs name one artifact) and does not interpret verifier findings or replace the resolved Skill's decisions.

A ready workspace proves repository readiness, not delivery of the next step's evidence. Re-admission verifies artifact ownership, integrity, size, and containment while preserving candidate edits, and replacement inputs are staged and verified before atomically replacing a prior read-only input. The ready-host lease persists the verified path mapping, so an Activity restart before first-turn delivery recovers the same inputs without another host or a changed candidate. Temporal patching preserves the commands of histories recorded before this boundary.

---

## 4. Execution lanes

Three true-agent ownership patterns sit behind the same canonical contracts. Omnigent is the normal agent backend. The direct managed runtimes are compatibility Backends an operator can choose under Advanced ([selection design](../UI/HarnessProfileBackendSelection.md)).

| Lane | Canonical identity | Who owns the live runtime | Who owns materialization and durable evidence |
| --- | --- | --- | --- |
| Omnigent | `agentKind=external`, `agentId=omnigent` | Stock Omnigent host/runner | The plan's recorded realizer, MoonMind host coordinator, bridge, artifacts |
| Direct managed runtime/session | `agentKind=managed` (`codex_cli`, `claude_code`) | MoonMind-supervised CLI/runtime | Managed adapter, supervisor, session store, artifacts |
| External delegation | `agentKind=external` (for example `openclaw`) | External provider | Provider adapter plus MoonMind artifacts/mappings |

Omnigent is not represented by a second `managed` alias: it remains the live session provider, while MoonMind manages the host container and credential authority that session uses (§7.2).

---

## 5. External delegation lane

An external adapter is used when MoonMind delegates execution to a system it does not run. It translates `AgentExecutionRequest` to provider transport, exchanges artifacts by reference, delivers the immutable resolved Skill snapshot without re-resolving sources, normalizes provider status into `AgentRunStatus`, returns outputs and diagnostics as `AgentRunResult`, and keeps provider-specific details in canonical metadata.

`external_adapter_registry.py` registers an adapter only when its provider is enabled. Each adapter declares an execution style; the registered adapters, OpenClaw and Omnigent, are streaming gateways that `MoonMind.AgentRun` runs through one bounded `integration.<id>.execute` Activity. Omnigent plans dispatch to their recorded realizer instead (§7.1).

---

## 6. Direct managed lane

A direct managed adapter is used when MoonMind launches and supervises the runtime process or workflow-scoped runtime session itself: direct Codex (`codex_cli`) as a workflow-scoped managed session and direct Claude Code (`claude_code`) as one-shot managed runs.

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

Managed session turn preparation stages declared `inputRefs`, including the
trusted issue brief and prior verification reports, in `.moonmind/attachments/`
through the shared workspace artifact projector after checkout. Every real turn
reconciles that directory to its declared inputs (including an empty set),
removing stale briefs and reports only after replacements are admitted, without
restoring or resetting the candidate repository. Launch-only metadata
preparation needs no checkout. Retained histories keep their original attachment
dispatch through the workflow patch boundary.

Managed runtimes may keep terminal loops and persistent auth state, work over a
workspace for long periods, emit incremental logs, and require approvals, so
they are launched asynchronously and supervised durably rather than modeled as
one long model-call Activity.

Runtime capability admission is closed over the launch mode. A one-shot process
may use only operations that finish before it exits, so a tool whose contract
needs a later callback is rejected or hidden when the runtime has no durable
same-session continuation owner. For the same reason the direct Claude Code
print-mode launcher sets `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` after profile
materialization (profile overrides cannot relax it) and denies deferred wakeup
tools. The generic Omnigent host applies the same variable to every
`claude-native` host class: the step settles when the turn ends and the host is
removed, so background work would finish with no owner to resume the session or
write its declared outputs.

---

## 7. Omnigent lane

### 7.1 Identity and topology

The canonical request is:

```text
agentKind = external
agentId   = omnigent
executionProfileRef = <selected Provider Profile>
```

New Omnigent work has one lifecycle owner: the persisted execution plan's
recorded realizer. Workflow Create, `moonmind workflow run`, and Omnigent child
plans submit through the task/workflow envelope, which resolves the Agent
Profile and persists the immutable plan before scheduling. A raw
`workflowType`/`initialParameters` request to `POST /api/executions` that names
Omnigent, or omits the runtime under an Omnigent default, is rejected with
`omnigent_product_boundary_required` instead of reaching a plan-less lane.
`generic-omnigent-host@1` runs through
`integration.omnigent.profile_bound_execute`; a plan recording
`codex-profile-bound@1` uses `integration.omnigent.execute` with its recorded
coordinator. The realizer launches the host and session, delivers the first
turn, and owns the terminal result and cleanup; AgentRun starts no second
supervisor around it. The live session belongs to Omnigent, while MoonMind owns
profile authorization and host lifecycle in this order:

```text
MoonMind.AgentRun
  -> Provider Profile lease (acquired in the workflow, before the Activity)
  -> the plan's recorded realizer Activity
      -> durable Omnigent host binding and host lease
      -> static Compose or on-demand host
      -> exact stock host registration and harness readiness
      -> bridge-authorized Omnigent session and first turn
      -> bounded event batches plus periodic authoritative snapshots
      -> artifact/evidence harvest and publication
      -> host cleanup
  -> Provider Profile release last
```

Recurring schedule creation and target edits compile the same plan into the
stored target when it names Omnigent or omits the runtime under an Omnigent
default; an edit whose plan cannot be compiled is rejected. Each
schedule-started occurrence recompiles the schedule's frozen inputs at
admission, binding the installed host and server images without rewriting the
schedule; a refused compile keeps the recorded plan under its own launch checks.
A schedule stored without a plan is compiled by the deployment schedule refresh
pass, which requires a stored snapshot to match the schedule's durable Agent
Profile usage and names definitions it cannot yet compile in its failures for
the next pass.

AgentRuns that carry no persisted plan (histories admitted before plan
admission, exact reruns of them, and occurrences of a still plan-less schedule)
use the retained `MoonMind.OmnigentSession` supervisor (§7.3). Its plan-less
admission is a distinct bounded Activity behind its own replay patch, and the
legacy profile-bound Activity stays behind a Temporal patch boundary so replay
does not transfer ownership in flight. Fully bound, reference-only, and
plan-less inputs all apply the operator's supervisor rollback control before new
admission, while recorded decisions from retired canary/allowlist modes still
replay. An admitted child never re-evaluates admission, so blocking new
admission cannot disable replay, query, cancellation, cleanup, or historical
reads. The plan-less branch can be removed once no retained plan-less execution
can be replayed or exactly rerun and no stored Omnigent schedule lacks a plan.

### 7.2 Why the identity stays external

The top-level identity describes the session and interaction provider, not only who issued `docker run`. Keeping `external/omnigent`:

- preserves one bridge, checkpoint, policy, metric, and UI identity;
- avoids aliases for static versus on-demand hosts;
- keeps the stock Omnigent session/resource protocol visible at the adapter boundary;
- lets host materialization evolve without changing workflow identity;
- distinguishes the Omnigent lane from direct Codex managed sessions.

### 7.3 Retained session supervisor

For plan-less AgentRuns, `MoonMind.OmnigentSession` keeps a deterministic canonical
session identity. Its input contains only immutable owner identities, a
compiled-intent artifact ref and digest, the initial turn-attempt identity, and
frozen feature/compatibility versions; provider content, credentials, and
mutable host paths stay outside workflow history. The supervisor drives the pure reconciliation contract and delegates
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

The supervisor accepts reference-only signals (provider observations,
continuation, cancellation, approval/intervention, cleanup, operator
reconciliation, callbacks, host exit). Its compact query projection never
returns the compiled intent body, provider payloads, credentials, or host paths.
Continue-As-New keeps the canonical workflow identity and carries only
immutable intent authority plus bounded revision/frontier/count summaries,
rolling over before decision, observation, history, or turn-attempt thresholds
make history unbounded; provider and command state reload from durable stores.

Terminal projection distinguishes integration unavailability, execution
failure, delivery ambiguity, reconciliation quarantine, incomplete cleanup,
timeout, cancellation, and completion. A workflow deadline first performs a
fresh bounded event read and snapshot, so timeout intent cannot overwrite proven
provider completion. An exhausted phase becomes typed, reference-only failure
evidence (no exception prose in history) and re-enters the reconciler so
harvest, provider stop, host stop, and lease release keep their order. If
cleanup itself exhausts, the primary result stays authoritative, the session
becomes `cleanup_incomplete`, and a fence-claimable handoff to the durable
janitor is recorded; Provider Profile capacity is not released early.

### 7.4 Launch modes

Host mode comes from the versioned launch policy compiled from the selected
execution configuration:

- **deterministic on-demand Docker**, the default policy, uses one lease-owned, run-dedicated stock host container, adding no idle host and realizable from the trusted worker;
- **static Compose bootstrap** uses canonical `docker-compose.yaml` and its Omnigent host profile, an explicit advanced choice for deployments that need a long-lived host lifecycle.

Both share the binding, profile lease, host lease, exact registration, readiness, bridge, artifact, checkpoint, and cleanup contracts, and neither requires manual `hostId` editing. `OMNIGENT_CODEX_HOST_LAUNCH_PROFILE`, when present, is only
host-substrate configuration after on-demand policy selection; it does not opt
the runtime in or choose the product policy.

### 7.5 Image authority

Operator configuration may use stock repository/tag inputs, including `latest`.
MoonMind acquires those images through its trusted Docker image boundary,
resolves repository digests, and persists them as resolved state. Fresh
admission binds the installed same-repository host and server digests; an
explicitly requested Host Class stays exact and a foreign host family fails
closed. A mutable tag is input to reconciliation, never launch authority. If a
tag cannot be resolved and no safe local repository digest exists, activation
fails closed with image readiness evidence. Credentialed conformance and release
manifests name the resolved immutable `OMNIGENT_IMAGE_REF` and
`OMNIGENT_HOST_IMAGE_REF` explicitly.

Bootstrap-owned policy defaults get a new immutable version only for a
resource-default migration or an image of another repository family; a
same-repository digest move is bound at admission instead, and operator-authored
defaults are never rewritten. Image resolution, registry refresh, provider
validation, and deployment qualification run in the lifespan-owned background
reconciler, which starts after local startup, retries with bounded backoff, and
is cancelled and awaited on shutdown. API startup waits neither for it nor for
credential leases held by active workflows; HTTP health reports API/database
availability while execution admission still requires the selected runtime's
validated evidence.

Server image authority is the running Compose container's observed repository
digest, never a newly pulled tag whose container is not running yet; the
mutable `Config.Image` input is diagnostic only. Live launch attestation
requires the selected policy reference to appear in the running image's observed
digests. An admitted plan replaces an idle OAuth binding's cached launch, while
a binding with an active host lease keeps its launch until the lease drains;
binding updates and lease acquisition lock the same durable binding row.

Bootstrap and resolved-image evidence belongs to one deployment. Explicit
`MOONMIND_OMNIGENT_BOOTSTRAP_STATE_PATH` and
`MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH` values select the sole write
destination, and test containers mask repository runtime state with private
storage so their startup probes cannot consume or replace live host evidence.

### 7.6 Claude Code launch defaults

Workflow-step `claude-native` sessions launch with `--permission-mode
bypassPermissions`, matching the direct launcher's
`--dangerously-skip-permissions`. Nobody answers prompts in a step's terminal,
and launching the workflow is the operator's authorization; Claude Code's
account default (`auto`) would otherwise let its classifier refuse requested
work such as queueing pr-resolver children. Omnigent policy hooks still enforce
deployment deny rules, and a permission flag authored in `terminalLaunchArgs` is
validated and preserved.

The bridge row freezes launch defaults when the attempt is first persisted,
before provider session creation, and both the streaming execution driver and
the canonical OmnigentSession Activity use that decision, including on a retry
after a worker died before attaching the session ID. A retained row without a
saved default is ambiguous, so before another create MoonMind searches the
provider's complete, bounded session inventory by the original MoonMind identity
and reattaches the match. Conflicting ownership, duplicate matches, or an
incomplete lookup report the problem without creating anything; only a
confirmed no-match freezes current defaults and creates a session.

A provider response ending in matched tool output while its session remains
active is not terminal evidence. The execution driver requests same-session
continuation only when its caller explicitly owns that recovery; other hosts
keep polling within the turn deadline while retaining their leases.

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
- a host lease or container-job slot does not replace the profile lease;
- provider-attributed 429/quota evidence updates the shared scope's cooldown policy (see below), never a duplicate per-profile throttle;
- profile capacity is released only after every credential consumer is stopped or safely reconciled.

### 8a. Shared capacity scopes: one accounting, one throttle owner

Profiles that draw from the same upstream allowance share a capacity scope
(`provider_capacity_scopes`, `scope_ref`). A grant needs room under both the
profile ceiling and the scope effective limit, counted with the same
purpose/cost accounting: admitted execution and provider-consuming validation
spend the scope, local credential repair does not, and uncommitted or
unreconciled grants stay spent until the durable ledger resolves them. OAuth
exclusivity and machine/worker capacity stay separate layers.

- Active consumption stays attributed to the scope admitted with the lease. A
  Profile edit that moves it to another scope is persisted as pending and
  applied after the affected use drains, and new admissions against the old
  scope stay blocked meanwhile so fresh leases cannot hold the old budget open.
  New limits constrain new grants without terminating existing work. A stale
  scope refresh (older generation) cannot roll back a newer limit or re-enable a
  disabled scope.
- The scope is the single throttle owner
  (`additive-increase-multiplicative-decrease@1`). A validated 429 report,
  checked against the actual attempt, Profile, acquired scope, and generation,
  halves the effective limit once per cooldown episode and arms the scope
  deadline; duplicate or conflicting reports in the same episode cannot halve
  again or extend it, while a valid later provider deadline is honored.
  Credential failure, host exhaustion, unavailable verification, and upstream
  quota stay distinct causes.
- Cooldown expiry reopens bounded attempts up to the reduced limit through
  normal admission rather than declaring full health. Recovery adds at most one
  slot per 300 seconds up to the configured ceiling and never revives a disabled
  scope. Missing or corrupt records are an affected-scope reconciliation problem,
  never positive capacity inferred from profile maxima.
- The `get_state` query projects the same scope aggregates admission uses
  (`configured_limit`, `effective_limit`, used units, wait/cooldown evidence),
  without secrets.

---

## 9. Host, session, and policy capacity

Execution may be constrained by several independent layers:

1. Provider Profile account capacity;
2. profile-bound host count;
3. sessions per host;
4. aggregate generic-host count and cold-launch rate;
5. container-job slots;
6. image and runtime resource policy;
7. network and egress policy;
8. workspace and mount availability;
9. approval policy.

The status projection identifies the blocking layer. Counters are not conflated, and success at one layer does not bypass another.

Generic-host preadmission checks the aggregate host ceiling and cold-launch rate
against the durable host-lease ledger. A short control Activity reports the
limiting layer, and workflow timers own the wait without occupying an execution
Activity or consuming a host attempt. An existing owned lease is admitted
unconditionally; atomic allocation remains the final fence. Container-job
capacity waits use durable timers under the original timeout.

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

Publication evidence follows [Workflow Publishing](../Workflows/WorkflowPublishing.md). At this boundary:

- When publication adds no commits, PR discovery requires the workflow's previously accepted publication head for the same workflow, repository, branch, and exact commit, and the GitHub PR head must still match it before the URL crosses the durable result boundary. An unchanged shared base grants no discovery authority.
- An unchanged checkout whose exact saved commit is an ancestor of the advanced remote base emits `no_commits` with `remoteVerified=false`. That proves saved work, not equality with the newer base, so exact-head consumers reject it as a write source. Owned-candidate reuse still requires the exact remote head; diverged history and unreadable remote evidence are failures.
- Before publication the workspace owner fetches the authored base into its remote-tracking ref with the same repository credential, so a candidate-only clone stays publishable. An unavailable base stops publication before candidate mutation and never substitutes another branch.

---

## 11. Runtime filesystem and network policy

### 11.1 Direct managed runtimes

Direct managed runtimes receive a workflow-scoped workspace and artifact area, runtime-specific credential materialization, immutable Skill projection, and bounded temporary state. Runtime-owned environment values take precedence over untrusted passthrough values.

Worker-side reads of session state, log spools, canary markers, and Git identity
configuration reject symlink ancestors and non-regular endpoints. JSON and config
reads are bounded. Support files and identity changes replace a fresh inode in a
pinned parent directory, preventing symlink and hardlink redirection. Skill
adapter creation and cleanup use the same directory boundary. Invalid canary
markers are not published as artifacts. Container-task environment values are
passed only as Docker container settings; they never change the worker Docker
process environment, executable search path, loader, or daemon selection.

Direct-runtime launches inherit only standard execution context (paths, login,
locale, temporary directories, proxy and TLS configuration) and `MOONMIND_URL`.
Provider credentials and settings are materialized from the selected profile;
worker database, signing, cloud, and unrelated environment values are not
forwarded. Root-launched Claude processes use native pre-exec UID/GID changes,
with supplementary groups cleared, before profile environment values can affect
the executable or dynamic loader.

A runtime whose normalized `requiredCapabilities` include `execution.fanout` (derived from a resolved Skill's `sideEffect.kind: enqueue_children`) receives a separate short-lived fan-out bearer after policy authorization; [Required Capabilities §7.5](../Workflows/RequiredCapabilities.md#75-executionfanout) owns its authority and API limits. The Run workflow carries the mint authorization from immutable resolved-Skill provenance in `stepExecution.skillSourcePolicy.executionFanout`; repo/local Skills and top-level-only declarations are denied before launch, and an absent field is a replay marker, not a current default. Launch context also carries `MOONMIND_REPOSITORY_CONNECTION_REF` from the canonical workspace request when the parent declares repository connection authority; ambient or Provider Profile environment cannot substitute another connection. Profile-bound Omnigent hosts expose the bearer through a lease-owned read-only file and pass only its non-secret selector to runner and login-shell environments, and restricted egress additionally requires the fan-out marker and bearer on the exact create and child-describe paths. Hosts without the requirement receive neither.

Merge automation resolver children require `git`, `gh`, and `docker` through
the capability mechanism, including omitted and historical templates that name
only repository tools; additional authored requirements stay intact. `docker`
identifies the API-owned Docker Backend, not a daemon socket or host Docker
credentials, and ordinary admission authorizes the child-scoped test capability
before launch. Temporal patching preserves the capability payload of
already-recorded child starts; no live token or persisted plan is amended to
retrofit missing authority.

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

Artifact publication preserves a verifier's typed acceptance contract across the
Activity-to-workflow projection. The full report stays authoritative in
artifact storage; compact metadata keeps the exact subject, source scope,
mandatory requirement identities, completion target, and freshness policy, with
requirement evidence pointing at the published report. Projection never invents
a verdict, fills missing requirements, or removes expiry. Initial publication
and size-triggered compaction use one projection that keeps gate evidence when
auxiliary metadata must be dropped.

An accepted assessment artifact remains the controlling input for downstream
issue steps. Implementation and publication steps consume its durable reference
and the associated issue brief; their local assessment-path arguments do not
declare new outputs or authorize replacement of that assessment. A fresh
workspace need not reproduce the producer's local files. The initial assessment
producer still must supply its declared verdict and brief before advancing.

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

Direct Codex managed sessions emit equivalent bridge-facing evidence, so Workflow Detail and recovery need no runtime-specific UI model. Process-local live buffers may optimize delivery, but they are not the cross-process durability boundary.

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

GitHub issue attempts carry a version-2 comment lease in the trusted brief and
canonical AgentRun request. AgentRun confirms ownership before launch and renews
the same comment on the cadence owned by
[GitHub Issue Status State Machine](../Workflows/GitHubIssueStatusStateMachineDesign.md).
A run still queued for provider or host capacity has not started work, so its
reservation lapses rather than holding the issue. Loss of the last confirmed
lease cancels execution through the runtime's preservation and cleanup owner;
expiry grants no workspace-deletion or completion authority.

---

## 15.1 Execution budget

An agent run has one execution budget, resolved from `timeoutPolicy` by a single
authority and enforced identically by the `MoonMind.AgentRun` poll loop and the
managed process supervisor, which receives it in the launch request.

| Field | Meaning |
|---|---|
| `timeout_seconds` | Base window. A run that cannot demonstrate progress ends here. |
| `max_timeout_seconds` | Hard ceiling. No observed progress extends a run past this. |
| `progress_stall_seconds` | How long observed progress may go stale before an over-base run is treated as stuck. |
| `execution_budget_mode` | `progress_aware` or `flat`. |

Omitted fields resolve to kind-specific defaults; the ceiling defaults to a
multiple of the base window, so a tight requested budget keeps a tight ceiling.
The stall window never exceeds the base window (a run's own start counts as an
observation, so a wider stall window would erase the base window), and the base
window is bound by the same absolute cap as the ceiling.

**Mode travels with the numbers.** A history that predates progress-awareness
publishes its flat decision, not just its base window. Otherwise a re-resolving
callee (every launch and retry) could derive a ceiling the workflow never
granted, and the supervisor would keep a process running after the workflow had
timed out and released its provider slot.

**Elapsed wall-clock alone is not evidence that a run is stuck.** A run ends for
budget only when the base window has elapsed with no progress observed within
`progress_stall_seconds` (or ever), or when the hard ceiling is reached. The two
outcomes are reported distinctly, because the operator responses differ (raise
the ceiling versus investigate a wedged runtime), and the result records the
budget and the condition that ended it.

Monotonic intervals resist host clock changes; persisted wall-clock timestamps
correlate runtime evidence. Before ending for lack of progress, the supervisor
samples CPU/workspace activity and the workflow checks fresh canonical status.

**One allowance per attempt.** Execution and automatic recovery (cooldown
re-entry, process-loss successor, terminal-contract continuation, profile-bound
retry) share one cumulative allowance; slot, capacity and cooldown waits and
pause before dispatch stop the clock without resetting it. Status, cancel and
fetch Activities during execution derive their timeouts from the remaining
allowance in one place; a fetch timeout at the deadline yields the normal budget
result with its original diagnostics. Confirming an already-terminal run and
saving its result keep their catalog-bounded timeouts, even after expiry, and
start no task work. MoonMind.Run step retries charge failed attempts against
the step's hard ceiling (§16.1); a rerun gets its own allowance.

**One recovery owner.** Adapters, providers and successor builders only propose
recovery; the AgentRun workflow admits at most one per result (cooldown,
successor or terminal stop). It never repeats a `delivery_unknown` effect,
never re-attempts a `no_retry` failure (auth rejection, permission denial,
invalid choice) and admits nothing once the allowance is spent. Results record
`recoveryDecisions` and `consumedExecutionSeconds`. The hard ceiling ends
execution without further reconciliation.

### Progress evidence

Progress is observed evidence, never inferred from elapsed time. The supervisor
publishes the newest of three signals as `last_log_at` in canonical status
metadata: runtime output on stdout/stderr, the newest meaningful file mtime
under the run workspace, and cumulative process-tree CPU time. No single signal
suffices: a print-mode CLI emits nothing until its turn completes, and a test
suite or compiler may write only to ignored directories for tens of minutes.
CPU activity covers both while distinguishing them from a process blocked on a
dead socket or input. CPU accounting is cumulative over the session lifetime, so
a series of short-lived child processes never reads as stalled. A livelocked
runtime reads as progress and is contained by the hard ceiling. The no-progress
watchdog uses the same observation, so the two cannot disagree.

Resources that must outlive the run, such as credential lifetimes and broker
sockets, are sized from the ceiling plus a bounded allowance for launch
preparation, never from the base window. An Activity `ScheduleToClose` is sized
from the ceiling only when the workflow can re-evaluate the budget while the
Activity runs. When the workflow is blocked on one long Activity and cannot
observe progress, `ScheduleToClose` is sized from the base window, or a run that
never progresses could hold its provider slot for the full ceiling.

---

## 16. Retry, replay, and reconciliation

### 16.1 Activity retry

An activity retry reuses the same canonical request and idempotency key. It inspects durable run/provider/session/host state before creating side effects.

Credential lifecycle identity includes the admitted AgentRun run ID and its
admission epoch. The provider-capacity owner remains the child workflow ID.
A reset can therefore start epoch one under the same capacity owner without
reusing a previous run's cleaned credential handles. Activity redelivery retains
its identity. An existing runtime binding keeps its exact recorded credential
authority across worker changes; historical tickets without a run ID retain
their recorded identity. Cleanup tombstones never authorize reactivation.

A revoked Activity delivery cannot settle its canonical turn command. It leaves
the claim and runtime binding for the replacement delivery to reconcile. User
cancellation and unknown provider responses retain their distinct cancellation
and delivery-ambiguity handling.

An interrupted generic host allocation that has not created a provider session
keeps its fenced cleanup owner. Once cleanup reaches `cleaned` with a durable
attestation and canonical settlement, the owner may return
`metadata.admissionRecovery` (`agent-admission-recovery/v1`), a typed control
receipt binding the execution plan, admission epoch, runtime binding, and
cleanup attestation. The workflow validates it against the admitted request and
re-enters the bounded capacity re-admission loop; the next epoch keeps the
original request, workspace, provider, and model and cannot resurrect the
cleaned binding. The receipt is persisted as the attempt result, so a lost
acknowledgement repeats no cleanup, and it never represents provider execution
or task success. Cleanup consumes the cumulative execution allowance
(replacement Activities get only the remaining time, and exhaustion returns the
failed attempt), while durable capacity queueing stays outside it. A fenced
completion leaves cleanup pending and releases no capacity. Missing, malformed,
stale, or cross-attempt receipts authorize nothing; an active provider session
keeps its reattachment path. Historical results without this metadata keep
their recorded behavior, so replay never reinterprets old cleanup hints or
error strings as authority.

Direct managed-session state is written through a monotonic revision
compare-and-swap boundary. A provider turn is admitted while holding the state
authority lock from the final locator/revision check through persistence of the
accepted turn identifier, so concurrent control actions cannot create an
accepted but untracked turn; longer provider observation stays outside the
lock. A publication based on a superseded revision fails as a managed-session
locator mismatch, and the caller reloads the authoritative epoch, thread, and
container before deciding whether to retry. Observers never roll state back to
an older epoch or thread. A deliberate replacement of a missing, stale, or
superseded container authorizes exactly that transition while preserving the
logical session, epoch, thread, workspace, and revision chain.

### 16.1.1 Terminal-contract continuation

Terminal-contract continuation is semantic recovery, not an Activity retry. It
reuses the immutable Skill, profile, policy, and workspace authority, carries a
bounded corrective instruction naming the missing evidence, and records each
attempt and outcome. Same-session continuation is preferred when the capability
snapshot supports it. Otherwise a directly managed one-shot lane may relaunch a
replacement process against the same run-owned workspace. No continuation may
silently create a fresh checkout or switch source authority.

### 16.2 Workflow replay

Changes that add, remove, or reorder workflow commands use Temporal patch/version markers so in-flight histories replay to the recorded command path.

Temporal resets preserve dynamic remediation child identities from the run
identity recorded by the policy-compilation Activity. A new reset execution ID
does not rename children already present in history. Continue-as-new executions
retain their own recorded policy identity.

### 16.3 Rerun and re-resolution

A rerun reuses the original immutable Skill and policy/profile snapshots by default. Explicit re-resolution is a distinct operator or workflow action.

### 16.4 Runtime reconciliation

Each lane reconciles its own side effects:

- external providers reconcile remote ids and callback state;
- direct managed runtimes reconcile process/container/session records and supervisors;
- Omnigent reconciles bridge attempts, first-message markers, profile bindings, host leases, deterministic containers, registered hosts, sessions, credential generations, and janitor work.

No lane silently creates replacement authority while the original may still be active.

The scheduled host janitor bounds OAuth and generic-host cleanup independently.
A historical credential-generation failure stays fenced and visible while
unrelated leases continue. Generic reclamation needs positive closure evidence
for the exact Temporal workflow/run owner; stale age alone cannot release a host.
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

Omnigent checkpoint extraction assigns restored files and directories to the
selected runtime identity before launch; archive ownership is not runtime
authority. Restore keeps ordinary executable bits, so scripts stay runnable and
unchanged Git candidates stay clean, while regular-file access becomes
owner-only and privilege bits and imported hooks are removed. Contained relative
symlinks are kept as link text, including dangling links to excluded inputs,
empty directories, or absent files, but escaping links, cycles, and other
resolution errors are rejected. Any of these failures aborts restore before
readiness. Injected input artifacts keep their read-only projection, and
excluded Skill inputs go through the current run's own admission.

An admitted runtime capability snapshot freezes three independent planes: agent
identity and session continuity, repository workspace ownership, and host
realization. For profile-bound Omnigent, Omnigent owns session reattach evidence
while MoonMind owns the sandbox `WorkspaceLocator` and repository checkpoint
capture, validation, and restore. An `external_state_ref` proves session
continuity only and cannot satisfy workspace restore preflight. Recovery uses
the versioned, digested snapshot admitted with the run, not one rewritten after
registry or credential changes.

Generic workspace capture keeps the authored repository and source branch in
the compact recovery request. A successful save may carry an auxiliary
`repository-worktree-state/v1` observation (repository, source branch, exact
head, clean worktree including untracked files, archive digest) outside the
versioned checkpoint; failing to capture it does not undo successful
preservation. The issue recovery owner combines it with fenced runtime cleanup
and fresh remote ancestry evidence before releasing a no-work claim. Alone it
grants no release or publication authority.

For Omnigent, checkpoint identity may include profile, provider-lease, credential-generation, binding, host-lease, host, bridge-session, Omnigent-session, idempotency, first-message, workspace-locator, diagnostics, terminal, and artifact refs. Credentials and daemon paths are forbidden.

Recovery chooses:

- **live reattach** only when the original authority, generation, host, session, and first-message evidence remain valid; or
- **cold restore** by reacquiring the selected profile, creating new host/session authority, and materializing validated artifact-backed state.

A branch receives independent authority and never concurrently reuses the original mutable OAuth lease.

A Checkpoint Branch turn has one durable MoonMind owner. Before any lease, host,
session, message, or repository side effect it validates the pinned source and
checkpoint, parent-turn lineage, immutable instructions, git binding, stored
execution/profile/policy authority, and current credential generation, then
persists stable identities and dispatches the canonical `external/omnigent`
request with `branch_from_checkpoint`. Retry and replay reattach to those
identities, and authority is released in the normal release-last order
([Checkpoint Branch System](../Workflows/CheckpointBranchSystem.md)).

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

Deterministic CI and credentialed live evidence are separate gates. CI proves schema, idempotency, lifecycle ordering, fake-server protocol behavior, artifact structure, cleanup selection, redaction, and replay without claiming provider credentials were exercised. Credentialed live conformance runs `tools/run_omnigent_live_conformance.py`; [Conformance and Live Smoke](../Omnigent/ConformanceAndLiveSmoke.md) and [Omnigent Adapter](../Omnigent/OmnigentAdapter.md) own its inputs and evidence rules. A repository test backend or bare success boolean is never live proof.

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

For Omnigent, a workflow selects `external/omnigent` with a Provider Profile; MoonMind compiles the execution plan and launch policy, acquires capacity, resolves a canonical workspace, materializes or validates exactly one compatible stock host, proves credential and harness readiness, authorizes one bridge session, posts the first message once, projects durable conversation and lifecycle evidence, harvests artifacts, cleans owned runtime state, and releases Provider Profile capacity last. Static and on-demand host modes are interchangeable at the workflow contract boundary while preserving explicit policy, readiness, evidence, and cleanup differences.
