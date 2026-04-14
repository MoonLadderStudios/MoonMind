# MoonMind Temporal Architecture

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-TemporalArchitecture.md`](../tmp/remaining-work/Temporal-TemporalArchitecture.md)

**Status:** Draft (migration-oriented, but core runtime shape is now live)  
**Owner:** MoonMind Platform  
**Last updated:** 2026-04-04  
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
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/MoonMindArchitecture.md`
- `docs/Tasks/AgentSkillSystem.md`

---

## 3. Current state and target state

## 3.1 Current state

MoonMind currently operates with:

- a FastAPI control plane
- Postgres-backed app state
- runtime-specific workers for Codex, Gemini, and Claude
- a task-oriented product surface
- Temporal foundation services in Docker Compose
- Temporal-backed workflow orchestration for core execution paths
- some migration-oriented compatibility layers and older substrate history

This document should not flatten that reality into “fully migrated” or “not migrated.” The honest state is mixed, with the core Temporal architecture already live for major orchestration paths.

## 3.2 Target state

**Architecture precept:**  
*MoonMind execution is Temporal-native. MoonMind adds domain contracts above Temporal—Tool, Plan, Artifact, Agent Skill / Skill Set, Agent Adapter, Provider Profile—but does not introduce a parallel orchestration substrate.*

In the target state:

- Temporal **Workflow Executions** are the durable orchestration primitive
- Temporal **Activities** perform all side effects
- Temporal **Visibility** is the list/query/count source for Temporal-managed work
- Temporal **Schedules** are the authoritative recurring-start mechanism
- MoonMind keeps only domain concepts Temporal does not provide directly:
  - **Tool**
  - **Plan**
  - **Artifact**
  - **Agent Skill / Skill Set**
  - **Agent Adapter**
  - **Provider Profile**

## 3.3 Non-goals

- claiming every legacy compatibility path is already retired
- rebuilding product vocabulary entirely around raw Temporal terms
- exposing Temporal task queues as user-visible queue ordering semantics
- reintroducing a second MoonMind-specific orchestration layer on top of Temporal

---

## 4. Locked platform decisions

The following are already locked and should be treated as canonical here:

- **Deployment mode:** self-hosted
- **Deployment runtime:** Docker Compose by default
- **Temporal persistence + visibility:** PostgreSQL
- **Default artifact backend:** MinIO / S3-compatible storage
- **Task queue posture:** routing only, not product semantics
- **Default queue set:** start small; add subqueues only when isolation or scaling demands it
- **Managed runtime separation:** dedicated `agent_runtime` fleet is canonical
- **Root agent execution wrapper:** `MoonMind.AgentRun` is canonical for true agent runs
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

---

## 6. Architecture overview

## 6.1 Current deployment shape

MoonMind currently contains:

- API service
- Postgres
- Qdrant
- runtime workers
- Temporal services in Compose
- Temporal worker fleets
- compatibility/product surfaces that still present task-oriented views

## 6.2 Target Temporal shape

For Temporal-managed flows, the architecture becomes:

1. **MoonMind API**
   - authenticates callers
   - starts workflows
   - sends updates, signals, and cancel requests
   - issues artifact upload/download grants
   - accepts task/step runtime and skill selectors
   - exposes task-oriented compatibility surfaces where needed

2. **Temporal service**
   - stores workflow state and history
   - provides visibility for list/query/count
   - owns timers, retries, schedules, child workflow orchestration

3. **Artifact system**
   - stores large inputs/outputs outside workflow history
   - links artifacts to workflow executions
   - enforces retention, previews, and access control

4. **Temporal workers**
   - workflow workers run deterministic orchestration code
   - activity workers execute side effects under capability and secret boundaries

5. **Compatibility adapters**
   - bridge task-oriented UI/API contracts onto Temporal-backed workflows where needed

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
- `postgres`
- `minio`

The important architectural rule is not the exact container table but the separation of concerns:

- Temporal server owns durable orchestration substrate
- worker fleets map to capability/security boundaries
- artifact storage is separate from workflow history
- managed runtime execution has its own dedicated fleet

### 7.1 Worker fleet summary

| Fleet | Queue | Primary role |
| --- | --- | --- |
| `workflow` | `mm.workflow` | deterministic orchestration code |
| `artifacts` | `mm.activity.artifacts` | artifact lifecycle and support activities |
| `llm` | `mm.activity.llm` | planning, validation, review, other LLM activity work |
| `sandbox` | `mm.activity.sandbox` | repo operations, shell/test execution |
| `agent_runtime` | `mm.activity.agent_runtime` | managed runtime launch/status/fetch/cancel/publish |
| `integrations` | `mm.activity.integrations` | external providers, repo publish/merge, provider-specific helpers |

### 7.2 Managed runtime separation

The `agent_runtime` fleet is not aspirational anymore. It is part of the canonical architecture.

That fleet exists because managed runtimes need different privileges and operational behavior than generic sandbox or integration work, including:

- auth volume mounts
- managed process/runtime supervision
- provider-profile slot/cooldown coordination support
- distinct result/status/cancel lifecycle behavior

---

## 8. Substrate evolution

Execution is moving toward **Temporal as the single durable orchestration substrate** for new flows, while preserving compatibility for task-oriented product surfaces during migration.

Important direction:

- new durable orchestration belongs in Temporal
- compatibility layers may continue temporarily
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
| repo/deployment/local agent skills | `ResolvedSkillSet` artifact-backed execution context | Resolved before runtime use |
| provider profile | workflow-coordinated runtime credential/concurrency contract | Important for managed runtimes |
| approval policy | signal/update + workflow policy evaluation | Policy remains a MoonMind concern |
| runtime selection | activity routing + adapter/provider selection | Not a root workflow taxonomy |
| degraded-mode state snapshot | artifact-backed and reconciliation-friendly | Not execution truth |

## 9.1 Adapter vs workflow boundary

A strict boundary exists between adapters and workflows:

- **Adapters translate provider/runtime semantics**
- **Workflows own lifecycle semantics**

### Adapters

Adapters:

- normalize provider/runtime state into canonical runtime contracts
- handle launch/start/status/fetch/cancel translation
- expose capability descriptors
- materialize resolved skill snapshots for target runtimes
- preserve runtime-independent resolution semantics

### Workflows

Workflows:

- own phase progression
- own waiting and polling strategy
- own orchestration-level retries and cooldown loops
- own HITL transitions
- own durable lifecycle state
- carry refs to resolved skill snapshots and artifacts

The architecture should not let workflow code become a provider payload repair layer.

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
- long-lived waiting and callback/poll handling

`MoonMind.AgentRun` is the durable lifecycle wrapper for one true agent execution.

`MoonMind.AgentSession` is the durable task-scoped managed-session wrapper, currently backed by Codex session contracts. It is separate from `MoonMind.AgentRun` so a session can span multiple step-scoped agent runs without making container-local state the source of truth.

`MoonMind.ManagedSessionReconcile` is a bounded support workflow that invokes managed-session reconciliation activities; it is not a product-facing task workflow.

`MoonMind.ProviderProfileManager` is the durable coordinator for provider-profile slots and cooldowns.

## 10.2 What not to do

Do not create separate root workflow families just for:

- Codex vs Gemini vs Claude
- queue task vs Temporal workflow execution
- provider brand
- task queue brand
- managed vs external root taxonomy

Those are routing and execution concerns, not root orchestration categories.

## 10.3 Agent execution model

MoonMind sanctions two broad agent execution modes:

1. **Workflow-native agentic loop**  
   The reasoning loop lives in workflow code and each model/tool interaction is an activity.

2. **Delegated true agent runtime**  
   The agent runs outside the workflow and `MoonMind.AgentRun` owns the durable lifecycle.

For delegated true agent runs, canonical rules are:

- `MoonMind.Run` dispatches one agent step to one `MoonMind.AgentRun`
- managed and external agents share the same high-level lifecycle
- agent-facing runtime activities return canonical contracts directly
- large execution data stays in artifacts

---

## 11. Activity model and worker topology

## 11.1 Core rule

All side effects belong in **Activities**:

- LLM calls
- filesystem and repo operations
- shell/sandbox execution
- artifact I/O
- GitHub and other integrations
- callback verification and external polling
- managed runtime supervision/status/result work
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

## 11.3 Routing model

Routing is by capability and security boundary, not by legacy nouns.

Typical routing examples:

- planning/validation/review → `llm`
- shell and repo commands → `sandbox`
- external provider APIs → `integrations`
- artifact lifecycle and support work → `artifacts`
- managed runtime launch/status/fetch/cancel/publish → `agent_runtime`

### Workflow helper exception

The workflow fleet may host a narrow helper activity exception, such as adapter metadata resolution, when needed to preserve deterministic orchestration without growing unnecessary routing indirection.

That exception should remain small and intentional.

---

## 12. Payloads and artifacts

## 12.1 Payload discipline

Temporal payloads and workflow history should remain small.

Use:

- `ArtifactRef` values for large inputs/outputs
- compact JSON for workflow parameters and summaries
- canonical runtime contracts for agent execution boundaries

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

## 12.2 Artifact system baseline

The default artifact path for Temporal-managed work is:

- MinIO / S3-compatible storage for bytes
- Postgres metadata/index for artifact records and execution linkage

This is also the correct path for:

- resolved skill snapshots
- managed runtime diagnostics
- provider result snapshots
- terminal execution summaries when they are too large for workflow payloads

## 12.3 Manifest processing best practice

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

MoonMind’s product surface is still task-oriented. Near-term experiences may unify:

- task-style product rows
- Temporal-backed workflow executions
- compatibility detail surfaces

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

Search attributes should remain bounded. Detail surfaces should use memo, queries, and artifacts rather than overloading visibility.

Operator-facing step progress follows the same split:

- the plan artifact owns planned structure
- workflow query/state owns the live step ledger
- artifacts and `/api/task-runs/*` own rich evidence
- logs and heartbeats must not be the primary transport for step truth

## 13.3 Query vs visibility split

Maintain a strict separation:

- **Visibility + projections** → lists, counts, filtering, history, dashboards
- **Queries** → live execution detail, current progress, step ledger, awaiting reason, active step, intervention point

## 13.4 Read models are never execution truth

Execution truth remains:

- workflow state and history
- child workflow state
- artifacts

Read models and projections exist for UI optimization only. Workflows must not depend on them to decide what to do next.

---

## 14. Public API posture

## 14.1 During migration

Public APIs may remain task-oriented where product compatibility still requires it.

That includes:

- `/tasks/*` style list/detail flows
- submission APIs that start Temporal-backed workflows under the hood

## 14.2 Temporal-backed operations

When a task is backed by Temporal, public actions map to Temporal-native controls:

- create/start → start workflow execution
- edit → update
- approval or external event → signal or update depending on semantics
- cancel → workflow cancellation
- rerun → explicit new execution or continue-as-new depending on the intended behavior

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

## 15.2 Signals

Use Signals for asynchronous events such as:

- approval arrival
- GitHub/Jules/provider callbacks
- pause/resume requests
- external completion notifications
- provider-profile slot assignment/release coordination

## 15.3 Approval policy

Approval remains a MoonMind policy concern, not a Temporal-native concept. Temporal supplies transport and durability; MoonMind workflows enforce policy semantics.

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

## 17.2 Security and isolation

- Temporal is self-hosted and private-network oriented
- worker fleets are segmented by capability and secret boundary
- sandbox execution is isolated separately from LLM and integrations work
- managed runtime execution has its own stronger boundary
- artifact access is mediated through MoonMind authorization and short-lived grants
- repo and local agent-skill sources are potentially untrusted inputs
- materialized skill snapshots and runtime outputs must avoid secret leakage through logs or dumps

---

## 18. Decommission criteria for legacy systems

Do not remove compatibility layers or older substrate dependencies merely because a Temporal design exists.

Retirement for any flow should require:

1. Temporal implementation is production-ready
2. observability, retries, artifacts, and approvals have parity
3. UI/API compatibility is preserved or intentionally retired
4. degraded-mode and rollback behavior are verified
5. migration of in-flight/historical behavior is intentionally handled

---

## 19. Open decisions to lock next

1. Which remaining non-Temporal-facing flows migrate next
2. Whether task list/detail remains a unified multi-source surface through the whole transition
3. Which approval paths should use Updates vs Signals
4. When child workflows are preferred over inline orchestration for larger fan-out cases
5. When, if ever, provider-specific LLM task queues become operationally necessary
6. When, if ever, a separate heavy-duty skill-resolution fleet is justified beyond existing capability fleets

---

## 20. Summary

MoonMind is not pretending migration is either unfinished everywhere or complete everywhere. The correct architecture stance is:

- Temporal is the durable orchestration substrate
- `MoonMind.Run` is the general root execution workflow
- `MoonMind.AgentRun` is the durable lifecycle wrapper for true agent runs
- `MoonMind.AgentSession` is the task-scoped managed-session wrapper, with Codex as the current concrete session implementation
- `MoonMind.ManagedSessionReconcile` is the support workflow for managed-session reconciliation
- `MoonMind.ProviderProfileManager` is the durable coordinator for managed-runtime provider-profile slots and cooldowns
- activity fleets are split by capability and secret boundary
- the dedicated `agent_runtime` fleet is canonical
- artifacts keep large data out of workflow history
- task-oriented product surfaces may remain during migration
- workflow code should not depend on provider-shaped runtime payloads; canonical runtime contracts belong at the adapter/activity boundary

MoonMind’s job is to add domain contracts above Temporal without rebuilding a second orchestration substrate beside it.
