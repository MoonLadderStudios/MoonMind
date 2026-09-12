# Activity Catalog and Worker Topology

Status: Implemented in core runtime (catalog live; some target-state families still pending)
Last updated: 2026-08-04
Scope: Defines MoonMind’s canonical **Activity Types**, **worker fleets**, **Task Queue routing**, and the operational rules for executing artifacts, planning, skills, integrations, managed runtime supervision, and related Temporal-side support work.

## Related docs

- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](./ManagedAndExternalAgentExecutionModel.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](./WorkflowArtifactSystemDesign.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Temporal/ErrorTaxonomy.md`](./ErrorTaxonomy.md)

---

## 1. Purpose

MoonMind uses Temporal’s abstractions directly:

- **Workflow Executions** orchestrate.
- **Activities** perform all side-effecting work.
- **Task Queues** are internal routing labels for worker fleets. They are **not** a product-level queue abstraction, and MoonMind makes no FIFO guarantees to users.

This document standardizes:

- activity families and naming
- which worker fleet owns which activity family
- the routing and timeout/retry model for those activities
- the contract boundary between workflow code and activity implementations
- the current implemented catalog and the target-state additions that are still pending

This document covers the **Temporal-managed worker model** only.

### Executable worker specification and readiness

Every fleet is constructed from one immutable `WorkerSpec`. For the workflow
fleet, the same class and activity tuples drive the Temporal SDK `Worker`,
startup logs, `/readyz`, diagnostics, tests, and the registry fingerprint.
Operator-facing workflow type lists are derived from those executable classes;
an independent advertised string catalog is forbidden.

`/healthz` proves only that the process event loop is responsive. `/readyz`
remains unavailable until configuration and the executable specification are
built, the Temporal client is connected, workers are constructed, and pollers
have started. Its bounded response includes task queues, registered workflow and
activity types, build/deployment identity, registry fingerprint, versioning
state, and resolver-core identity. `MoonMind.PRResolver` may appear only when
that exact workflow class was supplied to the SDK worker. Its registration is
replay support for previously recorded histories, not an active host-selection
surface for new `pr-resolver` runs.

Worker topology construction validates the canonical catalog against the
concrete runtime-handler inventory before any fleet starts polling. A handler
without a catalog route, or a catalog route without its owning handler, is a
startup/readiness failure rather than a runtime workflow-task failure. The
capability-routed `mm.tool.execute` alias and the separately registered
workflow-fleet helper are explicit ownership exceptions.

The workflow fleet remains Temporal-only. It receives no repository mutation,
GitHub credential, sandbox, local-git, or agent-runtime capabilities.

Production workers load code and the resolved release manifest from one
immutable image. Build identity is a content digest; source revision is separate
provenance. Source overlays belong in `docker-compose.development.yaml`. A
development freshness scan runs off the event loop with one in-flight scan and
bounded cached evidence. Unknown or stale evidence makes readiness unavailable;
it does not block liveness or Temporal progress. Supervisors reuse each child's
single readiness response, including diagnostics returned with HTTP 503.

`release.inspect` is registered on every affected worker queue and verifies the
installed content digest. The deployment controller waits for Temporal to
register all candidate queues before admitting its pinned cross-queue canary.
`release.reconcile` runs on the existing deployment-control fleet from scheduled
storage maintenance. It resumes durable updater owners and retires temporary
cohorts only after verifying their authority and server drainage evidence. Other
storage maintenance failures cannot starve this owner. Full promotion and
retention semantics belong to [Docker Compose Deployment Update System](../Steps/DockerComposeUpdateSystem.md).

---

## 2. Goals and non-goals

## 2.1 Goals

1. Provide a stable activity taxonomy for:
 - artifact lifecycle
 - planning
 - executable tool execution
 - external integrations
 - managed runtime supervision
 - provider-profile coordination support
 - review support
 - future agent skill resolution and materialization

2. Define worker fleets with clear:
 - capability boundaries
 - security boundaries
 - scaling expectations

3. Ensure activity contracts are:
 - retry-safe
 - observable
 - cancel-aware where needed
 - payload-disciplined

4. Keep Task Queue usage minimal and operational.

5. Make canonical contract boundaries explicit so workflow code does not perform provider-specific coercion.

## 2.2 Non-goals

This document does **not** define:

- workflow lifecycle semantics
- product-level queue ordering or priority semantics
- source-precedence rules for agent skill resolution
- provider-specific business logic beyond what is needed to define activity boundaries

---

## 3. Core principles

### 3.1 Determinism boundary

Workflow code must remain deterministic. Any nondeterminism belongs in Activities, including:

- network calls
- filesystem access
- subprocesses
- clocks and random values
- external provider inspection
- mutable runtime state reads

### 3.2 Stable public surface

Activity Type names are long-lived contracts. Prefer adding new activity types over changing semantics in place.

### 3.3 Routing by capability

Workers are split by what they can safely do:

- artifact I/O
- LLM access
- sandbox execution
- provider integration access
- managed runtime execution

They are not split by legacy product nouns.

### 3.4 Payload discipline

Large inputs and outputs belong in the artifact system. Activity payloads should contain refs and compact metadata, not blobs.

### 3.5 Canonical return-shape rule

For true agent-runtime execution, activities must return canonical runtime contracts directly.

That means:

- `integration.<provider>.start` returns `AgentRunHandle`
- `integration.<provider>.status` returns `AgentRunStatus`
- `integration.<provider>.fetch_result` returns `AgentRunResult`
- `integration.<provider>.cancel` returns `AgentRunStatus`
- `agent_runtime.status` returns `AgentRunStatus`
- `agent_runtime.fetch_result` returns `AgentRunResult`

Workflow code should not reconstruct canonical contracts from provider-shaped payloads.

### 3.6 Search Attribute ownership

Activities do **not** upsert Search Attributes or workflow memo state directly. They return results to workflows, and workflows own visibility updates.

### 3.7 Continue-As-New awareness

Activities must not assume a stable Temporal `run_id` across the entire logical lifetime of a workflow. `correlation_id` is the durable business identifier across Continue-As-New boundaries.

---

## 4. Task queues (routing only)

Temporal requires Task Queues so Workers can poll. MoonMind uses them strictly as internal routing plumbing.

## 4.1 Current queue set

Queue names below are routing addresses, not deployment units. A queue name,
a worker instance (poller), a process, a container, and an enabled deployment
profile are different things: multiple queues can share one process, and
multiple replicas can serve one queue. Do not maintain an independent
numerical total elsewhere; the authoritative inventory is derived from the
production registries and resolved settings (see #3959).

### Workflow task queues

- `mm.workflow.user.v2` (default start queue; `TemporalSettings.user_workflow_v2_task_queue`; new `MoonMind.UserWorkflow` starts and replay-patched child workflows route here under the `renamed_contract` mode)
- `mm.workflow` (replay/poll address; `TemporalSettings.workflow_task_queue`; the workflow fleet keeps polling it for pre-patch in-flight histories via `get_workflow_poll_task_queues()`)
- `mm.workflow.merge_automation` (`TemporalSettings.merge_automation_workflow_task_queue`; hosts the merge-automation workflow registration in `workflow_registry.py`)

### Activity task queues

- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.integrations`
- `mm.activity.agent_runtime`
- `mm.activity.agent_runtime.control` — a real worker queue polled by the
  same agent-runtime service with independent bounded concurrency so
  terminal-evidence evaluation (`agent_runtime.evaluate_terminal_evidence`)
  stays schedulable while fan-out launches occupy the long-lived execution
  queue. It is **not** in `client.py::_MOONMIND_TASK_QUEUES`, which scopes
  only drain metrics and batch Pause/Resume fan-out, not the full worker
  topology.

The workflow poll topology (`get_workflow_poll_task_queues()` in
`activity_catalog.py`: default start queue plus the replay queue) is wider
than the drain/fan-out scope (`_MOONMIND_TASK_QUEUES` in `client.py`), which
covers only drain metrics and batch Pause/Resume fan-out. The intended
contract derives the inventory from production registries and resolved
settings through #3959 (generated catalogs); until that lands, the list above
is hand-maintained against `client.py`, `config/settings.py`, and
`workflow_registry.py` and must not be copied into a second numerical total
elsewhere.

## 4.2 Queue policy

MoonMind starts with a minimal queue set.

Examples of deliberate non-decisions:

- no provider-specific LLM subqueues by default
- no priority lanes by default
- no queue-per-tool explosion
- no queue-per-provider unless isolation or scaling truly demands it

Rule of thumb: subdivide only when you need different secrets, different egress, different scaling behavior, or materially different isolation. The agent-runtime control queue is a bounded exception within the same worker fleet: short authority handoffs such as terminal-evidence evaluation must remain schedulable while fan-out launches occupy the long-lived execution queue.

---

## 5. Worker fleets

The activity catalog maps activity types onto the following fleets.

| Fleet | Queue(s) | Primary capabilities | Primary privileges |
|---|---|---|---|
| `workflow` | `mm.workflow.user.v2`, `mm.workflow` | workflow execution, limited helper activities | Temporal only |
| `artifacts` | `mm.activity.artifacts` | artifact lifecycle, provider-profile support, OAuth session support | artifact storage, DB-backed support services |
| `llm` | `mm.activity.llm` | planning, validation, review, generic LLM work | model/provider credentials |
| `sandbox` | `mm.activity.sandbox` | repo and command execution | isolated process execution |
| `integrations` | `mm.activity.integrations` | external provider APIs and repo operations | provider tokens, egress to provider APIs |
| `agent_runtime` | `mm.activity.agent_runtime`, `mm.activity.agent_runtime.control` | managed runtime launch and supervision; isolated terminal authority handoffs | isolated runtime execution, auth volume mounts |

## 5.1 Workflow fleet exception rule

The workflow fleet is primarily for workflow code. It may also host **small helper activities** when needed to preserve deterministic workflow behavior without creating unnecessary routing complexity.

Current registration (`workflow_registry.py::workflow_fleet_activity_handlers`,
verified against the function body — eight handlers):

- immutable-release identity probe from `workflows/release_canary.py`: `release.inspect` reads the installed release manifest without credentials or external mutation.
- adapter/metadata helpers from `workflows/agent_run.py`: `integration.resolve_adapter_metadata`, `integration.get_activity_route`, `integration.resolve_external_adapter`, `integration.external_adapter_execution_style`
- checkpoint-persistence handlers from `workflows/checkpoint_branch_turn.py` (via `checkpoint_branch_activity_handlers()`): `checkpoint_branch.turn.mark_running`, `checkpoint_branch.turn.persist_terminal`, `checkpoint_branch.turn.persist_terminal_rejection` — retained for replay/in-flight compatibility of pre-cutover histories; no new calls route there.

New-write routing, retained compatibility, and the final topology are
distinct states:

- **New writes** schedule `checkpoint_branch.turn.*` on the artifacts fleet
  (`mm.activity.artifacts`) behind the `checkpoint-branch-artifact-fleet-v1`
  patch marker. The catalog (`activity_catalog.py`) and the artifacts worker
  binding (`activity_runtime.py`) own that route.
- **Retained compatibility** keeps the same handler implementations
  registered on the workflow fleet only so pre-cutover histories recorded
  without a queue override can replay and drain. Fixture replay proves
  history compatibility, not deployed drainage.
- **Final topology** removes the workflow-queue persistence registration,
  its dead dependency injection, and now-unneeded permissions only after
  old consumers have a verified disposition. Queue separation is not privilege separation: the retained handlers still share the workflow
  worker process and its I/O authority until that removal lands.

Removal is owned by the drain gate in
`moonmind/gates/checkpoint_compat_drain.py`
(`evaluate_checkpoint_compat_drain_observations`, contract
`checkpoint-branch-artifact-fleet-drain-v1`), which reuses the canonical
`evaluate_worker_drain` predicate (`outstanding == 0` → safe to remove).
Deployment probes enter through `collect_checkpoint_compat_drain_observations`
/ `CheckpointCompatDrainObservations` /
`evaluate_checkpoint_compat_drain_observations`: any dimension that is
unobservable (`None`: missing visibility or failed probe) retains compat
and is named in `blocking_dimensions`. `render_checkpoint_compat_drain_report`
renders the exact operator procedure (visibility queries, history-marker
inspection, removal checklist). Scoped visibility counts come from
`TemporalExecutionService.get_drain_metrics` with the workflow task queue
passed explicitly. Drained means all three
deployment-observed dimensions reach zero:

- open pre-cutover histories recorded without the
  `checkpoint-branch-artifact-fleet-v1` marker (`get_drain_metrics`
  scoped to the workflow task queue, filtered to pre-marker histories);
- pending `checkpoint_branch.turn.*` activity tasks still addressed to
  the workflow queue;
- retained histories with an undischarged supported-reset obligation.

Fixture replay is history-compatibility evidence, not deployed drainage;
missing visibility or failed probes are not a clean drain and keep the
registration retained.

### Actual process permission boundary (consolidated topology)

Until the drain gate above releases the compat registration, the workflow
worker process intentionally carries database and artifact-retention
authority (`async_session_maker`, `CheckpointBranchService`, retained
artifact refs in `workflows/checkpoint_branch_turn.py`) because the
retained handlers execute old persistence tasks in that process. The four
`agent_run.py` metadata helpers need none of it (proven behaviorally by
`test_checkpoint_compat_drain_3949.py`, which runs all four helpers with
database I/O denied and provider/Docker/artifact configuration removed,
while the persistence handlers fail closed).
New-only workflow processing must carry only what its real helpers require;
while the topology stays consolidated, the justified permission set is exactly
the retained handlers' persistence authority plus the helpers' catalog
and registry reads — and the drain gate above is what retires the
persistence half.

Measured capability inventory (workflow fleet):

| Handler | Needs database | Needs provider/Docker/artifact-storage I/O |
|---|---|---|
| `release.inspect` | no | no (installed release manifest read only) |
| `integration.resolve_adapter_metadata` | no | no (registry + settings read only) |
| `integration.get_activity_route` | no | no (catalog read only) |
| `integration.resolve_external_adapter` | no | no (registry read only) |
| `integration.external_adapter_execution_style` | no | no (registry read only) |
| `checkpoint_branch.turn.mark_running` | yes (`mark_turn_running`) | yes (durable turn row) |
| `checkpoint_branch.turn.persist_terminal` | yes (`lock` + `finalize`) | yes (artifact retention + result/diagnostics writes) |
| `checkpoint_branch.turn.persist_terminal_rejection` | yes | yes (rejection row terminalization) |

Under bounded concurrent load the consolidated worker retains every
handoff's control record before its cleanup record with the drain gate
staying decisive per input (`test_consolidated_worker_retains_control_and_cleanup_progress_under_load`;
rehearsal, not production saturation proof). A stdlib-only decision-level
companion (`test_checkpoint_drain_saturation_3949.py`) extends the same
property to 100 concurrent workflow-decision handoffs with per-turn
control-before-cleanup ordering. Temporal execution-under-load proof
(concurrent `CheckpointBranchTurn` executions retaining control/cleanup
progress under saturation) remains integration scope and requires either
required-CI execution evidence or explicit reviewer acceptance of these
rehearsals plus the retry/timeout budgets below. New persistence is proven to
reach the artifacts fleet exclusively by
`test_new_{success,failure,cancellation}_reaches_artifacts_fleet_with_real_handlers_3949`
plus `test_transient_terminal_retry_reuses_owned_row_on_artifacts_fleet_3949`,
which bind the real handler objects only on the artifacts worker and assert
the serving queue, timeouts, retry budget, and durable row state from the
recorded history.

This registration is the current state, not the intended end state. The
intended least-privilege boundary keeps the workflow fleet Temporal-only with
no artifact, provider-mutation, or runtime-supervision I/O; whether
checkpoint persistence belongs beside deterministic workflows is the
implementation concern tracked in #3949. Do not read the registration above
as approval for broad workflow-fleet I/O.

This is a narrow exception, not a second general-purpose activity plane.

---

## 6. Naming conventions

Activity Type names use dotted namespaces.

### 6.1 Canonical namespaces

- `artifact.*` — artifact lifecycle
- `plan.*` — planning and plan validation
- `mm.skill.execute` — default registry-dispatched executable tool path
- `sandbox.*` — shell, repo, and workspace actions
- `integration.<provider>.*` — provider-specific external integrations
- `repo.*` — provider-backed repo operations exposed as general-purpose activities
- `provider_profile.*` — provider-profile coordination support
- `oauth_session.*` — OAuth session lifecycle support
- `agent_runtime.*` — managed runtime launch/supervision/result/cancel operations
- `step.review` — review gate execution

### 6.2 Implemented skill namespace

- `agent_skill.*` — skill resolution/materialization family, implemented in
  `workflows/agent_skills/agent_skills_activities.py` (`AgentSkillsActivities`)
  and registered in the live catalog (`activity_catalog.py`) on the
  agent-runtime fleet. See §8.11 for the per-operation table.

Portable instruction bundles (agent skill sets: `SKILL.md` plus supporting
files, resolved into immutable snapshots) and executable tool contracts
(`tool.type = "skill"` invocations dispatched through the tool router) remain
different concepts. Native workflow/activity infrastructure provides execution
substrate only — resolution, materialization, scheduling, artifacts, and
approvals — and must not reimplement Skill semantics such as data collection,
classification, or completion rules.

---

## 7. Contract model

Activity contracts should be small, business-focused, and explicit.

## 7.1 General request shape guidelines

Common business fields include:

- `correlation_id`
- `idempotency_key`
- `input_refs[]`
- `parameters`
- compact selector/config fields

Do not duplicate Temporal execution metadata into every payload unless an external contract truly requires it.

## 7.2 Canonical agent contract references

For true agent-runtime activities, the canonical schema source of truth is:

- `AgentExecutionRequest`
- `AgentRunHandle`
- `AgentRunStatus`
- `AgentRunResult`

as defined in `moonmind/schemas/agent_runtime_models.py`.

## 7.3 Where provider-specific data belongs

Provider-specific details belong in canonical `metadata` fields, not alternate top-level response shapes.

Examples of acceptable metadata:

- provider URLs
- normalized provider status labels
- callback support flags
- PR URLs
- merge outcomes
- tracking refs

Examples of unacceptable workflow-facing top-level variants:

- ad hoc `{external_id, tracking_ref}` instead of `AgentRunHandle`
- raw provider status blobs instead of `AgentRunStatus`
- custom provider result payloads instead of `AgentRunResult`

---

## 8. Current implemented activity catalog

This section describes the current implemented families and their role.

## 8.1 Artifact activities (`artifact.*`)

Purpose: artifact lifecycle management.

Current implemented activities include:

- `artifact.create`
- `artifact.write_complete`
- `artifact.read`
- `artifact.list_for_execution`
- `artifact.compute_preview`
- `artifact.link`
- `artifact.pin`
- `artifact.unpin`
- `artifact.lifecycle_sweep`

Worker queue: `mm.activity.artifacts`

Key rules:

- large content stays in artifact storage
- writes must be retry-safe
- artifact references are the durable interface used by workflows and other activities

## 8.2 Plan activities (`plan.*`)

Purpose: plan generation and validation.

Current implemented activities:

- `plan.check_preset_capabilities`
- `plan.generate`
- `plan.validate`

Worker queue: typically `mm.activity.llm`

Key rules:

- planning is always nondeterministic, therefore always an activity
- plan outputs are stored as artifacts, not placed directly into workflow history
- saved-schedule capability checks read scoped preset metadata before planning and
  return compact readiness diagnostics; they do not modify the schedule or grant
  capabilities (see [Required Capabilities](../Workflows/RequiredCapabilities.md))

## 8.3 Tool execution (`mm.skill.execute`)

Purpose: execute a registry-defined executable tool through the default dispatcher path.

Current implemented activity:

- `mm.skill.execute`

Routing is determined by registry metadata and capability class.

Key rules:

- tool execution remains separate from true agent-runtime execution
- the pinned registry snapshot is the source of truth for routing and policies

## 8.4 Sandbox activities (`sandbox.*`)

Purpose: isolated repo and process execution.

Current implemented activities:

- `sandbox.checkout_repo`
- `sandbox.apply_patch`
- `sandbox.run_command`
- `sandbox.run_tests`

Worker queue: `mm.activity.sandbox`

Key rules:

- strong isolation
- explicit concurrency limits
- heartbeat support for long-running operations
- careful retry handling to avoid duplicate side effects

## 8.5 Provider-profile support activities (`provider_profile.*`)

Purpose: support the managed-runtime provider-profile lifecycle.

Current implemented activities:

- `provider_profile.list`
- `provider_profile.ensure_manager`
- `provider_profile.reset_manager`
- `provider_profile.verify_lease_holders`
- `provider_profile.sync_slot_leases`

Worker queue: `mm.activity.artifacts`

These are support activities used by workflow and manager orchestration. They are not part of the end-user skill or agent contract surface.
`provider_profile.reset_manager` remains registered only for replay compatibility
and performs non-destructive ensure/recovery; it never terminates the lease
authority workflow.

## 8.6 OAuth session activities (`oauth_session.*`)

Purpose: OAuth session preparation, update, verification, and cleanup.

Current implemented activities:

- `oauth_session.ensure_volume`
- `oauth_session.start_auth_runner`
- `oauth_session.stop_auth_runner`
- `oauth_session.update_terminal_session`
- `oauth_session.update_status`
- `oauth_session.verify_volume`
- `oauth_session.verify_cli_fingerprint`
- `oauth_session.register_profile`
- `oauth_session.mark_failed`
- `oauth_session.cleanup_stale`

Worker queue: `mm.activity.artifacts`

## 8.7 Integration activities (`integration.<provider>.*`)

Purpose: external provider interaction and delegated agent execution.

### Current implemented provider families

- `integration.jules.*`
- `integration.codex_cloud.*`
- `integration.openclaw.execute`
- `integration.omnigent.execute`

Capability labels and execution visibility metadata are not provider activity
families by themselves. For example, Jira is available as the
`integration:jira` capability through trusted Jira tool surfaces and
merge-automation activities, but there is no `integration.jira.start` /
`integration.jira.status` external monitor family.

### Jules and Codex Cloud contract pattern

Current canonical pattern:

- `integration.jules.start(...) -> AgentRunHandle`
- `integration.jules.status(...) -> AgentRunStatus`
- `integration.jules.fetch_result(...) -> AgentRunResult`
- `integration.jules.cancel(...) -> AgentRunStatus`

and likewise for `integration.codex_cloud.*`

### Streaming-gateway contract pattern

`integration.openclaw.execute(...)` and `integration.omnigent.execute(...)` are special-case single-call external execution paths for providers using the streaming-gateway execution style.

Contract:

- `integration.openclaw.execute(...) -> AgentRunResult`
- `integration.omnigent.execute(...) -> AgentRunResult`

Worker queue: `mm.activity.integrations`

### Integration helper activity

Current helper activity:

- `integration.resolve_adapter_metadata`

Contract:

- validate adapter registration
- return adapter metadata such as execution style
- keep env inspection and dynamic provider registration reads out of deterministic workflow code

This activity is intentionally small and is not part of the true agent-run status/result contract family.

## 8.8 Repo activities (`repo.*`)

Purpose: provider-backed repository operations used by workflow or runtime flows.

Current implemented activities:

- `repo.create_pr`
- `repo.merge_pr`

Worker queue: `mm.activity.integrations`

## 8.9 Managed runtime activities (`agent_runtime.*`)

Purpose: managed runtime launch, supervision support, owner-authorized workspace
checkpoint capture, result collection, artifact publication, and cancellation.

Current implemented activities:

- `agent_runtime.launch`
- `agent_runtime.capture_workspace_checkpoint`
- `agent_runtime.launch_session`
- `agent_runtime.prepare_turn_instructions`
- `agent_runtime.publish_artifacts`
- `agent_runtime.session_status`
- `agent_runtime.send_turn`
- `agent_runtime.steer_turn`
- `agent_runtime.interrupt_turn`
- `agent_runtime.clear_session`
- `agent_runtime.terminate_session`
- `agent_runtime.fetch_session_summary`
- `agent_runtime.publish_session_artifacts`
- `agent_runtime.reconcile_managed_sessions`
- `agent_runtime.status`
- `agent_runtime.fetch_result`
- `agent_runtime.restore_workspace_checkpoint`
- `agent_runtime.evaluate_terminal_evidence`
- `agent_runtime.cancel`

Worker queues: `mm.activity.agent_runtime` for long-lived execution and
`mm.activity.agent_runtime.control` for terminal-evidence evaluation. Both are
polled by the same agent-runtime service with independent bounded concurrency,
so fan-out work cannot starve the terminal authority handoff and no additional
always-on container is required.

### Contract expectations

- `agent_runtime.status(...) -> AgentRunStatus`
- `agent_runtime.capture_workspace_checkpoint(...) -> compact worktree archive and manifest refs`
- `agent_runtime.fetch_result(...) -> AgentRunResult`
- `agent_runtime.restore_workspace_checkpoint(...) -> ManagedWorkspaceRestoreResult`
- `agent_runtime.cancel(...) -> AgentRunStatus`
- `agent_runtime.launch_session(...) -> ManagedSessionHandle`
- `agent_runtime.session_status(...) -> ManagedSessionHandle`
- `agent_runtime.prepare_turn_instructions(...) -> str | prepared-instructions payload`
- `agent_runtime.send_turn(...) -> ManagedSessionTurnResponse`
- `agent_runtime.steer_turn(...) -> ManagedSessionTurnResponse`
- `agent_runtime.interrupt_turn(...) -> ManagedSessionTurnResponse`
- `agent_runtime.clear_session(...) -> ManagedSessionHandle`
- `agent_runtime.terminate_session(...) -> ManagedSessionHandle`
- `agent_runtime.fetch_session_summary(...) -> ManagedSessionSummary`
- `agent_runtime.publish_session_artifacts(...) -> ManagedSessionArtifactsPublication`
- `agent_runtime.reconcile_managed_sessions(...) -> reconciliation summary payload`
- `agent_runtime.evaluate_terminal_evidence(...) -> AgentRunResult`

`agent_runtime.publish_artifacts` should return a canonical-result-compatible enriched payload that can be materialized as `AgentRunResult`.

Temporal workers also keep `agent_runtime.ensure_docker_sidecar` registered as a
non-retryable rejection for durable pre-cutover histories. It is not a supported
capability and cannot create a daemon, socket, graph volume, or container.

`agent_runtime.capture_workspace_checkpoint` accepts only a typed managed-runtime
locator, resolves it through the managed run store, and supports Codex CLI
`worktree_archive` capture. Archive and manifest bodies remain in artifact
storage. Capture support does not imply restore support or Resume eligibility.

`agent_runtime.launch` is an internal launch/support activity rather than a public canonical runtime contract in the same sense as `status` and `fetch_result`.

The session-oriented activities are remote-session contracts. They must delegate through a session controller or adapter boundary and must not fall back to the worker-local managed runtime launcher/process loop.

`agent_runtime.restore_workspace_checkpoint` is the `codex_cli` cold-restore data plane. It runs only on `mm.activity.agent_runtime`, verifies checkpoint, archive, manifest, repository base, path containment, and restored entries before atomically activating a new managed workspace, and persists compact restoration evidence. A launch carrying a restoration requirement is rejected unless the destination is `ready` and its checkpoint and capability digest match.

The session-oriented activity surface is intended to become runtime-neutral at the workflow boundary, but the live activity/controller path currently admits Codex CLI only. Runtime-specific protocol details remain behind the session adapter/controller boundary. The current container transport uses the Codex App Server-compatible remote-session protocol for the Codex binding. Claude Code must add a Claude-specific session adapter/controller before carrying `runtimeFamily = "claude_code"` or recording `runtimeId = "claude_code"` through this activity surface.

`agent_runtime.prepare_turn_instructions` is replay-visible when scheduled by
`MoonMind.AgentRun`. Moving it before or after session launch/status activities,
or before or after `agent_runtime.send_turn`, changes Temporal command order.
Such changes must be guarded by Temporal patch/version markers or Worker
Versioning so in-flight histories keep their recorded order while new histories
use the current order. Activity retry cannot repair a workflow-task
nondeterminism caused by an unguarded order change.

## 8.10 Review activities

Current implemented activities:

- `step.review`

Queues:

- `step.review` → `mm.activity.llm`

These are support families, not agent-runtime families.

## 8.11 Skill activities (`agent_skill.*`)

Purpose: resolve portable instruction bundles into immutable snapshots and
materialize those snapshots for runtime consumption. Workflows consume refs,
not inline skill content.

Actually registered and wired operations (five; verified against
`agent_skills_activities.py`, `activity_catalog.py`, and
`activity_runtime.py` — not by catalog membership alone):

| Activity | Owning contract (`schemas/agent_skill_models.py`) | Production wiring | Remaining gap |
|---|---|---|---|
| `agent_skill.resolve` | `SkillSelector` → `ResolvedSkillSet`; persists file-backed content and the resolved manifest via the artifact dependency | Catalog entry (agent-runtime fleet); runtime binding `("agent_skills", "resolve_skills")`; called from `MoonMind.UserWorkflow` (`workflows/run.py` resolves the selector per node) | On-demand injection path below is still gated; resolve callers pass `allow_repo_skills=False, allow_local_skills=False` |
| `agent_skill.query_on_demand` | `SkillsOnDemandQueryRequest` → `SkillsOnDemandQueryResult` via `SkillsOnDemandService`, gated on `settings.workflow.skills_on_demand_enabled` | Catalog entry; runtime binding present | No production workflow caller; behavior verified by unit tests only, not through a production boundary |
| `agent_skill.request_on_demand` | `SkillsOnDemandRequest` → `SkillsOnDemandRequestResult` via the same gate | Catalog entry; runtime binding present | Same as above |
| `agent_skill.build_prompt_index` | `ResolvedSkillSet` → prompt-injectable string | Catalog entry; runtime binding present | No production workflow caller; current body renders refs/metadata, not full prompt bundles |
| `agent_skill.materialize` | `(ResolvedSkillSet, runtime_id, mode, workspace_root)` → `RuntimeSkillMaterialization` via `AgentSkillMaterializer` | Catalog entry; runtime binding present | No production workflow caller; per-runtime materialization through a production boundary is unverified |

Do not describe this family as "future": the operations above are registered
and bound. Do not describe it as fully supported either: only `resolve` has a
production workflow caller, and resolve/materialize/on-demand injection must
be verified through production boundaries before any "supported" claim. The
'supported' bar is a workflow-boundary test exercising the real invocation
shape, not catalog membership or helper unit tests.

The intended contract keeps executable tool contracts and portable
instruction bundles distinct and requires per-operation
production-boundary verification; the table above records current wiring
vs gaps.

---

## 9. Retired target-state note (resolved by §8.11)

The `agent_skill.*` family was previously listed here as future work. It is
now documented as implemented-but-partially-wired in §8.11 (five registered
operations; only `agent_skill.resolve` has a production workflow caller). The
general rules below still apply: resolution semantics stay centralized,
materialization may vary by runtime, and workflows consume refs, not inline
skill content.

## 9.1 (reserved)

---

## 10. Routing rules

Workflows choose activities through Activity Options and catalog-derived routing metadata.

## 10.1 Capability mapping

Each activity family maps to one capability class and one fleet.

Representative capability classes include:

- `artifacts`
- `llm`
- `sandbox`
- `integration:<provider>`
- `agent_runtime`
- `workflow` (for narrow helper activity exceptions)

## 10.2 Selection examples

- `plan.generate` routes to `mm.activity.llm`
- `sandbox.run_tests` routes to `mm.activity.sandbox`
- `integration.jules.start` routes to `mm.activity.integrations`
- `agent_runtime.fetch_result` routes to `mm.activity.agent_runtime`
- `agent_runtime.restore_workspace_checkpoint` routes to `mm.activity.agent_runtime`
- `agent_runtime.evaluate_terminal_evidence` routes to `mm.activity.agent_runtime.control`
- `provider_profile.list` routes to `mm.activity.artifacts`
- `integration.resolve_adapter_metadata` routes to `mm.workflow`

## 10.3 No workflow-side route probing

Workflow code should use the live catalog as the routing source of truth. The system should not grow additional ad hoc workflow-side probing or provider-specific routing heuristics when the catalog can express the routing directly.

---

## 11. Reliability contracts

## 11.1 Timeout defaults by family

Typical defaults by family:

- `artifact.*` — short
- `plan.*` — moderate
- `sandbox.*` — longer, often heartbeat-required
- `integration.*` — short per request; long-running external work should be modeled as start/status/fetch or async completion
- `agent_runtime.*` — moderate, with short status reads and bounded launch/fetch/cancel windows

## 11.2 Retry policy rules

- use bounded exponential backoff
- prefer non-retryable classification for invalid inputs or unsupported contract states
- avoid retries that duplicate destructive sandbox side effects
- ensure external starts are idempotent
- ensure canonical contract normalization failures are treated as contract errors, not silently tolerated
- treat model-provider rate limits as retryable-with-policy

Activity-level retry for model-provider rate limits may be used only when it is
bounded and safe. Managed agent runtimes should prefer orchestration-aware
retry: classify the failure, back off with jitter, honor provider retry hints,
persist bounded attempt metadata, and surface exhausted rate-limit failures in
the step summary.

## 11.3 Heartbeats

Heartbeat-required activities should be explicitly marked in the catalog.

Representative heartbeat-required cases include:

- long-running sandbox operations
- long-running streaming gateway operations
- managed runtime launch/publish operations where progress visibility is required

Short status reads should remain short and should not become long-running heartbeat loops.

## 11.4 Idempotency

Rules:

- side-effecting activities accept or derive stable idempotency keys
- artifact writes remain naturally retry-safe through integrity checks
- external starts must not create duplicate jobs on retry
- managed launches must not create duplicate runtime executions on retry
- `agent_skill.materialize` must be safe under retry and must not mutate checked-in source trees in place. Known gap: when the workspace already contains a repo-authored `.agents/skills` directory, `AgentSkillMaterializer._project_builtin_support_directory()` still projects the `_shared` support directory into that repo-owned path, so the current implementation does not fully enforce the non-mutation invariant yet.

---

## 12. Security model

### 12.1 Least privilege per fleet

- sandbox workers do not hold provider API keys by default
- integration workers do not run arbitrary shell commands
- artifact workers do not need sandbox execution privileges
- agent runtime workers have stronger execution privileges but a narrower responsibility set

### 12.2 Network controls

- LLM fleet can reach model endpoints
- integrations fleet can reach provider APIs
- sandbox fleet should have restricted egress
- agent runtime fleet should only have the runtime/proxy/network access it actually needs

### 12.3 Secret handling

- use secret managers or controlled durable auth volumes where appropriate
- do not place raw credentials into workflow payloads, artifacts, or logs
- do not leak provider tokens through metadata or diagnostics artifacts

### 12.4 Data handling

- large content stays in artifacts
- workflow history carries only refs and compact metadata
- previews and redaction are handled through artifact-layer controls, not by bloating workflow payloads

---

## 13. Observability requirements

## 13.1 Logging

Every activity log line should include enough context to answer:

- which workflow/run initiated the activity
- which activity type ran
- which attempt this is
- which correlation ID and idempotency key were involved

At minimum:

- `workflow_id`
- `run_id`
- `activity_type`
- `activity_id`
- `attempt`
- `correlation_id`
- `idempotency_key` or a hash of it

Large logs belong in artifacts.

## 13.2 Metrics

Per fleet, track:

- queue backlog and lag
- execution latency
- retry counts
- failure reasons
- resource usage where relevant
- repeated timeout/retry patterns worth operator attention

For agent-runtime and integration activities, metric dimensions should align with canonical contract states rather than provider-specific raw states.

## 13.3 Tracing

If using OpenTelemetry:

- propagate correlation IDs through activities
- annotate spans with workflow/run identifiers
- keep provider-specific noise out of top-level span naming where possible

---

## 14. Testing strategy

1. **Activity contract tests**
 - validate canonical request and response schemas
 - ensure provider activities return canonical contracts

2. **Worker fleet integration tests**
 - verify activity-to-fleet routing
 - verify helper activities remain narrow and intentional

3. **Load tests**
 - sandbox concurrency and isolation
 - LLM rate limiting correctness
 - managed runtime queue behavior

4. **Failure injection**
 - provider outages
 - artifact store outages
 - worker restarts mid-activity
 - manager restart and lease recovery paths

5. **Canonical contract enforcement**
 - reject unknown provider statuses at the adapter/activity boundary
 - ensure workflows do not depend on provider-shaped payloads
 - ensure metadata carries provider-specific details without breaking canonical top-level schemas

6. **Traceability gate**
 - catalog changes must stay aligned with runtime code, docs, and tests

---

## 15. Decided questions

### 15.1 Provider-specific LLM task queues

Deferred. Start with one `mm.activity.llm` queue. Split only when operational isolation or scaling demands it.

### 15.2 Priority lanes

Deferred for v1. Throughput control comes from concurrency and rate limiting, not queue ordering guarantees.

### 15.3 Search Attributes from activities

Disallowed. Workflows own visibility state.

### 15.4 Workflow fleet helper activities

Allowed only as a narrow exception. Current registration: the seven handlers
listed in §5.1 (four adapter/metadata helpers plus three
replay-compatibility checkpoint handlers). The intended boundary stays
least-privilege; see #3949 for the checkpoint-persistence question.

### 15.5 Canonical runtime contract enforcement

Decided. The activity boundary, not workflow code, owns normalization into `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`.

---

## 16. Summary

MoonMind’s Temporal activity topology is organized around a small number of capability-based fleets:

- workflow
- artifacts
- llm
- sandbox
- integrations
- agent_runtime

The catalog is already live for:

- artifact lifecycle
- planning
- executable tool dispatch
- sandbox work
- provider integrations
- provider-profile support
- OAuth session support
- managed runtime supervision
- review gates

The key architectural rule for current and future work is:

- **Activities own side effects**
- **The catalog owns routing**
- **Canonical runtime contracts cross the workflow boundary**
- **Workflow code should not perform provider-specific coercion**

That keeps MoonMind’s Temporal model easier to reason about, easier to test, and easier to evolve without regressions.
