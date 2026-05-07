# Temporal Architecture

**Implementation tracking:** Rollout, backlog, one-off implementation notes, migration checklists, and work sequencing live in MoonSpec artifacts (`specs/<feature>/`), issues, pull requests, gitignored handoffs, or local-only files. Canonical `docs/` files describe durable architecture and product contracts.

**Status:** Normative architecture hub (Temporal-native; compatibility projections and hardening work remain repo-visible)
**Owner:** MoonMind Platform
**Last updated:** 2026-05-06
**Audience:** backend, infra, managed-runtime, integrations, dashboard, workflow authors, operators

---

## 1. Purpose

MoonMind uses Temporal as its durable orchestration substrate for Temporal-managed execution.

This document defines the top-level architecture for:

- workflow identity and vocabulary
- workflow catalog and lifecycle boundaries
- worker and activity topology
- scheduling posture
- Visibility and UI query posture
- source-of-truth and projection rules
- artifact and payload discipline
- managed-runtime and external-agent integration
- security, idempotency, versioning, observability, and operational guardrails

MoonMind may still expose product vocabulary such as `task`, `taskId`, task detail routes, and Mission Control task-oriented views. Those are product/API terms. Inside the Temporal architecture, the canonical durable entity is a **Workflow Execution**.

Compatibility tables, app DB projections, historical rows, dashboard adapters, and task-oriented APIs may exist, but they are not parallel orchestrators. For Temporal-managed work, workflow-owned state/history and workflow-managed metadata are authoritative. App DB projections are repairable read models.

---

## 2. Repo-aligned baseline

This architecture is grounded in the current MoonMind repository, not a generic Temporal deployment template.

The current repo baseline is:

- Temporal core paths are live, while compatibility and migration surfaces still exist.
- The Temporal deployment posture is explicitly self-hosted Docker Compose with PostgreSQL persistence and PostgreSQL Visibility.
- The default artifact backend is MinIO / S3-compatible storage.
- The worker topology is a small capability-based fleet set: `workflow`, `artifacts`, `llm`, `sandbox`, `integrations`, and `agent_runtime`.
- The live registered workflow catalog includes `MoonMind.MergeAutomation` in addition to the previously documented core workflow types.
- The workflow helper-activity exception is narrow; the concrete helper currently called out by the activity topology is `integration.resolve_adapter_metadata`.
- The shared Temporal data converter currently resolves to the Pydantic data converter. A payload-encryption codec is not currently visible as the shared converter contract and must not be assumed to exist.
- Projection repair, run-history/rerun semantics, visibility semantics, type-safety rules, and error taxonomy are covered by adjacent docs and should be treated as part of this architecture.

The architecture therefore avoids two inaccurate claims:

1. It does **not** treat Temporal Cloud or Kubernetes as the normative production target, because MoonMind’s platform foundation currently locks self-hosted Docker Compose.
2. It does **not** pretend the migration is complete everywhere. Temporal is the durable runtime direction and active core substrate, but compatibility adapters and app-local projections remain live.

---

## 3. Related docs

This document is the architecture hub. Detailed contracts live in:

- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/ArtifactPresentationContract.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/TaskExecutionCompatibilityModel.md`
- `docs/Temporal/TemporalTypeSafety.md`
- `docs/Temporal/RunHistoryAndRerunSemantics.md`
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/Temporal/WorkflowSchedulingGuide.md`
- `docs/Security/ProviderProfiles.md`
- `docs/Security/SecretsSystem.md`
- `docs/ManagedAgents/ManagedAgentArchitecture.md`
- `docs/ManagedAgents/CodexCliManagedSessions.md`
- `docs/ManagedAgents/LiveLogs.md`
- `docs/ManagedAgents/DockerOutOfDocker.md`
- `docs/Tasks/AgentSkillSystem.md`
- `docs/MoonMindArchitecture.md`

Avoid duplicating exact lifecycle state machines, complete activity catalogs, API schemas, or query syntax here. This hub defines architectural rules and points to the normative contract docs.

---

## 4. Architecture statement

MoonMind execution is Temporal-native.

Temporal provides:

- **Workflow Executions** for durable orchestration
- **Workflow Types** for stable orchestration contracts
- **Activities** for side effects
- **Task Queues** for worker routing
- **Child Workflows** for durable sub-lifecycles
- **Updates** for synchronous request/response mutations
- **Signals** for asynchronous events
- **Queries** for side-effect-free live state reads
- **Timers** for deterministic waiting
- **Schedules** for recurring starts
- **Visibility** for bounded list/filter/count indexes
- **Continue-As-New** for bounded histories

MoonMind adds domain contracts above Temporal:

- Task / Run product vocabulary
- Plan and Step Ledger
- Tool / Skill / Preset execution contracts
- Artifact and report presentation contracts
- Agent Adapter contracts
- Provider Profile and SecretRef policies
- Managed Session and session-continuity contracts
- Runner Profile and workload-container policies
- Mission Control read models

MoonMind must not introduce a second durable orchestration substrate for work that is Temporal-managed.

---

## 5. Operating model

MoonMind currently operates as a Temporal-native application with these planes.

### 5.1 MoonMind API / Control Plane

The API:

- authenticates and authorizes callers
- starts workflows
- sends Updates, Signals, Cancels, Terminations, and Schedule operations
- exposes task-oriented and execution-oriented APIs
- maintains projections and compatibility fields where useful
- enforces ownership, idempotency, and public contract validation before invoking Temporal

The API is a policy boundary. It must not become a competing scheduler or orchestration engine.

### 5.2 Temporal Server

Temporal Server:

- is self-hosted through Docker Compose in MoonMind’s current platform contract
- uses PostgreSQL for persistence and PostgreSQL Visibility
- owns workflow history, timers, retries, workflow state, and schedule triggers
- is operated inside MoonMind’s private service network rather than as a public untrusted endpoint

Because Docker Compose is the required operating posture, production hardening must be Compose-aware rather than deferred to an assumed managed service. Required hardening includes:

- persistent volume backup and restore procedures for PostgreSQL and artifact storage
- namespace bootstrap and retention automation
- SQL Visibility schema-upgrade rehearsal before Temporal server upgrades
- shard-count decision recorded before production load
- private network exposure and controlled Temporal UI/admin access
- health checks, metrics, alerts, and game-day recovery procedures
- capacity envelopes for workflow starts, worker concurrency, visibility list load, artifact throughput, and managed-runtime launches

### 5.3 Workflow Worker Fleet

The workflow fleet:

- polls `mm.workflow`
- runs deterministic workflow code
- owns workflow orchestration, child workflow starts, domain state transitions, and bounded Search Attribute updates

Workflow workers should not perform general side effects except for documented narrow helper Activities.

### 5.4 Activity Worker Fleets

Activity fleets:

- execute side effects
- are separated by capability, secrets, isolation, egress, and resource profile
- must register only the activity families assigned to their fleet
- must support graceful shutdown and bounded concurrency

### 5.5 Artifact Store

The artifact store:

- stores large inputs, outputs, logs, diagnostics, reports, manifests, prompt bundles, runtime artifacts, and continuity data
- keeps workflow history small by passing compact refs through workflow payloads
- owns byte storage, metadata linkage, retention, previews, redaction, and artifact ACLs

### 5.6 Visibility / Query Plane

Temporal Visibility is the Temporal-backed bounded list/filter/count index for Temporal-managed work.

Workflow history and workflow-owned state are canonical. Visibility is the canonical query index for selected bounded metadata. App DB projections are authorized/materialized views and may lag, but they must converge to Temporal-owned state.

### 5.7 Managed Runtime Plane

The managed runtime plane:

- supervises MoonMind-managed CLI/container runtimes
- owns runtime launch, status, result collection, cleanup, and log capture through Activities and support workflows
- uses `MoonMind.AgentRun` and, for task-scoped managed sessions, `MoonMind.AgentSession`
- keeps managed-run observability artifact-first and session-aware

### 5.8 External Integration Plane

The external integration plane:

- talks to delegated providers such as Jules, Codex Cloud, OpenClaw, GitHub, Jira, and future providers
- verifies callbacks before translating them into workflow events
- normalizes provider-specific payloads into canonical runtime contracts before workflow code sees them

---

## 6. Architectural invariants

### 6.1 Workflows orchestrate; Activities do side effects

Workflow code must remain deterministic.

Workflows may:

- branch on workflow-owned state
- start child workflows
- execute Activities
- wait on durable timers
- wait on Signals and Updates
- upsert bounded Search Attributes
- expose Queries
- Continue-As-New

Workflows must not directly perform:

- network calls
- filesystem reads or writes
- subprocess execution
- database reads or writes
- provider SDK calls
- raw clock or random reads outside Temporal APIs
- secret lookup
- unbounded log, artifact, prompt, transcript, manifest, or provider-payload handling

Those belong in Activities or external systems referenced by compact refs.

### 6.2 Child workflows represent durable sub-lifecycles

Use child workflows when the child has its own durable lifecycle, cancellation semantics, external wait, retry posture, operator-facing state, or history-management needs.

Current first-class examples:

- `MoonMind.AgentRun` for one true agent execution
- `MoonMind.AgentSession` for one task-scoped managed session
- `MoonMind.MergeAutomation` for post-run PR readiness and resolver launch
- `MoonMind.ProviderProfileManager` for long-lived provider-profile slot coordination
- `MoonMind.OAuthSession` for OAuth / terminal-auth lifecycle support
- `MoonMind.ManagedSessionReconcile` for bounded operational reconciliation

Do not collapse these into giant long-running Activities.

Child workflow rules:

- Every child workflow start must intentionally choose and document a Parent Close Policy.
- The parent must await child-start acceptance before treating the child as durably owned.
- `MoonMind.Run` cancellation should request cancellation of active `MoonMind.AgentRun` children and allow best-effort provider/runtime cleanup.
- Fire-and-forget operational children may use abandon semantics only when their workflow IDs are durably recorded and a reconciliation owner exists.
- Terminate-style parent close behavior should be reserved for tightly coupled subgraphs where independent cleanup would be wrong.
- A parent must not Continue-As-New with active children unless it has explicitly recorded child Workflow IDs and selected a handoff strategy.
- Large fanout must be bounded. A single parent should not spawn unbounded child workflows; use batching, manifest phases, child aggregation workflows, or Continue-As-New as needed.

### 6.3 Activities are bounded or heartbeat-aware

Activities should be bounded side-effect calls.

Long-running work should normally be represented as workflow orchestration plus short Activities, durable timers, callbacks, status polling, or child workflows.

Use heartbeat-aware Activities only where the side effect itself is a long single operation, such as sandbox execution, streaming-gateway work, runtime launch/publish work, or other explicitly cataloged cases.

### 6.4 Task Queues are routing plumbing

Temporal Task Queues are internal routing labels for worker fleets. They are not product queues, and MoonMind does not promise FIFO ordering to users.

Routing is by capability and security boundary, not by provider brand or product noun.

### 6.5 Visibility is for bounded query fields

Search Attributes are for small, bounded, queryable fields.

They must not contain:

- full prompts
- manifests
- logs
- transcripts
- step ledgers
- provider payloads
- secrets
- arbitrary user prose
- raw error bodies
- generated code or diffs

Visibility is eventually consistent and subject to indexing delay. It must not be used for transactional gating, strongly consistent detail reads, or workflow-control decisions. Use Workflow Queries, workflow responses, Describe/History, artifacts, or Activity-backed lookups where stronger or richer reads are required.

### 6.6 Artifacts carry large data

Large content belongs in the artifact system. Workflow payloads, history, Search Attributes, and Memo carry compact refs, summaries, hashes, and bounded metadata.

A payload that is too large or too sensitive to inspect in Temporal Web/UI/logs should be an artifact ref or a SecretRef, not a Temporal payload.

### 6.7 Updates, Signals, Queries, Cancels, and Terminates have distinct roles

| Primitive | Use when | Must not be used for |
| --- | --- | --- |
| Update | Caller needs synchronous acceptance/rejection and usually a result | fire-and-forget notifications |
| Signal | Asynchronous event or notification where the caller does not wait for workflow-side acceptance | request/response mutation semantics |
| Query | Side-effect-free live state read | mutation, validation side effects, external I/O |
| Cancel | Graceful user/API cancellation request | emergency forced stop |
| Terminate | Ops-only forced stop | ordinary user cancel or cleanup claim |

Additional rules:

- Public mutating Updates require validators.
- Validators must be deterministic, side-effect-free, and must reject impossible state transitions before acceptance.
- Externally supplied messages should include a dedupe key, request ID, provider event ID, Temporal Update ID, or equivalent bounded idempotency marker.
- Async Update/Signal handlers must not race each other or the main workflow loop. Conflicting mutations must be serialized with workflow-safe coordination or queued through the main loop.
- Callback ingestion should usually verify at the API/activity boundary and translate into Signals unless synchronous workflow acceptance is truly required.
- API callers must receive clear behavior when an Update cannot be accepted because workflow workers are unavailable.

### 6.8 Schedules own recurring starts

Recurring and periodic workflow starts must use Temporal Schedules. Do not introduce cron/beat-style orchestration for Temporal-managed workflows.

Workflow timers remain correct for in-workflow waits. Temporal Schedules own recurrence outside any one workflow execution.

### 6.9 Canonical runtime contracts cross workflow boundaries

True agent-runtime Activities must return canonical runtime contracts directly:

- `AgentRunHandle`
- `AgentRunStatus`
- `AgentRunResult`

Provider-specific data belongs in canonical `metadata`, not in alternate top-level provider-shaped workflow payloads. Workflow code should not reconstruct canonical contracts from raw provider responses.

### 6.10 Type safety is part of the architecture

Temporal boundary payloads are durable contracts.

Rules:

- Every public workflow input, Activity request/response, Signal, Update, Query, and Continue-As-New payload should use named typed models.
- New Temporal boundaries should use one structured request model and one structured response model where applicable.
- Compatibility dict-shaped shims may exist at the edge, but they must validate/coerce immediately into canonical models.
- Continue-As-New input is a first-class continuation contract, not a scratch dict.
- Additive evolution is preferred. Non-additive changes require replay safety, compatibility shims, or a controlled cutover.

---

## 7. Workflow catalog

The live repo-aligned workflow catalog is:

| Workflow Type | Role | Product visibility |
| --- | --- | --- |
| `MoonMind.Run` | Root user/service execution workflow; plans work, owns step ledger, starts child agent runs, integrates outputs | Primary task/execution surface |
| `MoonMind.ManifestIngest` | Ingests, validates, compiles, and orchestrates manifest-backed work | User/system execution surface |
| `MoonMind.AgentRun` | Durable lifecycle wrapper for one true managed or external agent execution | Child/internal, surfaced through parent details |
| `MoonMind.AgentSession` | Task-scoped managed-session workflow; currently Codex-backed in the live session plane | Internal/operator/detail support |
| `MoonMind.ManagedSessionReconcile` | Bounded operational reconciliation for managed sessions | Operational |
| `MoonMind.ProviderProfileManager` | Long-lived provider-profile slot, lease, cooldown, and reconciliation manager | Internal/operator |
| `MoonMind.OAuthSession` | OAuth / terminal-auth lifecycle support for managed runtimes | Support workflow |
| `MoonMind.MergeAutomation` | PR readiness watcher and resolver follow-up launcher after publish-capable runs | Child/support workflow |

Rules:

- Workflow Type names are stable contracts.
- Add new Workflow Types only when lifecycle behavior is materially distinct.
- Do not model provider brands as root workflow types.
- Do not model worker fleets or task queues as product taxonomies.
- Product `task` vocabulary maps primarily to `MoonMind.Run`.
- If docs and code disagree about the live catalog, code registration and `WorkflowTypeCatalogAndLifecycle.md` must be reconciled immediately.

---

## 8. Identifier model

### 8.1 Workflow ID

`workflowId` is the canonical durable identifier for Temporal-managed work.

Representative forms:

- `mm:<uuid>` for ordinary user/service executions
- stable prefixed IDs for singleton or support workflows, such as `provider-profile-manager:<runtime>` or `oauth-session:<session_id>`

Rules:

- use Workflow ID for routes, links, cache keys, and API lookup
- do not encode secrets or sensitive data
- Continue-As-New preserves Workflow ID
- prefer Workflow ID over Run ID for product identity
- child workflow IDs should be deterministic when duplicate child starts must be prevented

### 8.2 Run ID

`runId` identifies one concrete Temporal run of a Workflow Execution.

Rules:

- use for debugging, diagnostics, and history correlation
- do not use as the primary product handle
- detail views may expose it as debug metadata
- do not assume it is stable across Continue-As-New, retry chains, reset chains, or schedule-triggered executions

### 8.3 Task ID

`taskId` is product/API compatibility vocabulary.

For Temporal-backed task rows:

- `taskId` must equal `workflowId`
- APIs may return both
- compatibility aliases should converge toward Workflow ID
- `taskId` must not be minted as a second durable identity for the same Temporal execution

### 8.4 Workflow ID reuse and conflict policy

Every workflow start path must intentionally choose a conflict/reuse posture.

Default rules:

- Ordinary user/service starts use a new `mm:<uuid>` Workflow ID unless an API idempotency key resolves to an existing accepted start.
- Singleton managers use stable IDs and must be started idempotently through an ensure/start-or-signal pattern.
- Schedule-triggered recurring runs use fresh run Workflow IDs per tick, associated back to the schedule definition.
- Manual schedule triggers use the schedule model and produce a fresh run Workflow ID unless the schedule contract explicitly says otherwise.
- Rerun of the same logical execution uses Continue-As-New only where the run-history/rerun contract permits it.
- Failed-step Resume is a linked follow-up execution with its own Workflow ID unless a future in-place continuation model is explicitly designed.

The exact per-type matrix belongs in `WorkflowTypeCatalogAndLifecycle.md` and `RunHistoryAndRerunSemantics.md`.

---

## 9. Worker and Task Queue topology

MoonMind uses a small capability-based worker set.

| Fleet | Queue | Primary responsibility |
| --- | --- | --- |
| `workflow` | `mm.workflow` | deterministic workflow code and narrow helper Activities |
| `artifacts` | `mm.activity.artifacts` | artifact lifecycle, execution projections, support persistence, selected OAuth/provider-profile support work |
| `llm` | `mm.activity.llm` | planning, validation, review, LLM-bound work |
| `sandbox` | `mm.activity.sandbox` | isolated repo/process execution |
| `integrations` | `mm.activity.integrations` | external provider APIs, repo, Jira/GitHub/provider calls, merge automation integration work |
| `agent_runtime` | `mm.activity.agent_runtime` | managed runtime launch, supervision, auth runner, status, result, cleanup, Docker-backed managed runtime/workload boundary |

Rules:

- route by capability, not product noun
- split queues only for different secrets, isolation, scaling, egress, or resource policy
- no queue-per-provider unless operationally justified
- no user-facing FIFO claims
- worker startup must fail closed if the fleet or activity catalog is invalid
- workers polling the same task queue must register the workflows/activities expected for that queue, except during an explicit compatibility rollout
- worker concurrency limits are part of the fleet contract and must be observable
- deployments must allow graceful shutdown/drain so in-flight activities can heartbeat, cancel, or time out predictably

### 9.1 Workflow helper-activity exception

The workflow fleet is primarily for workflow code.

A narrow helper-activity exception is allowed only when all of the following are true:

- the helper exists to preserve deterministic workflow behavior
- it is fast and bounded
- it does not require broad secrets, Docker access, sandbox privileges, or provider-side mutation
- it does not block workflow-task throughput under normal operation
- it is explicitly listed in the activity topology

Current repo-aligned example:

- `integration.resolve_adapter_metadata`

If a helper grows into I/O-heavy work, provider mutation, artifact work, or runtime supervision, it must move to a capability-appropriate activity queue.

### 9.2 Local Activities

Local Activities are allowed only for fast, deterministic-adjacent helper work where durable server-side queuing is not needed and loss/retry semantics are acceptable.

Rules:

- do not use Local Activities for provider calls, DB writes, artifact writes, sandbox execution, runtime launch, or secret resolution
- do not use Local Activities to bypass task queue isolation
- if a local helper becomes important for recovery or operations, promote it to a normal Activity

---

## 10. Activity model

Activity Type names are stable contracts. Prefer adding a new Activity Type over changing semantics incompatibly.

Current activity families include:

- `artifact.*`
- `execution.*`
- `manifest.*`
- `plan.*`
- `mm.skill.execute`
- `sandbox.*`
- `provider_profile.*`
- `oauth_session.*`
- `integration.<provider>.*`
- `repo.*`
- `merge_automation.*`
- `agent_runtime.*`
- `proposal.*`
- `step.review`
- future/target `agent_skill.*`

The exact catalog, timeout defaults, retry policies, fleet routing, heartbeat requirements, and pending families are defined in `docs/Temporal/ActivityCatalogAndWorkerTopology.md`.

Rules:

- all side-effecting Activities must be idempotent or safely deduplicated
- all Activities must have explicit timeouts
- long-running Activities must heartbeat when appropriate
- non-retryable contract failures must be classified explicitly
- model-provider rate limits should use bounded retry/backoff with visible exhaustion summaries
- Activities return results to workflows; workflows own Search Attribute updates
- provider/runtime normalization belongs in Activities/adapters, not workflow code

### 10.1 Idempotency-key standard

Idempotency must be explicit enough for operators to reason about retries.

Default key patterns:

| Activity family | Key shape |
| --- | --- |
| External provider start | `{namespace}:{workflowId}:{logicalStepId}:{agentRunId or childWorkflowId}` |
| Managed runtime launch | `{namespace}:{workflowId}:{logicalStepId}:{sessionId or runHandle}` |
| Artifact write | deterministic artifact ID, content hash, or create-intent key |
| Projection write | upsert by `workflowId` plus monotonic source version/event time |
| Provider callback | `{provider}:{providerEventId}:{workflowId or providerRunId}` |
| Repo / PR mutation | `{workflowId}:{stepId}:{operation}:{targetRepo}:{branch or prRef}` |
| Merge automation | `{mergeAutomationWorkflowId}:{prRef}:{gateName}` |
| Cleanup/cancel | naturally idempotent target identity plus operation name |

Rules:

- Activity attempt number may be logged but must not be the primary business idempotency key.
- Retrying a start-like Activity must not create duplicate external jobs or duplicate managed launches.
- Cleanup Activities must be safe to run multiple times.
- Idempotency keys should appear in logs and diagnostics, redacted or hashed if they contain sensitive context.

---

## 11. Visibility and UI query model

Workflow history and workflow-owned state are canonical. Temporal Visibility is the canonical Temporal-backed list/filter/count index for bounded metadata.

Required Search Attributes:

- `mm_owner_id`
- `mm_owner_type`
- `mm_state`
- `mm_updated_at`
- `mm_entry`

Common optional Search Attributes:

- `mm_repo`
- `mm_integration`
- `mm_scheduled_for`

Required Memo fields:

- `title`
- `summary`

Rules:

- list rows must render without artifact hydration
- detail views may use Queries and artifacts for richer state
- app DB projections must preserve Temporal-backed semantics
- dashboard compatibility statuses must not redefine `mm_state`
- ordinary task list views may scope to `MoonMind.Run` and `mm_entry = run`
- operator/admin views may expose broader workflow scopes
- Search Attributes and Memo are visible to operators and must not contain secrets or sensitive prose

### 11.1 Search Attribute governance

MoonMind uses PostgreSQL Visibility. Custom Search Attribute count, type, and size budgets are finite. Treat the Search Attribute set as a governed schema.

| Field | Required | Type intent | Update frequency |
| --- | --- | --- | --- |
| `mm_owner_type` | Yes | keyword | set at start; immutable |
| `mm_owner_id` | Yes | keyword | set at start; immutable |
| `mm_state` | Yes | keyword | domain-state transitions only |
| `mm_updated_at` | Yes | datetime | meaningful user-visible mutations only |
| `mm_entry` | Yes | keyword | set at start; immutable |
| `mm_repo` | Optional | keyword | set when repo filtering is required |
| `mm_integration` | Optional | keyword | set when integration filtering is required |
| `mm_scheduled_for` | Optional | datetime | deferred/scheduled execution metadata |

`mm_updated_at` is allowed, but it must not become a high-churn telemetry feed. It should move on meaningful user-visible mutations such as domain-state transitions, accepted Updates, visible Signal handling, terminal transitions, bounded progress checkpoints, and title/summary changes. It must not move on every heartbeat, log line, polling tick, low-level retry, or internal backoff detail.

### 11.2 Memo bounds

Memo is presentation metadata, not payload storage.

Rules:

- `title` and `summary` must have byte limits enforced before write.
- Large summaries, generated prose, detailed errors, and diagnostics belong in artifacts.
- Memo must not carry raw prompts, manifests, logs, step ledger rows, provider payloads, or secrets.
- Memo should contain safe refs only where those refs are small and display-useful.

### 11.3 `mm_state` mapping

`mm_state` is derived from workflow-owned domain state and must align with Temporal close status.

Rules:

- workflows own `mm_state` transitions
- projections may mirror `mm_state`, but must not redefine it
- terminal Temporal statuses must reconcile to terminal domain states:
  - completed → `completed`
  - failed / timed out / terminated → `failed`
  - canceled → `canceled`
- if Temporal status and projection state conflict, Temporal wins and projection repair must correct the row
- if Temporal close status and `mm_state` conflict, the lifecycle contract must define the correction behavior and alert when it indicates a workflow bug

---

## 12. API and projection posture

The MoonMind API is the policy and authorization boundary for user-facing Temporal operations.

The API may:

- start workflows
- create, update, pause, unpause, delete, and trigger Schedules
- send Updates
- send Signals
- cancel workflows
- query workflows
- list/count through Visibility-backed semantics
- maintain DB projections for joins, authorization, compatibility, degraded reads, and repair

The API must not become a competing scheduler or orchestration engine.

Projection rows may add:

- authorization joins
- display denormalization
- reconciliation state
- compatibility aliases
- historical support
- local exact counts when explicitly described as projection-backed
- degraded-mode fallback markers

Projection rows may not redefine:

- workflow identity
- lifecycle state
- terminal outcome
- retry/cancel semantics
- query ordering semantics
- step truth
- run-history truth

### 12.1 Projection repair

Projection repair is required architecture, not a best-effort afterthought.

Required repair triggers:

1. post-mutation refresh after start/update/signal/cancel where practical
2. periodic sweeper that compares recent Temporal executions against projection rows
3. repair-on-read when a requested row is missing, stale, or obviously inconsistent
4. startup/backfill repair after outages, deployments, or schema changes

Repair rules:

| Drift type | Repair behavior |
| --- | --- |
| Temporal execution exists, projection missing | create/upsert projection from Temporal truth |
| projection exists, Temporal execution missing | mark orphaned/quarantine; do not present as active |
| projection `run_id` stale after Continue-As-New | update row in place by Workflow ID |
| projection state/close status disagrees | trust Temporal and overwrite projection |
| memo/Search Attributes stale | refresh bounded fields from Temporal-visible state |
| artifact refs diverge | refresh from workflow/artifact linkage and deduplicate |

Degraded-mode reads must be truthful. If a route falls back to projection data because Visibility is degraded, it should expose stale/fallback mode internally and avoid claiming exact canonical freshness.

---

## 13. Managed and external agent execution

`MoonMind.Run` remains the task-level root workflow. When a plan step requires a true agent runtime, `MoonMind.Run` starts `MoonMind.AgentRun`.

`MoonMind.AgentRun` owns exactly one true agent execution lifecycle.

Both managed and external agents follow the same conceptual lifecycle:

1. prepare compact execution context and refs
2. start asynchronously
3. wait through durable timers, Signals, Updates, or callbacks
4. read canonical status
5. fetch canonical result
6. publish artifacts
7. cancel/cleanup if requested

Managed runtimes additionally use:

- provider-profile slot coordination
- managed runtime launcher/supervisor/store components
- artifact-first log capture
- bounded status/result Activities
- optional task-scoped `MoonMind.AgentSession` for interactive session continuity

External providers use provider adapters, but still return canonical runtime contracts before crossing into workflow code.

### 13.1 Async callback pattern

External provider callbacks must be:

- authenticated before workflow delivery
- correlated to a known run or workflow
- deduplicated by provider event ID or equivalent
- normalized into typed compact event payloads
- delivered to the workflow using Signal or Update according to caller semantics

Callback verification belongs at the API or Activity boundary. Workflow code must not perform cryptographic verification or provider network calls directly.

### 13.2 Saga and compensation posture

Multi-step workflows that perform side effects across external systems must not rely on implicit rollback.

Rules:

- workflows own explicit compensation decisions
- compensating Activities must be idempotent
- failure summaries must state which side effects happened and which cleanup/compensation succeeded, failed, or was skipped
- cancellation cleanup is best effort and must not be reported as successful if it was not attempted or failed

---

## 14. Managed sessions and workload containers

MoonMind distinguishes three concepts.

### 14.1 True agent run

A true agent run:

- is owned by `MoonMind.AgentRun`
- represents one durable agent execution step
- may be managed by MoonMind or delegated to an external provider

### 14.2 Managed session

A managed session:

- is owned by `MoonMind.AgentSession`
- provides task-scoped runtime continuity, session epoch tracking, turn/control handling, and session continuity artifacts
- is currently Codex-backed in the live session plane
- may later generalize to other runtimes through neutral managed-session contracts

### 14.3 Workload container

A workload container:

- is launched through a tool/workload Activity and runner profile
- returns ordinary tool results and artifacts
- is not a managed session unless the workload is itself a true agent runtime

Rules:

- `clear_session` creates a new continuity epoch and explicit reset evidence
- container-local state is not audit truth
- session continuity is artifact-backed
- workload identity and session identity must not be blurred in APIs or UI
- promotion from workload container to managed session requires an explicit lifecycle decision, not merely “it uses Docker”

---

## 15. Scheduling

Temporal Schedules are the authoritative mechanism for recurring workflow starts and operational sweepers.

Current schedule-backed concerns include:

- recurring task definitions
- recurring manifest runs
- managed-session reconciliation
- artifact or projection sweepers where appropriate

Rules:

- schedule definitions may have DB records for product management, but Temporal Schedule objects are the execution trigger
- manual recurring runs should trigger the schedule model rather than bypassing it
- workflow timers should be used for in-workflow waits
- external cron loops must not start Temporal-managed workflows directly
- every schedule must declare overlap, catchup, jitter, target Workflow Type, and Workflow ID policy

### 15.1 Recurring run identity

Each recurring schedule tick should create a fresh Workflow ID for the produced run, associated back to the schedule definition.

Rules:

- scheduled run `taskId == workflowId`
- schedule definition ID is not the run Workflow ID
- run history belongs to the schedule detail/read model
- manual run-now uses the schedule’s trigger path and produces a normal run identity
- schedule-triggered workflows must have idempotency/conflict policy for duplicate ticks or retried schedule operations

### 15.2 Overlap and catchup

Every schedule type must explicitly choose:

- overlap policy
- catchup policy
- jitter
- behavior when a previous run is still active
- behavior when the schedule was paused or Temporal was down

Defaults should be conservative:

- recurring user tasks: skip or buffer one, not unbounded overlap
- operational sweepers: skip if still active unless the sweeper is explicitly designed for overlap
- reconciliation workflows: bounded overlap only when idempotent and partitioned

### 15.3 Schedule / DB reconciliation

Temporal Schedule objects are execution authority. DB schedule rows are product-management projections.

Repair direction:

- if DB says a schedule exists but Temporal Schedule is missing, repair or mark schedule unhealthy
- if Temporal Schedule exists but DB row is missing/deleted, pause/quarantine or recreate the DB projection according to operator policy
- if pause/enabled state differs, reconcile to the documented authority for that product action and record an audit event
- scheduled run history must be derived from Temporal executions plus schedule metadata, not from an independent cron log

---

## 16. Cancellation and termination

User cancel maps to Temporal workflow cancellation.

Expected behavior:

- transition domain state to `canceled`
- propagate cancellation to in-flight child workflows where appropriate
- attempt best-effort cleanup through Activities
- release provider-profile slots when held
- publish truthful final summary/diagnostics

Forced termination is ops-only.

Expected behavior:

- use only for runaway workflows, policy violations, or operational emergencies
- do not pretend cleanup occurred if it did not
- classify resulting product outcome truthfully
- repair projections after termination
- alert if termination indicates a broken cancellation path

---

## 17. Continue-As-New and history management

Use Continue-As-New to keep replay bounded for long-lived or event-heavy workflows.

Use it for:

- long wait/poll loops
- managed session control histories
- provider-profile manager loops
- large manifests or repeated graph phases
- explicit reruns that preserve durable identity
- history growth approaching configured thresholds

Rules:

- preserve Workflow ID
- preserve business correlation IDs
- preserve required Search Attributes and Memo
- preserve artifact refs and compact state required to resume
- use a typed carry-forward / continuation model
- test carry-forward behavior whenever Search Attributes, Memo, or continuation state changes
- do not depend on stable `runId` across a logical workflow lifetime
- do not Continue-As-New with active children unless child ownership handoff is explicit

Continue-As-New does not automatically mean user-visible rerun. Manual rerun, automatic lifecycle rollover, and major reconfiguration must be distinguished in summaries and projections.

---

## 18. Replay-safe evolution and deployment

Temporal workflow history is durable. Workflow changes must be replay-safe.

Current repo-aligned rule:

- MoonMind does not currently rely on Temporal Worker Deployment routing as the runtime contract.
- Replay-sensitive workflow changes must use patch gates, replay tests, or an explicit cutover plan.
- Activity signature changes require a new Activity Type or a controlled compatibility cutover.
- DTO/schema changes must remain backward compatible for in-flight payloads.

Forward-compatible rule:

- If MoonMind adopts Temporal Worker Versioning / Worker Deployment routing in the future, this document should be updated to make Build ID rollout, drain, rollback, and compatibility windows first-class.
- Until then, do not write architecture text that assumes server-side Worker Versioning is available in MoonMind deployments.

Required deployment safety:

- Never remove or rename Update/Signal handlers while histories may reference them without a compatibility plan.
- Prefer new Activity Types for incompatible Activity contract changes.
- Use replay tests for workflow changes with meaningful branching behavior.
- Avoid reading mutable global config directly in workflow logic unless routed through deterministic inputs or Activities.
- Feature flags that affect workflow branching must be resolved at workflow start, stored in workflow state, or resolved via Activities whose results are recorded before branch decisions.
- Workers must keep old-compatible code available long enough for open executions or must migrate those executions intentionally.

---

## 19. Payload, data converter, and security boundary

MoonMind’s approved Temporal payload conversion policy is part of the runtime contract.

Current repo-aligned baseline:

- the shared converter is the Pydantic Temporal data converter
- compact payload validation is part of ongoing contract hardening
- no repository evidence should be interpreted as a deployed Temporal payload-encryption codec

Rules:

- all Temporal clients and workers must use the approved shared data converter
- boundary models must validate compactness and supported wire shapes
- raw nested bytes, large mappings, unbounded strings, and arbitrary provider payloads are not approved Temporal payload shapes
- payloads exceeding configured size thresholds must be rejected and moved to artifact refs
- sensitive business payloads that must enter Temporal history require an approved payload codec/encryption posture before production use
- Search Attributes, Memo, Workflow IDs, Activity IDs, Task Queue names, logs, and unencrypted artifacts must be treated as visible to operators

### 19.1 Secret handling

Rules:

- never put raw secrets in workflow inputs, history, Search Attributes, Memo, logs, or ordinary artifacts
- API authorizes user-originated Temporal actions before calling Temporal
- external callbacks are verified before being translated into workflow events
- worker fleets receive only the secrets and mounts required for their capability
- sandbox and agent-runtime workers have stronger isolation boundaries than LLM or artifact workers
- provider-profile references are durable handles, not credential payloads
- secret-bearing tool mutations must resolve SecretRefs just in time inside trusted MoonMind-side handlers
- generated runtime files containing credentials are sensitive runtime files, not artifacts by default

### 19.2 Artifact security

Artifacts are the large-data source of truth and require their own controls:

- owner/workflow/run linkage
- ACL and authorization enforcement
- retention class
- redaction policy
- preview limits
- audit logging for sensitive reads/writes
- integrity metadata or content hashes where useful
- explicit handling for diagnostics that may contain provider or runtime-sensitive output

---

## 20. Workflow failure, retry, and dead-letter posture

Activity retries are the default low-level recovery mechanism. Workflow Execution retries must be used sparingly and only when product semantics are explicit.

Rules:

- root workflows should avoid broad automatic Workflow Execution retry unless the product meaning is documented
- workflows should catch failures at orchestration boundaries when they can produce truthful terminal summaries, compensation, or retry-with-policy behavior
- invalid input and contract failures should fail fast and visibly
- rate limits, slot contention, and cooldowns should use orchestration-aware retry/backoff rather than tight Activity retry loops
- permanently failed workflows must be visible through Visibility/projections and require an operator or product-defined repair action
- failure categories must come from the shared error taxonomy
- failures should preserve bounded summary in Memo and detailed diagnostics in artifacts

Dead-letter posture:

- there is no separate hidden durable queue for failed Temporal work
- the failed Workflow Execution, its visibility metadata, error taxonomy category, diagnostics artifacts, and projection repair state are the primary operational “DLQ” surface
- repair workflows, reconciliation sweepers, and operator actions may create follow-up executions, but must link provenance to the failed source

Rerun posture:

- `RequestRerun` means Continue-As-New for the same logical execution only where permitted by the run-history/rerun contract
- terminal rerun behavior must be explicitly implemented; do not assume closed executions accept ordinary updates
- failed-step Resume is not `RequestRerun`; it is a separate linked follow-up execution with pinned source Workflow ID and Run ID

---

## 21. Limits and guardrails

MoonMind must enforce limits at the API, workflow, activity, and worker levels.

Required limit families:

- workflow starts per owner / tenant / service principal
- concurrent open workflows per owner / runtime / provider profile
- child workflows per parent
- activities per phase or workflow run
- workflow history size/event-count continuation thresholds
- payload size and metadata byte limits
- artifact size, count, and retention limits
- Search Attribute count/type/update-frequency budgets
- Memo byte limits
- Query polling rate from dashboards
- Schedule count and schedule-trigger rate
- managed-runtime launch concurrency
- sandbox command duration and resource limits

Limit behavior must be explicit:

- reject before start when the API can safely decide
- wait durably when waiting is a product behavior, such as provider-profile slots
- fail with typed non-retryable errors for invalid payloads or unsupported size
- emit operator-visible diagnostics when limits are hit
- do not silently degrade into projection-only orchestration

---

## 22. Namespaces, tenancy, and retention

MoonMind’s current platform foundation uses:

- `default` for local default operation
- a dedicated namespace such as `moonmind` for shared/enterprise deployments
- 90-day closed execution retention by default, with operator override and storage-cap guardrails

Rules:

- environment isolation should use separate deployment/namespace boundaries where possible
- tenant/user ownership is mirrored into `mm_owner_type` and `mm_owner_id`
- standard user views must enforce ownership at the API layer, not rely on UI filters
- shared namespaces must treat Search Attributes, Memo, IDs, and logs as operator-visible metadata
- namespace retention is for Temporal histories/visibility, not for long-term artifact record guarantees
- artifacts and projections needed beyond Temporal retention must have their own retention and archival posture

---

## 23. Observability and operations

Logs must include, when available:

- Workflow ID
- Run ID
- Workflow Type
- Activity Type
- Activity ID
- attempt
- Task Queue / fleet
- correlation ID
- idempotency key or safe hash
- child workflow ID
- provider/runtime/run identifiers when safe
- schedule ID for schedule-triggered runs

Rules:

- use `workflow.set_current_details` only for bounded operator-facing current state
- do not store prompts, logs, user prose, or provider payloads in current details
- use Search Attributes only for bounded query fields
- Live Logs are secondary observability; artifact persistence is authoritative
- stdout, stderr, diagnostics, reports, patches, and large execution outputs belong in artifacts
- model-provider rate-limit exhaustion must remain visible in summaries and diagnostics
- heavy dashboard Query polling must be bounded so it does not starve workflow workers

### 23.1 Required alerts and operational signals

At minimum, operators need alerts or dashboards for:

- no pollers on critical Task Queues
- workflow task schedule-to-start latency
- activity task schedule-to-start latency
- activity retry storms by Activity Type and provider/runtime
- workflow task failure loops, especially nondeterminism
- failed workflow rate by Workflow Type and error category
- history size/event count approaching continuation threshold
- long-running workflows without progress
- stuck `awaiting_slot` / provider-profile cooldown loops
- Update acceptance failures and latency
- Signal delivery or callback dedupe anomalies
- child workflow orphan/abandon counts
- schedule misfires, skipped overlaps, and catchup executions
- visibility query failures and slow list queries
- projection lag, repair failures, and orphaned projection rows
- artifact write/read failures and preview/redaction failures
- worker restarts, CPU/memory pressure, and graceful-drain failures
- Temporal DB/visibility schema or storage-cap warnings

### 23.2 Game-day expectations

Before production load, MoonMind should rehearse:

- Temporal server restart
- worker rolling restart
- activity worker crash mid-heartbeat
- PostgreSQL outage or restore
- artifact store outage
- visibility degradation with projection fallback
- provider outage
- rate-limit/cooldown storm
- callback replay/duplicate delivery
- stuck schedule overlap
- Continue-As-New carry-forward
- cancellation of a run with active child workflows
- managed-runtime container orphan cleanup

---

## 24. Testing strategy

Temporal architecture is not complete without deterministic and replay-oriented testing.

Required testing layers:

1. Workflow unit tests using Temporal test environment or equivalent.
2. Activity contract tests for typed request/response models and canonical runtime contracts.
3. Replay tests against representative histories for workflow changes with branching behavior or active production histories.
4. Continue-As-New carry-forward tests for every workflow type that uses continuation.
5. Update/Signal concurrency tests for managed session and runtime-control workflows.
6. Child workflow cancellation and Parent Close Policy tests.
7. Schedule overlap/catchup/manual-trigger tests.
8. Projection repair tests for missing, stale, terminal, and orphan rows.
9. Payload-policy tests for large strings, raw bytes, unsupported nested types, and artifact-ref alternatives.
10. Failure injection for provider outages, rate limits, artifact failures, worker restarts, and callback duplicates.
11. Worker topology tests that verify fleet registration, routing, forbidden capabilities, and helper-activity scope.

Changes to workflow code that can affect replay-visible behavior must not be merged solely because unit tests pass. They require replay safety, compatibility planning, or an explicit cutover decision.

---

## 25. Non-goals

This document does not define:

- exact per-workflow lifecycle state machines
- full Activity catalog and all timeout defaults
- complete API response schemas
- UI component layouts
- provider-specific adapter internals
- detailed artifact class registries
- deployment upgrade runbooks
- implementation task checklists
- full run-history product UX
- exact numeric SLOs for every environment

Those live in related docs, specs, issues, runbooks, or implementation tracking artifacts.

---

## 26. Architecture acceptance gates

This hub is acceptable as the normative Temporal architecture when the following are true across docs and code:

1. The workflow catalog in docs matches registered workflow types in code.
2. Worker queues and fleet names match the activity catalog and Compose runtime.
3. Docker Compose self-hosted hardening is explicit: retention, backups, schema upgrades, private network, shard decision, metrics, and recovery.
4. Child workflow Parent Close Policy and Continue-As-New handoff rules are documented per child type.
5. Search Attribute schema, size/type budget, and `mm_updated_at` write policy are documented and enforced.
6. Activity idempotency-key standards are defined per side-effect family.
7. Schedule overlap, catchup, jitter, Workflow ID, and reconciliation rules are fixed.
8. Payload converter and payload-size policy are shared by all Temporal clients/workers, and encryption/codec posture is explicit before sensitive payloads enter history.
9. Projection repair has post-mutation, sweeper, on-read, and startup/backfill paths.
10. Replay-safe deployment, patching/cutover, and replay-test policy are explicit.
11. Failure taxonomy, retry-with-policy, and terminal repair semantics are tied to operator-visible diagnostics.
12. Observability alerts cover workers, visibility, history growth, schedules, projection lag, child orphaning, and provider/runtime failure modes.
13. The document remains honest about current compatibility surfaces and does not claim migration work is complete simply because Temporal is the target substrate.

---

## 27. Summary

MoonMind’s Temporal architecture has one central rule:

> Temporal owns durable orchestration truth; MoonMind adds product contracts, artifacts, adapters, and projections around that truth without creating a second workflow engine.

The design remains strong because it keeps boundaries explicit:

- Workflows orchestrate.
- Activities perform side effects.
- Child workflows own durable sub-lifecycles.
- Artifacts carry large data.
- Visibility indexes bounded metadata.
- Projections are repairable read models.
- Schedules own recurrence.
- Provider/runtime adapters normalize external reality into canonical contracts.
- SecretRefs and artifacts prevent sensitive or large data from leaking into workflow history.

The most important ongoing hardening work is operational: self-hosted Compose reliability, replay-safe evolution, child workflow handoff rules, Search Attribute governance, idempotency keys, schedule semantics, payload/security posture, projection repair, and concrete observability.
