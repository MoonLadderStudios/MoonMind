# Temporal Platform Foundation

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-TemporalPlatformFoundation.md`](../tmp/remaining-work/Temporal-TemporalPlatformFoundation.md)

**Project:** MoonMind
**Doc type:** System architecture / platform foundation
**Status:** Draft (implementation-oriented)
**Last updated:** 2026-03-30

---

## 1. Purpose

This document defines the **Temporal Platform** foundation for MoonMind: how we deploy, secure, observe, and operate Temporal as the **primary workflow manager and scheduling backbone for the live Temporal-backed core architecture**, and what durable platform contracts MoonMind relies on.

MoonMind aligns to Temporal's core abstractions:

* **Workflow Execution / Workflow Type**
* **Activity / Activity Type**
* **Worker**
* **Task Queue** (routing plumbing only)
* **Namespace**
* **Visibility** (list/query/count), **Search Attributes**
* **Schedules** (preferred over cron-style systems)

---

## 2. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/TaskExecutionCompatibilityModel.md`

These docs define the normative contracts that build on this platform foundation.

---

## 3. Decisions and constraints

### Locked decisions (per project clarifications)

* **Deployment mode:** **purely self-hosted** (no Temporal Cloud).
* **Deployment runtime:** **Docker Compose** (required for MoonMind deployments).
* **Visibility store:** **PostgreSQL** (SQL-based advanced visibility).
* **Retention intent:** not compliance-driven; keep records indefinitely until Temporal storage usage reaches a configured cap.
* **Worker versioning policy:** **Auto-Upgrade** is the default behavior.
* **History shards:** target **1 shard by default** if feasible to keep things simple, with explicit acknowledgment of the immutability tradeoff.

---

## 4. Deployment model (self-hosted only)

### 4.1 Deployment profile

* Run a **self-hosted Temporal cluster** in a private network (no public exposure).
* Use PostgreSQL-backed persistence and PostgreSQL-backed visibility.
* Deploy and operate Temporal through Docker Compose in all MoonMind environments.

### 4.2 Runtime topology

Temporal Server is logically composed of multiple services (Frontend/History/Matching/Worker). In self-hosted operation, MoonMind deploys these services with Docker Compose and scales according to Compose service boundaries and host capacity.

---

## 5. Core workflow catalog

The following workflow types constitute the live Temporal application layer at the platform foundation level:

| Workflow type | Purpose |
| --- | --- |
| `MoonMind.Run` | General root execution workflow for plan-driven orchestration |
| `MoonMind.ManifestIngest` | Fan-out/fan-in manifest ingestion |
| `MoonMind.AgentRun` | Durable lifecycle wrapper for true agent execution (managed and external) |
| `MoonMind.ProviderProfileManager` | Long-running provider-profile coordination for managed runtimes |
| `MoonMind.OAuthSession` | OAuth dance lifecycle |

All true agent execution steps are dispatched as `MoonMind.AgentRun` child workflows from `MoonMind.Run`. They are not plain activity invocations.

---

## 6. Persistence and Visibility (PostgreSQL-first)

Temporal has two distinct storage concerns:

1. **Persistence store**: authoritative workflow state/history.
2. **Visibility store**: indexing + query/list/count.

MoonMind uses **PostgreSQL for both**, and specifically chooses **PostgreSQL 12+** to support **Advanced Visibility** on SQL backends.

### 6.1 SQL-based visibility schema management

Because we are using **SQL-based visibility**, we must manage visibility schema upgrades as part of Temporal upgrades.

**MoonMind platform contract**

* Visibility schema upgrades are mandatory steps in Temporal server upgrade playbooks.
* Upgrades must be rehearsed in a pre-rollout validation environment before rollout.

---

## 7. Namespaces and retention (troubleshooting-first)

### 7.1 Namespaces

We will operate with:

* `default` (local default)
* shared/enterprise deployments should use a dedicated namespace (e.g., `moonmind`)

### 7.2 Retention policy (non-compliance, storage-cap driven)

We want enough history to support:

* troubleshooting,
* evals,
* improvement proposal generation,
* "what happened?" investigations.

We will **not** keep sensitive/large payloads in Temporal history as a strategy; those go in MoonMind's Artifact Store (separate doc). Retention is primarily about workflow execution history and visibility records.

**Retention contract**

* Retain records indefinitely by default (no time-based operational pruning target).
* Enforce a cluster storage cap through env var `TEMPORAL_RETENTION_MAX_STORAGE_GB`.
* Default `TEMPORAL_RETENTION_MAX_STORAGE_GB=100`.
* When usage reaches the cap, run automated pruning of the oldest closed execution records until usage drops below the cap.

---

## 8. Visibility contract for MoonMind

For **Temporal-managed work**, **Temporal Visibility is the authoritative query plane** for list, filter, query, and count semantics.

App DB projection rows may continue to exist during migration, but they are **adapters and caches**, not the semantic owner of query behavior. If a projection and Temporal-backed canonical metadata drift, the system should repair the projection rather than redefining the query contract around projection drift.

### 8.1 Search Attributes

Because we're using SQL Advanced Visibility (Postgres 12+), we rely on:

* custom Search Attributes for filtering and UI views,
* SQL-like list filters for querying.

### 8.2 Required Search Attributes

| Name | Type | Lifecycle owner | Notes |
| --- | --- | --- | --- |
| `mm_owner_type` | keyword | API/workflow start path | `user`, `system`, or `service` |
| `mm_owner_id` | keyword | API/workflow start path | Principal identifier |
| `mm_state` | keyword | Workflow lifecycle logic | Exact MoonMind lifecycle state |
| `mm_updated_at` | datetime | Workflow lifecycle logic | Default recency/sort key |
| `mm_entry` | keyword | Workflow start path | Execution category for UI/query surfaces |

### 8.3 Foundation contract

The full query model — allowed filters, paging token handling, count strategy, and compatibility mapping — is defined in:

- `docs/Temporal/VisibilityAndUiQueryModel.md`

This platform foundation doc locks the Search Attribute names and types. The query model doc locks their operational semantics.

---

## 9. Task Queues (routing only; not a product queue)

Temporal Task Queues are required plumbing for dispatching work to workers, but **MoonMind does not treat them as user-visible queues**. They are purely:

* routing labels,
* worker-fleet segmentation boundaries.

### 9.1 Canonical queue set

Use a small stable set of queues:

* Workflow tasks:

  * `mm.workflow`

* Activity tasks (capability-based):

  * `mm.activity.llm`
  * `mm.activity.sandbox`
  * `mm.activity.integrations`
  * `mm.activity.artifacts`
  * `mm.activity.agent_runtime`

`mm.activity.agent_runtime` is canonical, not optional. It is the queue for the `agent_runtime` worker fleet that handles managed runtime execution activities.

Optional lanes (only if needed): `:high`, `:normal`, `:low`

---

## 10. Worker fleet strategy and versioning (Auto-Upgrade default)

### 10.1 Worker segmentation

Workers are separated by:

* secrets needed (LLM keys vs GitHub/Jules tokens),
* isolation level (sandbox execution),
* cost profile (LLM vs non-LLM).

### 10.2 Workflow fleet note

The workflow fleet primarily runs deterministic workflow code. However, it may contain a narrow set of helper activities such as lightweight memo updates or search-attribute maintenance that do not justify a separate fleet. This is an acknowledged exception to pure workflow-only fleet posture.

### 10.3 Auto-Upgrade default

We will use **Auto-Upgrade** as the default versioning behavior.

**Foundation contract**

* Default: **Auto-Upgrade**
* Exception: workflow types that must never change mid-flight can opt into pinned behavior (rare; separate workflow doc defines when).

---

## 11. Canonical runtime contract discipline

True agent-runtime activities must return canonical contracts directly. Workflow code must not depend on provider-shaped payloads.

The canonical contracts are:

* `AgentRunHandle`
* `AgentRunStatus`
* `AgentRunResult`

Large execution outputs, diagnostics, and runtime/provider payloads must stay in artifact storage and be referenced by compact `ArtifactRef` values in activity results. Workflow history must remain small, bounded, and safe.

This rule applies to both managed and external (provider-backed) agent execution paths.

---

## 12. Provider-profile coordination

Provider-profile coordination is a first-class platform concern for managed runtimes.

`MoonMind.ProviderProfileManager` runs as a long-lived workflow that manages profile lifecycle, slot availability, and health monitoring for managed runtime providers.

Managed runtime execution depends on provider-profile availability. The platform must ensure that `MoonMind.ProviderProfileManager` workflows are running and healthy before managed runtime slots can be claimed.

---

## 13. History shards (simplicity-first, with hard constraint)

### 13.1 Default shard count

We will target **`numHistoryShards = 1`** *only if feasible for MoonMind's expected scale* and we accept the limitations. This is a "keep it simple" posture for early rollout.

### 13.2 Immutability constraint (critical)

Changing `numHistoryShards` after a cluster is provisioned is effectively a **cluster migration problem** (stand up a new cluster and migrate / cut over).

**Foundation contract**

* Shard count is treated as a pre-rollout gate decision.
* If we launch with 1 shard, scaling beyond that is not "a config tweak"; it is a migration project.

---

## 14. Scheduling foundation (Temporal Schedules)

MoonMind standardizes on Temporal-native scheduling mechanisms (Schedules) for periodic triggers, sweepers, and recurring automation for Temporal-managed workflows.

**Foundation contract**

* No cron/beat-style external schedulers for Temporal-driven workflows.
* Scheduling definitions are managed as Temporal Schedule objects and controlled via Temporal CLI/automation.

The legacy DB-polling `moonmind-scheduler` has been removed. Temporal Schedules and Timers are now the authoritative mechanism for all recurring and time-based workflow starts.

(Details live in `docs/Temporal/WorkflowSchedulingGuide.md`.)

---

## 15. Security foundation (self-hosted posture)

### 15.1 Network isolation

* Temporal endpoints are not exposed publicly.
* Access restricted to internal network / private cluster connectivity.

### 15.2 AuthN/Z expectations

* MoonMind API is the primary client of Temporal in runtime operation.
* Worker processes authenticate as trusted internal clients.
* We will not operate Temporal as an open service reachable by untrusted clients.

(Exact TLS/mTLS and authorization configuration is deferred to a dedicated "Security" system doc.)

---

## 16. Observability foundation

**Minimum requirements**

* Server metrics scraped and graphed (Temporal service health, matching backlog, history pressure).
* Worker metrics and logs correlated by:

  * Workflow ID
  * Run ID
  * Workflow Type
  * Task Queue
* Cluster alerting covers:

  * worker "no pollers" conditions,
  * activity retry storms,
  * visibility query failures.

---

## 17. Upgrade strategy (SQL visibility-aware)

**Upgrade contract**

* Pre-rollout rehearsal is required before upgrades.
* Because we use SQL-based visibility, visibility schema upgrades are part of upgrade sequencing.

**Practical guidance**

* Maintain explicit versions for:

  * Temporal server
  * temporal CLI/admin tools
  * SQL visibility schemas (Postgres)

---

## 18. Platform acceptance criteria

The Temporal Platform Foundation is "done" when:

1. A self-hosted Temporal cluster exists with private connectivity.
2. Postgres persistence + **Postgres SQL visibility** configured and validated:

   * list/filter works with custom Search Attributes (advanced visibility).
3. Namespace retention management is explicit and automated:

   * custom namespaces (e.g. `moonmind`) are managed with a storage-cap policy (`TEMPORAL_RETENTION_MAX_STORAGE_GB`, default `100`) and idempotent retention automation.
4. Worker fleets deployed (workflow + activity fleets including `agent_runtime`) with clear task queue routing.
5. Worker versioning policy set: **Auto-Upgrade default**.
6. Shard count decision recorded and signed off; if 1 shard is chosen, the migration implications are acknowledged.
7. SQL visibility schema upgrade path rehearsed in pre-rollout validation.
8. Core workflow catalog (`MoonMind.Run`, `MoonMind.AgentRun`, `MoonMind.ManifestIngest`, `MoonMind.ProviderProfileManager`, `MoonMind.OAuthSession`) registered and schedulable.
9. Canonical Search Attributes (`mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`) registered and verified in Visibility queries.
10. Provider-profile coordination workflows running for managed runtimes that require them.
