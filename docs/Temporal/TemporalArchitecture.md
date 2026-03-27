# MoonMind Temporal Architecture

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-TemporalArchitecture.md`](../tmp/remaining-work/Temporal-TemporalArchitecture.md)  
**Status:** Active  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-27  
**Audience:** backend, infra, dashboard, workflow authors

## 1. Purpose

This document defines the steady-state Temporal architecture used by MoonMind.

MoonMind now uses **Temporal as its only live execution substrate** for task
execution. MoonMind still adds product and domain contracts above Temporal, but
it no longer maintains a parallel pre-Temporal execution engine.

## 2. Related docs

- `docs/MoonMindArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/TemporalScheduling.md`
- `docs/Tasks/TaskArchitecture.md`

## 3. Core architecture stance

MoonMind execution is Temporal-native:

- **Workflow Executions** are the durable orchestration primitive.
- **Activities** perform all side effects.
- **Visibility** is the source of truth for execution list/query behavior.
- **Schedules and timers** own time-based execution behavior.
- **MoonMind APIs and projections** adapt Temporal-backed execution into
  task-shaped product surfaces without becoming a second workflow engine.

MoonMind keeps only domain concepts Temporal does not provide directly:

- **Tool**
- **Plan**
- **Artifact**
- **Agent adapter / runtime contract**
- **Proposal and schedule product modules**

## 4. Vocabulary

- **User-facing product term:** `task`
- **Runtime term:** `workflow execution`
- **Temporal Task Queue:** routing plumbing only, never a user-facing queue
  product

Compatibility rule:

- for Temporal-backed task surfaces, `taskId == workflowId`
- `temporalRunId` is detail/debug metadata, not the primary route key

## 5. Deployment shape

MoonMind deploys Temporal in Docker Compose with a private-network-only posture.

Core Temporal runtime shape:

| Component | Role |
| --- | --- |
| `temporal` | Temporal server with Postgres persistence and visibility |
| `temporal-namespace-init` | Registers namespace and search attributes |
| `temporal-worker-workflow` | Deterministic workflow orchestration |
| `temporal-worker-artifacts` | Artifact I/O activities |
| `temporal-worker-llm` | LLM-call activities |
| `temporal-worker-sandbox` | Shell, repo, and CLI activities |
| `temporal-worker-agent-runtime` | Agent-runtime orchestration activities |
| `temporal-worker-integrations` | External integration activities |
| `postgres` | Shared Postgres for MoonMind + Temporal |
| `minio` | S3-compatible artifact storage |

Optional tooling:

- `temporal-ui`
- `temporal-admin-tools`
- `temporal-visibility-rehearsal`

Local Docker deployments default to the `default` namespace unless
`TEMPORAL_NAMESPACE` is overridden.

## 6. Workflow model

MoonMind keeps a small root workflow catalog:

| Workflow | Purpose |
| --- | --- |
| `MoonMind.Run` | Primary task execution workflow |
| `MoonMind.AgentRun` | Durable wrapper around delegated agent runtime execution |
| `MoonMind.ManifestIngest` | Manifest-driven graph ingestion and execution |
| provider profile manager workflow | Slot management for managed runtime profiles |

Rules:

- do not split root workflows by provider brand alone
- do not create separate workflow families for old product nouns
- route provider/runtime differences through activities, child workflows, and
  execution parameters

## 7. Activity and worker model

All side effects belong in Activities:

- LLM calls
- filesystem and repo operations
- shell and sandbox execution
- artifact read/write
- external integrations
- callback verification and polling

Default queue posture is intentionally small:

- `mm.workflow`
- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.agent_runtime`
- `mm.activity.integrations`

Provider-specific sub-queues are optional operational refinements, not part of
the product model.

## 8. API and control-plane model

The API service is the primary Temporal client in runtime operation.

Primary execution lifecycle surface:

- `POST /api/executions`
- `GET /api/executions`
- `GET /api/executions/{workflowId}`
- `POST /api/executions/{workflowId}/update`
- `POST /api/executions/{workflowId}/signal`
- `POST /api/executions/{workflowId}/cancel`
- `POST /api/executions/{workflowId}/reschedule`

Task-shaped product routes such as `/tasks/*` remain valid, but they are
compatibility/product surfaces over the same Temporal executions.

## 9. Visibility, projections, and read models

For Temporal-managed work:

- **Temporal workflow state/history** is authoritative for execution lifecycle
- **Temporal Visibility** is authoritative for indexed list/query behavior
- **Postgres projections** such as `TemporalExecutionRecord` are downstream read
  models for compatibility, enrichment, repair, and degraded-mode support

Canonical execution metadata should be mirrored into:

**Search Attributes**

- `mm_owner_id`
- `mm_owner_type`
- `mm_state`
- `mm_updated_at`
- `mm_entry`
- optional bounded fields such as `mm_repo`, `mm_integration`,
  `mm_scheduled_for`

**Memo**

- `title`
- `summary`
- safe artifact refs and related display metadata

Strict rule: projections must never become a second lifecycle engine.

## 10. Payload and artifact discipline

Temporal payloads and workflow history must remain small.

Use:

- `ArtifactRef` values for large instructions, manifests, plans, logs, diffs,
  and generated outputs
- bounded JSON for workflow parameters, state summaries, and patches

Artifact storage model:

- MinIO/S3-compatible storage for bytes
- Postgres metadata/index rows for artifact records and linkage
- workflow-linked reports such as `reports/run_summary.json` for compact
  operator-facing outcomes

## 11. Scheduling and waiting

MoonMind uses Temporal-native time controls:

- **Immediate execution:** normal workflow start
- **Deferred one-time execution:** `start_delay`
- **Reschedulable waiting:** in-workflow timer and signal pattern
- **Recurring automation:** Temporal Schedules reconciled from MoonMind schedule
  definitions

Temporal Schedules and Timers are the authoritative mechanism for time-based
execution starts.

## 12. Human and external control model

Preferred workflow controls:

**Updates**

- `UpdateInputs`
- `SetTitle`
- `RequestRerun`

**Signals**

- `Approve`
- `Pause`
- `Resume`
- `ExternalEvent`
- reschedule signal where applicable

Approval policy remains a MoonMind concern; Temporal provides the durable
transport and execution semantics.

## 13. Reliability and security

Reliability rules:

- activity retries are the default recovery path
- side-effecting activities must be idempotent or safely deduplicated
- Continue-As-New is used to control workflow history growth
- already-running executions and persisted payloads must be protected by
  compatibility-aware workflow-boundary contracts

Security rules:

- Temporal is self-hosted and private-network only
- worker fleets are segmented by capability and secret boundary
- sandbox execution is isolated separately from LLM and integration workers
- artifact access is mediated through MoonMind authorization and short-lived
  grants

## 14. Summary

MoonMind's Temporal architecture is now straightforward:

- one durable execution substrate
- one workflow lifecycle model
- one visibility/query model
- one action model built on workflow start, update, signal, cancel, and
  schedule primitives

The remaining complexity in MoonMind is product semantics and operator UX, not
parallel execution backends.
