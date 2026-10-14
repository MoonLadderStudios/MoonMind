# Temporal Architecture

**Audience:** backend, infra, managed-runtime, integrations, dashboard, workflow authors, operators

---

## 1. Purpose

MoonMind uses Temporal as its durable orchestration substrate. This document
is the top-level description of workflow identity, catalog, worker topology,
scheduling, Visibility, projections, payload discipline, agent integration,
and the guardrails around them.

Some payloads carry `task` / `taskId` field names; those are product/API terms.
Inside the Temporal architecture, the durable entity is a **Workflow
Execution**. Compatibility tables, app DB projections, dashboard adapters, and
product APIs are not parallel orchestrators: workflow-owned state/history is
authoritative, and app DB projections are repairable read models.

---

## 2. Repo-aligned baseline

This architecture describes the MoonMind repository, not a generic Temporal
deployment template. Temporal is self-hosted through Docker Compose with
PostgreSQL persistence and Visibility; Temporal Cloud and Kubernetes are not
the production target. Artifacts default to S3-compatible (MinIO-compatible)
storage. Workers form six capability-based fleets (§9). The shared data
converter is the Pydantic data converter
(`moonmind/workflows/temporal/data_converter.py`) with no payload-encryption
codec. Compatibility adapters and app-local projections remain live alongside
the Temporal core paths.

---

## 3. Related docs

This document is the architecture hub. Detailed contracts live in the other
`docs/Temporal/` documents (platform foundation, workflow type catalog and
lifecycle, activity catalog and worker topology, managed and external agent
execution, visibility and UI query model, artifact system, source of truth and
projections, execution product model, type safety, run history and new-run
semantics, error taxonomy, scheduling), plus
`docs/Artifacts/ArtifactPresentationContract.md`,
`docs/Security/ProviderProfiles.md`, `docs/Security/SecretsSystem.md`,
`docs/ManagedAgents/ManagedAgentArchitecture.md`,
`docs/ManagedAgents/CodexCliManagedSessions.md`,
`docs/ManagedAgents/DockerOutOfDocker.md`, `docs/Observability/LiveLogs.md`,
`docs/Steps/SkillSystem.md`, and `docs/MoonMindArchitecture.md`.

Exact lifecycle state machines, complete activity catalogs, API schemas, and
query syntax live in those docs rather than here, so they have one owner each.

---

## 4. Architecture statement

MoonMind execution is Temporal-native: Workflow Executions orchestrate,
Activities perform side effects, Task Queues route to workers, Child Workflows
hold durable sub-lifecycles, Updates/Signals/Queries carry interaction,
Timers and Schedules handle time, Visibility indexes bounded metadata, and
Continue-As-New bounds history.

MoonMind adds domain contracts above Temporal: Workflow / Run product
vocabulary, the Plan and Step Ledger, Tool / Skill / Preset execution
contracts, artifact and report presentation, Agent Adapter contracts, Provider
Profile and SecretRef policies, Managed Session continuity, Runner Profile and
workload-container policies, and dashboard read models.

MoonMind does not introduce a second durable orchestration substrate for
Temporal-managed work.

---

## 5. Operating model

### 5.1 MoonMind API / Control Plane

The API authenticates and authorizes callers; starts workflows; sends Updates,
Signals, Cancels, Terminations, and Schedule operations; exposes workflow
product and execution APIs; maintains projections and compatibility fields; and
enforces ownership, idempotency, and contract validation before invoking
Temporal. It is a policy boundary, not a competing scheduler or orchestration
engine.

### 5.2 Temporal Server

Temporal Server is self-hosted through Docker Compose, uses PostgreSQL for
persistence and Visibility, owns workflow history, timers, retries, workflow
state, and schedule triggers, and runs inside MoonMind's private service
network rather than as a public endpoint.

Because Docker Compose is the operating posture, production hardening is
Compose-aware rather than deferred to a managed service: volume backup and
restore, namespace bootstrap and retention, Visibility schema-upgrade
rehearsal, a shard-count decision before production load, private network
exposure, health checks, metrics, alerts, and capacity envelopes.

### 5.3 Workers, artifacts, and Visibility

The workflow fleet runs deterministic workflow code: orchestration, child
starts, domain state transitions, and bounded Search Attribute updates, with
no general side effects beyond the helper Activities in §9.1. Activity fleets
execute side effects, separated by capability, secrets, isolation, egress, and
resource profile. The artifact store holds large inputs, outputs, logs,
diagnostics, manifests, prompt bundles, and continuity data, and owns
retention, previews, redaction, and artifact ACLs. Temporal Visibility is the
bounded list/filter/count index; app DB projections may lag but converge to
Temporal-owned state.

### 5.4 Managed runtime and external integration planes

The managed runtime plane supervises MoonMind-managed CLI/container runtimes
(launch, status, results, cleanup, log capture) through Activities,
`MoonMind.AgentRun`, and, for workflow-scoped sessions, `MoonMind.AgentSession`.
The external integration plane talks to delegated providers such as Jules,
OpenClaw, GitHub, and Jira, verifies callbacks, and normalizes provider
payloads into canonical runtime contracts before workflow code sees them.

---

---

## 6. Architectural invariants

### 6.1 Workflows orchestrate; Activities do side effects

Workflow code stays deterministic so Temporal can replay it. Workflows branch
on workflow-owned state, start children, execute Activities, wait on timers,
Signals, and Updates, upsert bounded Search Attributes, expose Queries, and
Continue-As-New. They do not perform network calls, filesystem or database
I/O, subprocess execution, provider SDK calls, raw clock or random reads
outside Temporal APIs, secret lookup, or unbounded handling of logs,
artifacts, prompts, transcripts, manifests, or provider payloads. Those belong
in Activities or external systems referenced by compact refs.

### 6.2 Child workflows represent durable sub-lifecycles

A child workflow is used when the child has its own durable lifecycle,
cancellation semantics, external wait, retry posture, operator-facing state, or
history-management needs, rather than collapsing that work into a giant
long-running Activity. First-class examples:

- `MoonMind.AgentRun` for one true agent execution
- `MoonMind.AgentSession` for one workflow-scoped managed session
- `MoonMind.MergeAutomation` for post-run PR readiness and resolver launch
- `MoonMind.ProviderProfileManager` for long-lived provider-profile slot coordination
- `MoonMind.OAuthSession` for OAuth / terminal-auth lifecycle support
- `MoonMind.ManagedSessionReconcile` for bounded operational reconciliation

Each child start chooses a Parent Close Policy deliberately, and the parent
awaits child-start acceptance before treating the child as owned.
`MoonMind.UserWorkflow` cancellation requests cancellation of active
`MoonMind.AgentRun` children for best-effort cleanup. Abandoned operational
children are allowed only when their IDs are durably recorded and a
reconciliation owner exists; terminate-style close is reserved for tightly
coupled subgraphs. A parent does not Continue-As-New with active children
without recorded child IDs and a handoff strategy, and fanout is bounded
through batching, phases, aggregation children, or Continue-As-New.

### 6.3 Activities are bounded or heartbeat-aware

Activities are bounded side-effect calls. Long-running work is normally
workflow orchestration plus short Activities, durable timers, callbacks, status
polling, or child workflows. Heartbeat-aware Activities are reserved for side
effects that are themselves long single operations, such as sandbox execution,
streaming-gateway work, or runtime launch/publish.

### 6.4 Task Queues are routing plumbing

Task Queues are internal routing labels for worker fleets, not product queues;
MoonMind makes no FIFO promise to users. Routing is by capability and security
boundary, not by provider brand or product noun.

### 6.5 Visibility is for bounded query fields

Search Attributes hold small, bounded, queryable fields. They never contain
full prompts, manifests, logs, transcripts, step ledgers, provider payloads,
secrets, arbitrary user prose, raw error bodies, or generated code.

Visibility is eventually consistent and subject to indexing delay, so it is
not used for transactional gating, strongly consistent detail reads, or
workflow-control decisions. Queries, workflow responses, Describe/History,
artifacts, or Activity-backed lookups serve stronger or richer reads.

### 6.6 Artifacts carry large data

Workflow payloads, history, Search Attributes, and Memo carry compact refs,
summaries, hashes, and bounded metadata. Content too large or too sensitive to
inspect in Temporal UI or logs is an artifact ref or a SecretRef.

### 6.7 Updates, Signals, Queries, Cancels, and Terminates have distinct roles

| Primitive | Use when | Not used for |
| --- | --- | --- |
| Update | Caller needs synchronous acceptance/rejection and usually a result | fire-and-forget notifications |
| Signal | Asynchronous event where the caller does not wait for workflow-side acceptance | request/response mutation semantics |
| Query | Side-effect-free live state read | mutation, validation side effects, external I/O |
| Cancel | Graceful user/API cancellation request | emergency forced stop |
| Terminate | Ops-only forced stop | ordinary user cancel or cleanup claim |

Public mutating Updates have deterministic, side-effect-free validators.
Externally supplied messages carry a bounded idempotency marker (dedupe key,
request ID, provider event ID, or Update ID). Conflicting async Update/Signal
mutations are serialized with workflow-safe coordination or queued through the
main loop. API callers get clear behavior when an Update cannot be accepted
because workflow workers are unavailable.

### 6.8 Schedules own recurring starts

Recurring starts use Temporal Schedules rather than cron/beat-style
orchestration; workflow timers handle in-workflow waits (§15).

### 6.9 Canonical runtime contracts cross workflow boundaries

True agent-runtime Activities return `AgentRunHandle`, `AgentRunStatus`, and
`AgentRunResult` directly. Provider-specific data lives in canonical
`metadata`, so workflow code never reconstructs canonical contracts from raw
provider responses.

### 6.10 Type safety is part of the architecture

Temporal boundary payloads are durable contracts. Workflow inputs, Activity
requests/responses, Signals, Updates, Queries, and Continue-As-New payloads use
named typed models; dict-shaped edge shims coerce immediately into them.
Evolution is additive where possible; non-additive changes need replay
compatibility, shims, or a controlled migration. Details are in
`TemporalTypeSafety.md`.

---

## 7. Workflow catalog

The live workflow catalog is the generated reference
`WorkflowTypeCatalogGenerated.md` (exact Temporal type, module/class owner,
and projection role for every production registration, produced from
`moonmind/workflows/temporal/workflow_registry.py`). Roles group as
user-submitted roots, manifest-backed orchestration, durable agent-execution
wrappers, session and coordination workflows, and operational/maintenance
workflows.

Workflow Type names are stable contracts, added only for materially distinct
lifecycle behavior rather than per provider, fleet, or queue. Product Workflow
Execution vocabulary maps primarily to `MoonMind.UserWorkflow`
(`moonmind/workflows/temporal/workflows/run.py`). Because the generated
reference comes from code registration, it is the source to trust when prose
and code disagree.

---

## 8. Identifier model

### 8.1 Workflow ID

`workflowId` is the durable identifier for Temporal-managed work: `mm:<uuid>`
for ordinary executions, and stable prefixed IDs such as
`provider-profile-manager:<runtime>` or `oauth-session:<session_id>` for
singleton or support workflows. It is used for routes, links, cache keys, and
API lookup, never encodes secrets, and is preserved across Continue-As-New.
Child workflow IDs are deterministic where duplicate child starts must be
prevented.

### 8.2 Run ID

`runId` identifies one concrete Temporal run. It is for debugging, diagnostics,
and history correlation, may appear in detail views as debug metadata, and is
not the product handle because it changes across Continue-As-New, retry
chains, resets, and schedule-triggered executions.

### 8.3 `taskId` fields

For Temporal-backed workflow rows that carry `taskId`, it equals `workflowId`;
APIs may return both, and `taskId` is never minted as a second durable identity
for the same execution.

### 8.4 Workflow ID reuse and conflict policy

Ordinary starts use a new `mm:<uuid>` unless an API idempotency key resolves
to an existing accepted start; singleton managers use stable IDs with an
ensure/start-or-signal pattern; schedule runs get a fresh Workflow ID per run
(§15.1); rerun uses Continue-As-New only where the rerun contract permits; and
failed-step recovery is a linked follow-up execution. The per-type matrix is
in `WorkflowTypeCatalogAndLifecycle.md` and
`WorkflowRunHistoryAndNewRunSemantics.md`.

---

## 9. Worker and Task Queue topology

| Fleet | Queue(s) | Primary responsibility |
| --- | --- | --- |
| `workflow` | `mm.workflow.user.v2`, `mm.workflow`, `mm.workflow.merge_automation` | deterministic workflow code and narrow helper Activities |
| `artifacts` | `mm.activity.artifacts` | artifact lifecycle, execution projections, support persistence, OAuth/provider-profile support |
| `llm` | `mm.activity.llm` | planning, validation, review, LLM-bound work |
| `sandbox` | `mm.activity.sandbox` | isolated repo/process execution |
| `integrations` | `mm.activity.integrations` | external provider APIs, repo and Jira/GitHub calls, merge automation integration work |
| `agent_runtime` | `mm.activity.agent_runtime`, `mm.activity.agent_runtime.control` | managed runtime launch, supervision, auth runner, status, result, cleanup, terminal-evidence handoff |

Queues are split only for different secrets, isolation, scaling, egress, or
resource policy. Worker startup fails closed if the fleet or activity catalog
is invalid, so a misrouted handler surfaces at startup rather than as a runtime
workflow-task failure. Concurrency limits are observable, and graceful drain
lets in-flight activities heartbeat, cancel, or time out predictably. Details
are in
[ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md).

### 9.1 Workflow helper-activity exception

The workflow fleet hosts a helper Activity only when it is fast, bounded,
needs no broad secrets, Docker access, sandbox privileges, or provider
mutation, and is listed in the activity topology; heavier work moves to a
capability-appropriate queue. The current registration, including retained
checkpoint-persistence handlers kept for replay compatibility, is in
[ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md#51-workflow-fleet-exception-rule)
§5.1; that registration is not a general allowance for workflow-fleet I/O.

### 9.2 Local Activities

Local Activities are limited to fast helper work where durable server-side
queuing is unnecessary and loss/retry semantics are acceptable. They are not
used for provider calls, DB or artifact writes, sandbox execution, runtime
launch, or secret resolution, nor to bypass task queue isolation.

---

## 10. Activity model

Activity Type names are stable contracts; incompatible semantic changes get a
new Activity Type. Current families include `artifact.*`, `execution.*`,
`manifest.*`, `plan.*`, `mm.skill.execute`, `sandbox.*`, `provider_profile.*`,
`oauth_session.*`, `integration.<provider>.*`, `repo.*`, `merge_automation.*`,
`agent_runtime.*`, `step.review`, and `agent_skill.*`. The exact catalog,
timeouts, retry policies, routing, and heartbeat requirements are in
`docs/Temporal/ActivityCatalogAndWorkerTopology.md`.

Side-effecting Activities are idempotent or safely deduplicated, have explicit
timeouts, and classify non-retryable contract failures explicitly. Activities
return results to workflows, which own Search Attribute updates.

### 10.1 Idempotency-key standard

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

The attempt number is not the business key, so retrying a start-like Activity
does not create duplicate external jobs or launches. Keys appear in logs and
diagnostics, hashed if sensitive.

---

## 11. Visibility and UI query model

Required Memo fields are `title` and `summary`. List rows render without
artifact hydration, while detail views use Queries and artifacts for richer
state. Ordinary workflow lists may scope to `MoonMind.UserWorkflow` and
`mm_entry = run`; operator views may expose broader scopes. Search Attributes
and Memo are operator-visible, so they carry no secrets or sensitive prose.

### 11.1 Search Attribute governance

PostgreSQL Visibility has finite custom Search Attribute count, type, and size
budgets, so the set is treated as a governed schema.

| Field | Required | Type intent | Update frequency |
| --- | --- | --- | --- |
| `mm_owner_type` | Yes | keyword | set at start; immutable |
| `mm_owner_id` | Yes | keyword | set at start; immutable |
| `mm_state` | Yes | keyword | domain-state transitions only |
| `mm_updated_at` | Yes | datetime | meaningful user-visible mutations only |
| `mm_entry` | Yes | keyword | set at start; immutable |
| `mm_repo` | Optional | keyword | set when repo filtering is required |
| `mm_integration` | Optional | keyword | set when integration filtering is required |
| `mm_target_runtime` | Optional | keyword_list | one-item runtime facet; not sortable |
| `mm_target_skill` | Optional | keyword_list | one-item primary skill facet; not sortable |
| `mm_scheduled_for` | Optional | datetime | deferred/scheduled execution metadata |

`mm_updated_at` moves on meaningful user-visible mutations (state
transitions, accepted Updates, bounded progress checkpoints, title/summary
changes), not on heartbeats, polling ticks, or internal retries, so it does not
become a high-churn telemetry feed.

### 11.2 Memo bounds

Memo is presentation metadata, not payload storage. `title` and `summary`
byte limits are enforced before write; large prose, detailed errors, and
diagnostics go to artifacts, and Memo carries no prompts, manifests, logs,
provider payloads, or secrets.

### 11.3 `mm_state` mapping

Workflows own `mm_state`; projections may mirror it but not redefine it.
Native terminal statuses reconcile to terminal domain states: completed →
`completed`; failed / timed out / terminated → `failed`; canceled →
`canceled`. A raw Temporal `Completed` with a workflow-owned terminal
`mm_state` keeps that state, so a graceful cancellation that finalized and
returned normally projects as `canceled` rather than success. When a native
failure/cancellation close status conflicts with `mm_state`, Temporal wins and
projection repair corrects the row; such a conflict may indicate a workflow
bug and is alertable.

---

## 12. API and projection posture

The MoonMind API (§5.1) is the policy and authorization boundary for
user-facing Temporal operations, including Schedule management and
Visibility-backed list/count, and maintains DB projections for joins,
authorization, compatibility, degraded reads, and repair. Projection rows may
add joins, denormalization, reconciliation state,
compatibility aliases, and degraded-mode markers, but do not redefine workflow identity, lifecycle state, terminal outcome, retry/cancel
semantics, query ordering, step truth, or run-history truth.

### 12.1 Projection repair

Projection repair is part of the architecture. It runs on four triggers:
post-mutation refresh after start/update/signal/cancel where practical, a
periodic sweeper comparing recent executions to projection rows, repair-on-read
for missing, stale, or inconsistent rows, and startup/backfill repair after
outages, deployments, or schema changes.

| Drift type | Repair behavior |
| --- | --- |
| Temporal execution exists, projection missing | create/upsert projection from Temporal truth |
| projection exists, Temporal execution missing | mark orphaned/quarantine; do not present as active |
| projection `run_id` stale after Continue-As-New | update row in place by Workflow ID |
| projection state/close status disagrees | trust Temporal and overwrite projection |
| memo/Search Attributes stale | refresh bounded fields from Temporal-visible state |
| artifact refs diverge | refresh from workflow/artifact linkage and deduplicate |

Degraded-mode reads are truthful: a route that falls back to projection data
because Visibility is degraded marks stale/fallback mode internally and does
not claim canonical freshness.

---

## 13. Managed and external agent execution

`MoonMind.UserWorkflow` is the workflow-level root. When a plan step requires a
true agent runtime, it starts `MoonMind.AgentRun`, which owns exactly one agent
execution lifecycle. Managed and external agents share one conceptual
lifecycle: prepare compact refs, start asynchronously, wait durably, read
canonical status, fetch the canonical result, publish artifacts, and clean up.

Managed runtimes add provider-profile slot coordination, launcher/supervisor/
store components, artifact-first log capture, and optional
`MoonMind.AgentSession` continuity; external providers use adapters that
return canonical runtime contracts.

### 13.1 Async callback pattern

External provider callbacks are authenticated before workflow delivery,
correlated to a known run or workflow, deduplicated by provider event ID or
equivalent, normalized into typed compact payloads, and delivered by Signal or
Update according to caller semantics. Verification happens at the API or
Activity boundary, because workflow code cannot perform cryptographic
verification or provider network calls deterministically.

### 13.2 Saga and compensation posture

Workflows with side effects across external systems do not rely on implicit
rollback: they own explicit, idempotent compensation, and failure summaries
state which side effects happened and which cleanup succeeded, failed, or was
skipped. Best-effort cancellation cleanup is never reported as successful if it
was not attempted or failed.

---

## 14. Managed sessions and workload containers

MoonMind distinguishes three concepts:

- A **true agent run** is owned by `MoonMind.AgentRun`, represents one durable agent execution step, and may be managed by MoonMind or delegated to an external provider.
- A **managed session** is owned by `MoonMind.AgentSession` and provides workflow-scoped runtime continuity, session epoch tracking, turn/control handling, and continuity artifacts. The live session plane is Codex-backed.
- A **workload container** is launched through a tool/workload Activity and runner profile and returns ordinary tool results and artifacts. It is not a managed session unless the workload is itself a true agent runtime.

`clear_session` creates a new continuity epoch with explicit reset evidence.
Session continuity is artifact-backed rather than container-local, workload
and session identities stay distinct in APIs and UI, and promoting a workload
to a managed session is an explicit lifecycle decision.

---

## 15. Scheduling

Temporal Schedules are the authoritative mechanism for recurring workflow
starts and operational sweepers (recurring workflow definitions, manifest
runs, managed-session reconciliation, artifact or projection sweepers). DB
schedule rows are product-management projections; Temporal Schedule objects
are the execution trigger. Manual recurring runs go through the schedule
model, external cron loops do not start Temporal-managed workflows, and every
schedule declares overlap, catchup, jitter, target Workflow Type, and Workflow
ID policy.

### 15.1 Recurring run identity

Each tick, and each manual run-now through the schedule's trigger path,
creates a fresh run Workflow ID associated with the schedule definition, with
`taskId == workflowId`. Run history belongs to the schedule detail/read model,
and duplicate ticks or retried schedule operations are covered by an
idempotency/conflict policy.

### 15.2 Overlap and catchup

Defaults are conservative: recurring user workflows skip or buffer one rather
than overlapping without bound; operational sweepers skip if still active
unless designed for overlap; reconciliation workflows overlap only when
idempotent and partitioned. Each schedule type also defines behavior when it
was paused or Temporal was down.

### 15.3 Schedule / DB reconciliation

A DB schedule missing in Temporal is repaired or marked unhealthy; a Temporal
schedule whose DB row is missing is paused/quarantined or its projection
recreated per operator policy; pause/enabled drift reconciles to the
documented authority with an audit event. Scheduled run history derives from
Temporal executions plus schedule metadata, not an independent cron log.

---

## 16. Cancellation and termination

User cancel maps to Temporal workflow cancellation: the domain state moves to
`canceled`, cancellation propagates to in-flight children where appropriate,
best-effort cleanup runs through Activities, held provider-profile slots are
released, and a truthful final summary and diagnostics are published.

Forced termination is ops-only, for runaway workflows, policy violations, or
emergencies. It does not pretend cleanup occurred, classifies the outcome
truthfully, and is followed by projection repair.

---

## 17. Continue-As-New and history management

Continue-As-New keeps replay bounded for long-lived or event-heavy workflows:
long wait/poll loops, managed session control histories, provider-profile
manager loops, large manifests or repeated graph phases, explicit reruns that
preserve durable identity, and histories approaching configured thresholds.

Long waits also avoid high-cardinality no-op history: a polling loop whose
domain state has not changed coalesces evidence rather than recording each
identical observation as a step attempt. It keeps the first and latest
observations in compact state, records durable artifacts only for state
transitions, terminal outcomes, operator actions, and coarse periodic
checkpoints, keeps repeated activity inputs compact, and skips Memo/Search
Attribute upserts and step-attempt manifests on internal polling ticks.

Workflow history holds the commands needed for replay and enough compact
state to explain the current wait, not every "still waiting" sample.

Continue-As-New preserves the Workflow ID, business correlation IDs, required
Search Attributes and Memo, and the artifact refs and compact state needed to
resume, through a typed carry-forward model. Nothing depends on a stable
`runId`, and active children require an explicit handoff (§6.2).
Continue-As-New is not automatically a
user-visible rerun; manual rerun, automatic lifecycle rollover, and major
reconfiguration are distinguished in summaries and projections.

---

## 18. Replay-safe evolution and deployment

Temporal workflow history is durable, so workflow changes are replay-safe.
Immutable production releases use Temporal Worker Deployment routing. The
installed release manifest supplies the worker Build ID; Temporal owns the
effective current/ramping route, so starting a different worker image does not
by itself promote that route. The image-owned controller qualifies a
candidate, promotes it with compare-and-set, verifies ordinary installed
traffic, and keeps previous pollers only as bounded transitional drainage while
Temporal still requires them for open executions; the steady state is one
installed fleet recreated in place. The
[deployment update documentation](../Steps/DockerComposeUpdateSystem.md)
describes promotion, drainage, rollback, and recovery.

Versioning preserves availability as well as identity. Process liveness and
candidate registration cannot prove that ordinary work reaches a worker, so
product readiness requires fresh server routing/poller evidence and observed
unpinned execution. An unavailable route is a recoverable infrastructure
condition with a durable owner, not a healthy-looking deployment or a
user-input failure, and the trusted release recovery owner stays reachable when
application workflow dispatch is broken.

Build identity does not establish history compatibility. AUTO_UPGRADE
executions need compatible workflow code and payload readers; pinned
executions keep exact compatible workers until drainage is confirmed. Identity
canaries, replay tests, and ordinary post-promotion journeys establish
different facts, and a test that invokes promotion directly does not prove
autonomous recovery.

Handlers referenced by open histories are not removed or renamed without a
compatibility plan; incompatible Activity contract changes get new Activity
Types; branching workflow changes get replay tests; workflow logic reads
mutable config and feature flags only through start inputs, workflow state, or
recorded Activity results; and old-compatible code stays available for open
executions unless they are migrated deliberately.

---

## 19. Payload, data converter, and security boundary

All Temporal clients and workers use the shared Pydantic data converter.
Boundary models validate compactness and supported wire shapes: raw nested
bytes, large mappings, unbounded strings, and arbitrary provider payloads are
not accepted Temporal payload shapes, and oversized payloads are rejected in
favor of artifact refs. No payload-encryption codec is configured, so Temporal
history is not a protected store for sensitive business payloads; Search
Attributes, Memo, Workflow IDs, Activity IDs, Task Queue names, logs, and
unencrypted artifacts are treated as operator-visible.

### 19.1 Secret handling

Raw secrets never appear in workflow inputs, history, Search Attributes, Memo,
logs, or ordinary artifacts. Fleet separation lets secrets and mounts follow
capability, with sandbox and agent-runtime workers isolated more strongly;
credentials placed in the operator `.env` still reach every worker that loads
it (see [ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md#51-workflow-fleet-exception-rule)
§5.1). Provider-profile references are durable
handles, not credential payloads. Secret-bearing tool mutations resolve
SecretRefs just in time inside trusted MoonMind-side handlers, and generated
runtime files containing credentials are sensitive runtime files, not
artifacts by default.

### 19.2 Artifact security

Artifacts carry their own controls: owner/workflow/run linkage, ACLs,
retention class, redaction policy, preview limits, audit logging for sensitive
access, and integrity hashes.

---

## 20. Workflow failure, retry, and dead-letter posture

Activity retries are the default low-level recovery mechanism; Workflow
Execution retries are used sparingly and only where product semantics are
explicit.

Failure classification survives Activity, adapter, child-workflow, and parent
boundaries. Temporary capacity and unavailable routing keep a typed
infrastructure category, a durable wait owner, and a cumulative budget, so
wrapping a child result cannot turn them into non-retryable user errors.
Recovery preserves the selected provider, model, credentials, source,
publication intent, and work evidence; exhaustion records the original cause.
The issue lifecycle owner settles claims and remote effects before successor
writes, and a new workflow ID never clears an unresolved predecessor.

Workflows catch failures at orchestration boundaries when they can produce
truthful summaries, compensation, or retry-with-policy. Invalid input and
contract failures fail fast. Rate limits, slot contention, and cooldowns use
orchestration-aware backoff rather than tight Activity retry loops. Categories
come from the shared error taxonomy, with a bounded Memo summary and detailed
diagnostics in artifacts.

There is no hidden dead-letter queue: the failed execution, its visibility
metadata, error category, and diagnostics artifacts are the operational "DLQ"
surface, and follow-up executions link provenance to the failed source.

Rerun posture:

- active `RequestRerun` may Continue-As-New only where the workflow type supports the [run-history/rerun contract](WorkflowRunHistoryAndNewRunSemantics.md#7-new-run-semantics)
- terminal `RequestRerun` starts a fresh linked execution (new Workflow ID and Run ID) and leaves the closed source unchanged; clients follow the returned destination
- failed-step recovery is a separate linked follow-up execution with pinned source Workflow ID and Run ID

---

## 21. Limits and guardrails

Limits are enforced at the API, workflow, activity, and worker levels: starts
and concurrency per owner, runtime, and provider profile; fanout; history
thresholds; payload, Memo, and artifact sizes; Search Attribute budgets; Query
polling; schedule rates; managed-runtime launches; and sandbox resources.

When a limit is hit, MoonMind rejects before start when the API can safely
decide, waits durably when waiting is the product behavior (such as
provider-profile slots), fails with typed non-retryable errors for invalid
payloads or unsupported size, and emits operator-visible diagnostics. It does
not silently degrade into projection-only orchestration.

---

## 22. Namespaces, ownership, and retention

MoonMind is a single-user application: one operator per instance. The platform
foundation uses `default` for local operation, a dedicated namespace such as
`moonmind` per independent deployment (namespaces isolate deployments, not
users), and 90-day closed execution retention by default with operator
override and storage-cap guardrails.

Standard views enforce ownership at the API layer rather than through UI
filters. Namespace retention covers Temporal histories and visibility only;
artifacts and projections needed beyond it have their own retention.

---

## 23. Observability and operations

Logs include, when available, workflow and run IDs and type, activity type,
ID, and attempt, task queue, correlation ID, idempotency key or safe hash,
child workflow ID, safe provider/runtime identifiers, and schedule ID.

`workflow.set_current_details` carries only bounded operator-facing state.
Live Logs are secondary; artifact persistence of stdout, stderr, diagnostics,
and large outputs is authoritative. Dashboard Query polling is bounded so it
does not starve workflow workers.

### 23.1 Operational signals

Operators need alerts or dashboards covering pollers and schedule-to-start
latency, retry storms and workflow-task failure loops (especially
nondeterminism), failure rates by type and error category, history growth,
stalled workflows and slot/cooldown loops, Update and Signal/callback
anomalies, child orphaning, schedule misfires, visibility query health,
projection lag and repair failures, artifact failures, worker resource and
drain problems, and Temporal storage-cap warnings.

---

## 24. Testing strategy

Temporal behavior is covered by workflow tests in the Temporal test
environment, Activity contract tests, replay tests against representative
histories, Continue-As-New carry-forward tests, Update/Signal concurrency
tests, child cancellation and schedule tests, projection repair tests,
payload-policy tests, failure injection, and worker topology tests. Passing
unit tests alone do not establish that a replay-visible workflow change is
safe; that also needs replay compatibility, a compatibility plan, or a
controlled migration.

---

## 25. Summary

> Temporal owns durable orchestration truth; MoonMind adds product contracts, artifacts, adapters, and projections around that truth without creating a second workflow engine.

Workflows orchestrate, Activities perform side effects, child workflows own
durable sub-lifecycles, artifacts carry large data, Visibility indexes bounded
metadata, projections are repairable read models, Schedules own recurrence,
and adapters normalize external reality into canonical contracts, while
SecretRefs and artifacts keep sensitive or large data out of workflow history.
