# MoonMind Temporal Architecture

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-TemporalArchitecture.md`](../tmp/remaining-work/Temporal-TemporalArchitecture.md)

**Status:** Draft (migration-oriented, but core runtime shape is now live)  
**Owner:** MoonMind Platform  
**Last updated:** 2026-04-14  
**Audience:** backend, infra, dashboard, workflow authors

---

## 1. Purpose

This document defines MoonMind’s Temporal architecture at the bridge point between:

- the system that still exists today
- the system that is already implemented in core Temporal paths
- the target state MoonMind is moving toward

This document is intentionally a **bridge document**. It should describe the runtime honestly rather than pretending the migration is either unfinished everywhere or fully complete everywhere.

This document explains how MoonMind’s Temporal-native architecture incorporates:

- executable tools
- plans
- artifacts
- managed and external agent runs
- provider profiles
- agent skills and resolved skill snapshots
- adapters
- compatibility layers for task-oriented product surfaces

Detailed agent-skill storage, precedence, and workspace path rules live in `docs/Tasks/AgentSkillSystem.md`.

Detailed true-agent execution lifecycle rules live in `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`.

---

## 2. Related docs

- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/ArtifactPresentationContract.md`
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/MoonMindArchitecture.md`
- `docs/ManagedAgents/CodexManagedSessionPlane.md`
- `docs/ManagedAgents/LiveLogs.md`
- `docs/ManagedAgents/DockerOutOfDocker.md`
- `docs/Tools/JiraIntegration.md`
- `docs/Tasks/AgentSkillSystem.md`

---

## 3. Current state and target state

## 3.1 Current state

MoonMind currently operates with:

- a FastAPI control plane and task-oriented Mission Control product surface
- Postgres-backed app state plus MinIO-backed artifacts and Qdrant-backed retrieval/memory services
- Temporal foundation services and multiple worker fleets in Docker Compose
- Temporal-backed orchestration for core execution paths, including `MoonMind.Run`, `MoonMind.AgentRun`, `MoonMind.AgentSession`, `MoonMind.ManagedSessionReconcile`, `MoonMind.ProviderProfileManager`, and `MoonMind.OAuthSession`
- a **Codex-first** task-scoped managed-session plane, with `MoonMind.AgentSession` and session activities carrying live Codex session contracts today
- a dedicated `agent_runtime` fleet that owns managed runtime supervision and is also the current Docker-capable boundary for managed-session launch and control-plane-launched specialized workload containers
- artifact-first, session-aware observability for managed runs, with task/session continuity projected from artifacts and bounded workflow metadata rather than container-local state
- compatibility layers and older substrate history that are still present while Temporal-backed flows continue to absorb more of the runtime surface

This document should not flatten that reality into “fully migrated” or “not migrated.” The honest state is mixed: Temporal is already the durable orchestration direction, while some compatibility and migration surfaces remain live.

## 3.2 Target state

**Architecture precept:**  
*MoonMind execution is Temporal-native. MoonMind adds domain contracts above Temporal—Tool, Plan, Artifact, Agent Skill / Skill Set, Agent Adapter, Provider Profile, Session Policy, Runner Profile—but does not introduce a parallel orchestration substrate.*

In the target state:

- Temporal **Workflow Executions** are the durable orchestration primitive
- Temporal **Activities** perform all side effects
- Temporal **Visibility** is the list/query/count source for Temporal-managed work
- Temporal **Schedules** are the authoritative recurring-start mechanism
- managed runtimes run as **task-scoped session containers** launched from runtime-specific images through MoonMind adapters
- specialized non-agent Docker work stays in a **sibling workload plane**, launched through MoonMind’s control plane as tools or curated workload activities rather than being conflated with session identity
- artifacts remain **execution-centric**, and task/session/workload views are read models over execution-linked evidence
- managed-run observability is **artifact-first and session-aware**; live follow is optional and never the durable source of truth
- secret-bearing downstream mutations execute at **trusted MoonMind-side tool boundaries**, not by handing raw credentials to managed runtime shells
- MoonMind keeps only domain concepts Temporal does not provide directly:
  - **Tool**
  - **Plan**
  - **Artifact**
  - **Agent Skill / Skill Set**
  - **Agent Adapter**
  - **Provider Profile**
  - **Session Policy**
  - **Runner Profile**

## 3.3 Non-goals

- claiming every legacy compatibility path is already retired
- rebuilding product vocabulary entirely around raw Temporal terms
- exposing Temporal task queues as user-visible queue ordering semantics
- reintroducing a second MoonMind-specific orchestration layer on top of Temporal
- collapsing managed session containers, specialized workload containers, and true agent runs into one blurred execution category
- treating terminal embedding as the primary observability model for managed runs

---

## 4. Locked platform decisions

The following are already locked and should be treated as canonical here:

- **Deployment mode:** self-hosted
- **Deployment runtime:** Docker Compose by default
- **Temporal persistence + visibility:** PostgreSQL
- **Default artifact backend:** MinIO / S3-compatible storage
- **Task queue posture:** routing only, not product semantics
- **Default queue set:** start small; add subqueues only when isolation or scaling demands it
- **Agent orchestration scope:** one MoonMind orchestration layer spans managed and external agents
- **Managed runtime model:** task-scoped session containers launched from independently versioned runtime images
- **Current concrete managed-session implementation:** Codex-first; Claude Code and Gemini CLI remain managed-runtime targets and future session-plane adopters, not current peer `MoonMind.AgentSession` implementations
- **Managed runtime separation:** dedicated `agent_runtime` fleet is canonical
- **Current Docker-capable boundary:** `docker-proxy` plus the `agent_runtime` fleet are the current local launch path for managed sessions and specialized workload containers
- **Docker workload identity rule:** specialized workload containers are a sibling workload plane, not session identity and not `MoonMind.AgentRun` unless the launched workload is itself a true agent runtime
- **Root agent execution wrapper:** `MoonMind.AgentRun` is canonical for true agent runs
- **Root managed-session wrapper:** `MoonMind.AgentSession` is canonical for task-scoped managed sessions
- **Durable truth rule:** artifacts plus bounded workflow metadata are the operator/audit truth; supervision stores and container-local state are recovery aids or caches only
- **Observability rule:** managed-run Live Logs are a MoonMind-owned, artifact-first, session-aware projection; `xterm.js` is reserved for OAuth and other interactive terminal auth flows
- **Secret boundary rule:** secret-bearing integrations such as Jira execute through trusted MoonMind-side tool handlers that resolve SecretRefs just in time; raw secrets do not belong in managed runtime shells, workflow payloads, logs, or artifacts
- **Runtime compatibility rule:** runtime compatibility is adapter-defined, not base-image-defined
- **Canonical runtime contract rule:** agent-facing runtime activities return canonical contracts directly; workflow code should not depend on provider-shaped payloads

This document should not reopen those choices.

---

## 5. Vocabulary and compatibility model

## 5.1 User-facing vs internal terms

MoonMind still needs two layers of vocabulary during migration:

- **user-facing term:** `task`
- **Temporal/runtime term:** `workflow execution`

Rationale:

- MoonMind’s product surface remains task-oriented
- Temporal’s substrate is workflow-oriented
- forcing a total rename would create unnecessary compatibility churn

## 5.2 Recommended wording

- use **task** in current dashboard and compatibility APIs
- use **workflow execution** in implementation and runtime docs
- use **task queue** only for Temporal plumbing, never as a user-visible ordering promise

## 5.3 Identifier compatibility

During migration, multiple identifiers may appear:

| Identifier | Meaning | Status |
| --- | --- | --- |
| `taskId` | MoonMind product/compatibility handle | Required during migration |
| `runId` | Historical compatibility identifier in some surfaces | Transitional only |
| `workflowId` | Temporal Workflow ID | Canonical for Temporal-managed executions |
| `temporalRunId` | Temporal run instance identifier | Detail/debug only |

Rules:

- task-oriented APIs may return both `taskId` and `workflowId`
- `workflowId` is the durable Temporal identity
- `temporalRunId` is not the primary product handle

## 5.4 Tool vs agent-skill terminology

MoonMind explicitly uses:

- **tool** for executable capabilities
- **agent skill** for instruction bundles
- **ResolvedSkillSet** for the immutable artifact-backed execution context produced from those skill sources

This document must not blur those concepts.

## 5.5 Managed session vs workload terminology

MoonMind explicitly distinguishes:

- **managed session container** for a task-scoped managed runtime session owned by `MoonMind.AgentSession`
- **workload container** for a specialized non-agent Docker workload launched through the control plane
- **true agent run** for an execution owned by `MoonMind.AgentRun`

A workload container does not become a managed session merely because Docker is involved, and a managed session does not become the owner of every sibling container launched on behalf of the task.

---

## 6. Architecture overview

## 6.1 Current deployment shape

MoonMind currently contains:

- API service and Mission Control surfaces
- Postgres, MinIO, and Qdrant
- Temporal services in Compose
- workflow, artifacts, llm, sandbox, integrations, and agent_runtime worker fleets
- `docker-proxy` plus shared workspace/auth volumes for the current Docker-capable launch boundary
- task-scoped managed session containers for managed runtimes, with Codex as the live session-plane implementation
- external agent integrations and specialized Docker workload execution under MoonMind control
- compatibility/product surfaces that still present task-oriented views

## 6.2 Temporal-native target shape

For Temporal-managed flows, the architecture becomes:

1. **MoonMind API / Control Plane**
   - authenticates callers
   - starts workflows
   - sends updates, signals, and cancel requests
   - issues artifact upload/download grants
   - accepts task/step runtime, provider-profile, runner-profile, and skill selectors
   - exposes task-oriented compatibility, observability, and session-continuity surfaces where needed

2. **Temporal service**
   - stores workflow state and history
   - provides visibility for list/query/count
   - owns timers, retries, schedules, and child workflow orchestration

3. **Agent Orchestration Layer**
   - owns `MoonMind.Run`, `MoonMind.AgentRun`, `MoonMind.AgentSession`, `MoonMind.ManagedSessionReconcile`, `MoonMind.ProviderProfileManager`, and `MoonMind.OAuthSession`
   - translates operator intent into workflow-safe control semantics
   - coordinates provider-profile policy, approvals, recovery, and canonical contract normalization across managed and external agents

4. **Managed Session Plane**
   - runs task-scoped managed runtime session containers from runtime-specific images
   - reuses continuity across steps within a task when policy allows
   - exposes session identity through bounded metadata such as `session_id`, `session_epoch`, `container_id`, `thread_id`, and `active_turn_id`
   - keeps continuity/performance state as cache rather than durable truth

5. **Docker Workload Plane**
   - launches specialized non-agent workload containers through MoonMind-owned tools or curated activities
   - uses runner profiles, explicit mounts, network/device policy, and controlled Docker access
   - returns normal tool results plus artifacts rather than session identity

6. **Artifact + Observability system**
   - stores large inputs/outputs outside workflow history
   - links artifacts to workflow executions
   - publishes stdout/stderr/diagnostics, session continuity artifacts, workload outputs, previews, and operator-facing read models
   - supports MoonMind-owned live follow as a secondary convenience surface

7. **Compatibility adapters and read models**
   - bridge task-oriented UI/API contracts onto Temporal-backed workflows where needed
   - project task/session/workload views from workflow state, bounded metadata, and artifacts without becoming a second durable orchestration substrate

## 6.3 Domain concept boundaries

To prevent ambiguity across the architecture:

- **Tool** = executable Temporal-facing capability
- **Plan** = MoonMind execution graph or ordered work definition
- **Step Ledger** = compact workflow-owned state for current/latest run step truth
- **Artifact** = large durable input/output outside workflow history
- **Agent Skill** = deployment-scoped instruction bundle
- **ResolvedSkillSet** = immutable artifact-backed run/step context
- **Agent Adapter** = provider/runtime translation layer
- **Provider Profile** = managed-runtime credential/concurrency/policy binding
- **Session Policy** = reuse/reset/isolate/recreate hint for managed session behavior
- **Managed Session** = task-scoped continuity envelope owned by `MoonMind.AgentSession`
- **Session Continuity** = artifact-backed summary/checkpoint/control/reset evidence for a managed session
- **Runner Profile** = curated policy definition for an allowed workload-container shape
- **Workload Container** = specialized non-agent container launched through the control plane against the task workspace

---

## 7. Container reference

All Temporal-related containers are defined in `docker-compose.yaml`.

The current Compose shape includes:

- `temporal`
- `temporal-namespace-init`
- `temporal-admin-tools`
- `temporal-ui` (profile-gated)
- `temporal-worker-workflow`
- `temporal-worker-artifacts`
- `temporal-worker-llm`
- `temporal-worker-sandbox`
- `temporal-worker-agent-runtime`
- `temporal-worker-integrations`
- `docker-proxy`
- `postgres`
- `minio`
- `qdrant`

The important architectural rule is not the exact container table but the separation of concerns:

- Temporal server owns durable orchestration substrate
- worker fleets map to capability and security boundaries
- artifact storage is separate from workflow history
- `docker-proxy` is the controlled Docker daemon boundary
- the `agent_runtime` fleet is the current Docker-capable launch boundary for managed sessions and specialized workload containers
- runtime images and workload containers stay separate from generic MoonMind worker images

### 7.1 Worker fleet summary

| Fleet | Queue | Primary role |
| --- | --- | --- |
| `workflow` | `mm.workflow` | deterministic orchestration code plus narrow helper activities |
| `artifacts` | `mm.activity.artifacts` | artifact lifecycle plus provider-profile and OAuth support work |
| `llm` | `mm.activity.llm` | planning, validation, review, other generic LLM activity work |
| `sandbox` | `mm.activity.sandbox` | repo checkout, patching, shell/test commands, and other non-Docker process execution |
| `agent_runtime` | `mm.activity.agent_runtime` | managed session launch/supervision/status/control/result, session observability publication, and the current Docker-backed specialized workload launch path |
| `integrations` | `mm.activity.integrations` | external providers, repo publish/merge, callbacks/webhooks, and other trusted non-runtime integration helpers |

### 7.2 Managed runtime separation and current Docker placement

The `agent_runtime` fleet is not aspirational anymore.

It is part of the canonical architecture. In the current local Compose shape, that fleet is also attached to `docker-proxy` and the shared workspace/auth volumes. That makes it the current launch boundary for:

- managed session containers
- current Docker-backed specialized workload containers

This does **not** mean every Docker-backed workload becomes a managed session. Identity remains separate.

### 7.3 Session container vs workload container

A managed session container owns `session_id`, `session_epoch`, `container_id`, `thread_id`, and `active_turn_id`.

A workload container may carry optional association metadata such as `session_id`, `session_epoch`, or `source_turn_id`, but it remains a step-scoped workload execution whose primary outputs are tool results and artifacts.

That distinction must stay explicit in workflow code, APIs, and UI surfaces.

---

## 8. Substrate evolution

Execution is moving toward **Temporal as the single durable orchestration substrate** for new flows, while preserving compatibility for task-oriented product surfaces during migration.

Important direction:

- new durable orchestration belongs in Temporal
- compatibility layers may continue temporarily
- task, session, workload, and observability surfaces should be projections over workflow state, bounded metadata, and artifacts rather than alternative durable substrates
- legacy substrate retirement should be driven by parity and operational confidence, not by design intent alone

Phase-style rollout notes remain appropriate for the tracking documents, but this document should reflect the fact that the core Temporal architecture is already the active direction and not just a paper target.

---

## 9. Concept mapping: MoonMind to Temporal

| MoonMind concept | Temporal-aligned concept | Notes |
| --- | --- | --- |
| Task | Workflow Execution plus MoonMind compatibility row | User may still see `task` |
| Plan | Plan artifact plus workflow orchestration logic | Plan remains a MoonMind domain concept |
| Step | Plan node executed as activity call or child workflow | Isolation boundary decides which |
| True agent step | `MoonMind.AgentRun` child workflow | Canonical true-agent lifecycle wrapper |
| Managed session | `MoonMind.AgentSession` child workflow plus bounded session snapshot metadata | Task-scoped continuity envelope |
| Session continuity | Execution-linked artifacts such as summary/checkpoint/control/reset refs | Not container-local truth |
| Workload container | Tool/workload activity invocation plus runner profile and artifacts | Sibling plane, not session identity |
| repo/deployment/local agent skills | `ResolvedSkillSet` artifact-backed execution context | Resolved before runtime use |
| provider profile | workflow-coordinated runtime credential/concurrency contract | Important for managed runtimes |
| runner profile | workload launch policy consumed by activities | Important for Docker-backed specialized workloads |
| approval policy | signal/update + workflow policy evaluation | Policy remains a MoonMind concern |
| live logs timeline | projection over observability events and artifacts | Not execution truth |
| runtime selection | activity routing + adapter/provider selection | Not a root workflow taxonomy |
| degraded-mode state snapshot | artifact-backed and reconciliation-friendly | Not execution truth |

## 9.1 Adapter vs workflow boundary

A strict boundary exists between adapters and workflows:

- **Adapters translate provider/runtime semantics**
- **Workflows own lifecycle semantics**

### Adapters

Adapters:

- normalize provider/runtime state into canonical runtime or session contracts
- handle launch/start/status/fetch/cancel/control translation
- expose capability descriptors and compatibility facts
- materialize resolved skill snapshots and runtime delivery bundles
- preserve runtime-independent resolution semantics
- translate provider-native observability or continuity signals into MoonMind-owned artifacts and events

### Workflows

Workflows:

- own phase progression
- own waiting and polling strategy
- own orchestration-level retries and cooldown loops
- own HITL transitions and control-intent routing
- own durable lifecycle state
- carry refs to resolved skill snapshots, observability artifacts, and session/workload evidence
- distinguish true agent runs, managed sessions, and sibling workload invocations

The architecture should not let workflow code become a provider payload repair layer or a container-lifecycle sidecar.

## 9.2 Truth surfaces and caches

MoonMind intentionally keeps three truth surfaces distinct:

- **operator/audit truth** = artifacts plus bounded workflow metadata
- **operational recovery index** = supervision records such as the current ManagedSessionStore, used for recovery/reconciliation but not operator truth
- **disposable cache** = container-local runtime state, thread stores, scrollback, scratch, and other local session/workload state

Live streaming, session containers, and workload containers may improve continuity and performance, but none of them replace artifact-backed execution truth.

---

## 10. Workflow model

## 10.1 Root-level workflow types

MoonMind should keep **few** workflow types.

Current core catalog includes:

- `MoonMind.Run`
- `MoonMind.ManifestIngest`
- `MoonMind.ProviderProfileManager`
- `MoonMind.AgentRun`
- `MoonMind.AgentSession`
- `MoonMind.ManagedSessionReconcile`
- `MoonMind.OAuthSession`

`MoonMind.Run` is the general entry point and may orchestrate:

- direct tool execution
- plan-driven execution
- external integrations
- managed runtime execution
- provider-profile-aware child agent runs
- task-scoped managed sessions
- control-plane-launched specialized workload containers through tool/workload activities
- long-lived waiting and callback/poll handling

`MoonMind.AgentRun` is the durable lifecycle wrapper for one true agent execution step. For managed runtimes it may attach to or request a `MoonMind.AgentSession`; for external agents it owns delegated execution lifecycle directly.

`MoonMind.AgentSession` is the durable task-scoped managed-session wrapper. It owns one managed runtime session envelope for a task/runtime/profile combination, including launch, reuse, clear/reset, epoch changes, bounded session snapshot state, and final teardown. The current concrete implementation is Codex-first: one task-scoped Docker session container per task, one active Codex thread per session epoch, and no cross-task session reuse.

`MoonMind.ManagedSessionReconcile` is a bounded support workflow that invokes reconciliation activities against supervision records and container state; it is not a product-facing task workflow.

`MoonMind.ProviderProfileManager` is the durable coordinator for provider-profile slots, leases, and cooldowns.

`MoonMind.OAuthSession` owns interactive auth/browser flows. It is also the sanctioned place where PTY/xterm-style interaction remains appropriate.

## 10.2 What not to do

Do not create separate root workflow families just for:

- Codex vs Gemini vs Claude
- queue task vs Temporal workflow execution
- provider brand
- task queue brand
- managed vs external root taxonomy
- specialized workload containers that are better modeled as tool/workload invocations
- session clear/reset events that are better modeled as lifecycle transitions within `MoonMind.AgentSession`

Those are routing and execution concerns, not root orchestration categories.

## 10.3 Execution classes

MoonMind sanctions three execution classes:

1. **Workflow-native agentic loop**  
   The reasoning loop lives in workflow code and each model/tool interaction is an activity.

2. **Delegated true agent runtime**  
   The agent runs outside the workflow and `MoonMind.AgentRun` owns the durable lifecycle. Managed and external agents share the same high-level orchestration model.

3. **Specialized non-agent workload execution**  
   A bounded Docker-backed or otherwise specialized workload runs as a tool/workload activity and returns a normal tool result plus artifacts. It is not a `MoonMind.AgentRun` unless the launched workload is itself a true agent runtime.

For delegated true agent runs, canonical rules are:

- `MoonMind.Run` dispatches one agent step to one `MoonMind.AgentRun`
- managed runtimes may attach to an existing `MoonMind.AgentSession` or request a new one according to session policy
- the current task-scoped managed-session plane is Codex-first, while Claude Code and Gemini CLI remain future adopters of the same pattern where adapters support it
- `clear_session` creates a new continuity epoch and new runtime thread inside the same managed session rather than creating a new root workflow family
- agent-facing runtime activities return canonical contracts directly
- large execution data, observability outputs, and continuity evidence stay in artifacts and bounded metadata

---

## 11. Activity model and worker topology

## 11.1 Core rule

All side effects belong in **Activities**:

- LLM calls
- filesystem and repo operations
- shell/sandbox execution
- artifact I/O
- GitHub, Jira, and other integrations
- callback verification and external polling
- managed runtime launch, supervision, status, control, result, reconciliation, and observability publication
- control-plane launch and cleanup of specialized workload containers
- provider-profile support operations
- OAuth/session support operations
- future agent skill resolution/materialization work

Workflow code remains deterministic.

## 11.2 Minimal queue set

Start with a small queue topology:

- `mm.workflow`
- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.integrations`
- `mm.activity.agent_runtime`

Provider-specific or heavier subqueues remain deferred unless isolation or scaling requires them.

Specialized Docker workloads currently use the existing Docker-capable `agent_runtime` boundary in local Compose. A separate workload fleet is a future isolation option, not a prerequisite for the architectural split between session identity and workload identity.

## 11.3 Routing model

Routing is by capability and security boundary, not by legacy nouns.

Typical routing examples:

- planning/validation/review → `llm`
- repo checkout, patching, shell commands, and non-Docker process execution → `sandbox`
- artifact lifecycle, provider-profile support, and OAuth session support → `artifacts`
- external provider APIs, repo publish/merge, and other trusted non-runtime integrations → `integrations`
- managed runtime launch/status/fetch/cancel/session control, session observability publication, and current Docker-backed specialized workload launch → `agent_runtime`

The default executable-tool path remains registry-driven. The tool definition or live catalog chooses whether a capability executes through `mm.skill.execute`, a curated activity family, or a true agent-runtime contract.

### Workflow helper exception

The workflow fleet may host a narrow helper activity exception, such as adapter metadata resolution, when needed to preserve deterministic orchestration without growing unnecessary routing indirection.

That exception should remain small and intentional.

## 11.4 Trusted tool boundary

Secret-bearing downstream mutations should occur at a trusted MoonMind-side activity or tool-handler boundary.

Examples include Jira-style SaaS mutations where the agent needs the capability to create or transition issues but should not receive the raw credential.

Rules:

- the managed runtime receives a tool capability, not the raw secret
- SecretRefs are resolved just in time inside trusted worker/backend code
- raw credentials must not be copied into workflow payloads, artifacts, logs, diagnostics, or general-purpose agent shells
- strongly typed tool actions are preferred over generic “arbitrary HTTP” mutation surfaces

---

## 12. Payloads and artifacts

## 12.1 Payload discipline

Temporal payloads and workflow history should remain small.

Use:

- `ArtifactRef` values for large inputs/outputs
- compact JSON for workflow parameters and summaries
- canonical runtime contracts for agent execution boundaries
- bounded session snapshot metadata for operator orientation
- compact workload metadata for runner/profile/status context

Do **not** put these into workflow history:

- prompts
- manifests
- diffs
- generated files
- logs
- large command output
- agent skill bodies
- resolved skill manifests
- runtime materialization bundles
- prompt indexes
- raw provider snapshots
- long managed runtime logs
- workload stdout/stderr blobs
- raw secret material or serialized SecretRef resolutions

## 12.2 Artifact system baseline

The default artifact path for Temporal-managed work is:

- MinIO / S3-compatible storage for bytes
- Postgres metadata/index for artifact records and execution linkage

Artifacts remain **execution-centric** and are the authoritative durable evidence for:

- resolved skill snapshots
- managed runtime diagnostics
- provider result snapshots
- stdout/stderr/merged log artifacts
- session continuity artifacts such as `session.summary`, `session.checkpoint`, `session.control_event`, and `session.reset_boundary`
- terminal execution summaries when they are too large for workflow payloads
- specialized workload outputs, logs, reports, packages, and diagnostics

Task, step, session, and workload views are projections over that execution-linked evidence rather than a second durable artifact identity model.

## 12.3 Managed-run observability outputs

Every managed run should publish durable observability outputs, including:

- `stdout_artifact_ref`
- `stderr_artifact_ref`
- `diagnostics_ref`
- optional `merged_log_artifact_ref`
- bounded session snapshot metadata when a managed session plane is active
- latest continuity refs for session summary, checkpoint, control event, and reset boundary when available

Live follow, shared spools, and SSE-like delivery are optional convenience surfaces. They are never the durable source of truth.

## 12.4 Manifest processing best practice

Manifest-heavy flows should exchange refs, not blobs:

1. read artifact only inside an activity boundary
2. parse into an artifact-backed intermediate form
3. validate into an artifact-backed validated form
4. compile into a plan artifact
5. execute from refs

That keeps workflow state small and retry behavior safer.

---

## 13. Visibility and UI model

## 13.1 Current product reality

MoonMind’s product surface is still task-oriented.

Near-term experiences may unify:

- task-style product rows
- Temporal-backed workflow executions
- step-level run evidence surfaces
- Execution Artifacts and Session Continuity projections
- managed-run observability panels backed by MoonMind APIs and artifacts

## 13.2 Target Temporal model

For Temporal-managed work, Temporal Visibility is the list/query/count source of truth.

Canonical Search Attributes include bounded fields such as:

- `mm_owner_id`
- `mm_state`
- `mm_updated_at`
- `mm_entry`
- optional bounded context like `mm_repo` or `mm_integration`

Memo should carry compact presentation metadata such as:

- title
- summary
- selected safe refs or compact metadata

Search attributes should remain bounded. Detail surfaces should use memo, queries, artifacts, and observability APIs rather than overloading visibility.

Operator-facing step progress follows the same split:

- the plan artifact owns planned structure
- workflow query/state owns the live step ledger
- artifacts and `/api/task-runs/*` own rich evidence
- session continuity surfaces own artifact-backed continuity drill-down
- logs and heartbeats must not be the primary transport for step truth

## 13.3 Query, observability, and visibility split

Maintain a strict separation:

- **Visibility + projections** → lists, counts, filtering, history, dashboards
- **Queries** → live execution detail, current progress, step ledger, awaiting reason, active step, intervention point
- **Observability APIs** → stdout/stderr/diagnostics, Live Logs timeline, bounded session snapshot, and continuity refs for a concrete run or step

Live Logs is a projection over durable observability events and artifacts. It is not execution truth.

## 13.4 Managed-run observability surfaces

The task detail experience should present managed-run observability as MoonMind-owned read surfaces such as:

- Live Logs
- Stdout
- Stderr
- Diagnostics
- Artifacts
- optional Session Continuity drill-down

Rules:

- managed-run logs use a MoonMind-native viewer, not an embedded terminal
- `xterm.js` remains appropriate for OAuth and other interactive terminal auth flows only
- the active session snapshot (`session_id`, `session_epoch`, `container_id`, `thread_id`, `active_turn_id`) is orientation metadata, not durable truth
- epoch/reset boundaries should be explicit UI-visible milestones rather than hidden in raw text
- live streaming is optional and secondary; ended runs should remain fully readable from artifacts and durable observability history

## 13.5 Read models are never execution truth

Execution truth remains:

- workflow state and history
- child workflow state
- artifacts
- bounded workflow-owned session snapshot metadata

Read models, live streams, and projections exist for UI optimization and operator clarity only.

Workflows must not depend on them to decide what to do next.

---

## 14. Public API posture

## 14.1 During migration

Public APIs may remain task-oriented where product compatibility still requires it.

That includes:

- `/tasks/*` style list/detail flows
- submission APIs that start Temporal-backed workflows under the hood
- `/api/task-runs/*` observability, artifact, and continuity-detail surfaces for concrete step/run evidence

## 14.2 Temporal-backed operations

When a task is backed by Temporal, public actions map to Temporal-native controls:

- create/start → start workflow execution
- edit → update
- approval, clear/reset, operator message, or other external control → signal or update depending on semantics
- cancel → workflow cancellation
- rerun → explicit new execution or continue-as-new depending on the intended behavior

Read-only detail surfaces for logs, diagnostics, continuity, and artifacts remain projections over workflow state, bounded metadata, and artifact-backed observability rather than separate execution substrates.

## 14.3 Internal vs external API

If MoonMind later adds a direct `/executions` API surface, treat it as:

- internal first, or
- future public surface after compatibility needs are intentionally retired

This document does not assume `/executions` is already the primary product API.

---

## 15. Edits, approvals, and external events

## 15.1 Updates

Use Updates when the caller needs request/response semantics and acceptance decisions, for example:

- input changes
- title updates
- rerun requests
- synchronous session-control requests such as clear/reset when the caller needs immediate acceptance semantics

## 15.2 Signals

Use Signals for asynchronous events such as:

- approval arrival
- GitHub/Jules/provider callbacks
- pause/resume requests
- operator messages or external control intents that do not require immediate request/response semantics
- external completion notifications
- provider-profile slot assignment/release coordination

## 15.3 Approval policy

Approval remains a MoonMind policy concern, not a Temporal-native concept.

Temporal supplies transport and durability; MoonMind workflows enforce policy semantics.

---

## 16. Scheduling and long-lived monitoring

For Temporal-managed flows:

- use Temporal Timers for deterministic waiting
- use Temporal Schedules for recurring starts
- prefer callback-first integration patterns where reliable
- use polling only as a bounded fallback when callbacks are absent or untrusted

The old scheduler substrate is no longer the authoritative design direction for recurring Temporal-managed starts.

---

## 17. Reliability and security

## 17.1 Reliability rules

- activity retries are the default low-level recovery mechanism
- side-effecting activities must be idempotent or keyed for safe retry
- use Continue-As-New to control workflow history growth
- keep large payloads out of history
- skill resolution/materialization work must be idempotent or safe under retry
- retries must not silently drift to “latest” skill versions
- true agent-runtime activities must return canonical runtime contracts directly
- provider rate limits and slot contention may require orchestration-aware retries, not just ordinary activity-level retries
- managed-session reuse and `clear_session` must publish explicit continuity artifacts and epoch boundaries; operators must not infer resets from missing local state
- durable artifact publication and bounded session/workload metadata must succeed even if live follow or stream publication fails
- live observability transport must cross the producer/API process boundary; process-local publishers are optimization only
- operational supervision stores (for example current ManagedSessionStore-style records) may aid recovery and reconciliation but are not operator/audit truth
- specialized workload containers must be labeled, timeout-bounded, and cleanly cancellable or sweepable so orphaned containers do not become hidden durable state

## 17.2 Security and isolation

- Temporal is self-hosted and private-network oriented
- worker fleets are segmented by capability and secret boundary
- sandbox execution is isolated separately from LLM, integrations, and managed runtime work
- managed runtime execution has its own stronger boundary and uses runtime-specific images rather than embedding runtimes into generic workers
- Docker access is mediated through a controlled boundary such as `docker-proxy`; managed session containers do not receive unrestricted raw Docker authority by default
- specialized workload containers use curated runner profiles with explicit image, mount, env, network, resource, and device policy
- workload containers do not automatically inherit managed-runtime auth volumes or broad worker/session environments
- artifact access is mediated through MoonMind authorization and short-lived grants
- repo and local agent-skill sources are potentially untrusted inputs
- raw credentials and SecretRefs must not appear in workflow payloads, profile rows, artifacts, diagnostics, or general logs
- trusted SaaS mutations such as Jira issue operations should execute in MoonMind-owned handlers that resolve secrets just in time and return sanitized results to the agent
- materialized skill snapshots, runtime outputs, and observability surfaces must avoid secret leakage through logs, dumps, or copied workspace files

---

## 18. Decommission criteria for legacy systems

Do not remove compatibility layers or older substrate dependencies merely because a Temporal design exists.

Retirement for any flow should require:

1. Temporal implementation is production-ready
2. observability, retries, artifacts, approvals, and session-continuity/read surfaces have parity
3. UI/API compatibility is preserved or intentionally retired
4. degraded-mode and rollback behavior are verified
5. migration of in-flight/historical behavior is intentionally handled

---

## 19. Open decisions to lock next

1. Which remaining non-Temporal-facing flows migrate next
2. When a second managed-session runtime becomes real and a neutral `ManagedSession*` contract should be extracted above Codex-specific session contracts
3. Whether task list/detail remains a unified multi-source surface through the whole transition
4. Whether specialized Docker workloads eventually justify a dedicated workload fleet separate from the current `agent_runtime` Docker-capable boundary
5. Which approval and control actions should use Updates vs Signals, especially for operator messages, clear/reset, and other session controls
6. What long-term durable store should back structured observability history in addition to current live transport/spool choices
7. When, if ever, provider-specific LLM task queues or a separate heavy-duty skill-resolution fleet become operationally necessary

---

## 20. Summary

MoonMind is not pretending migration is either unfinished everywhere or complete everywhere.

The correct architecture stance is:

- Temporal is the durable orchestration substrate
- `MoonMind.Run` is the general root execution workflow
- `MoonMind.AgentRun` is the durable lifecycle wrapper for true agent runs
- `MoonMind.AgentSession` is the task-scoped managed-session wrapper, with Codex as the current concrete implementation and one active runtime thread per session epoch
- `MoonMind.ManagedSessionReconcile` is the support workflow for managed-session reconciliation
- `MoonMind.ProviderProfileManager` is the durable coordinator for managed-runtime provider-profile slots and cooldowns
- managed runtimes run in task-scoped session containers launched from runtime-specific images; compatibility is defined by adapters and canonical control/observability/artifact contracts
- specialized Docker workloads are a sibling workload plane launched through MoonMind control-plane tools, not disguised managed sessions
- the dedicated `agent_runtime` fleet is canonical and is the current Docker-capable boundary for managed sessions and specialized workloads in local Compose
- artifacts and bounded workflow metadata are the durable operator/audit truth; containers, live streams, and supervision stores are caches or recovery aids
- managed-run observability is session-aware, artifact-first, and MoonMind-owned; terminal embedding is reserved for OAuth and other interactive auth flows
- secret-bearing integrations such as Jira belong at trusted MoonMind-side tool boundaries that resolve SecretRefs just in time rather than inside managed runtime shells
- task-oriented product surfaces may remain during migration
- workflow code should not depend on provider-shaped runtime payloads; canonical runtime contracts belong at the adapter/activity boundary

MoonMind’s job is to add domain contracts above Temporal without rebuilding a second orchestration substrate beside it.
