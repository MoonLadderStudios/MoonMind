# Temporal Platform Foundation

**Project:** MoonMind
**Doc type:** System architecture / platform foundation
**Status:** Draft (implementation-oriented)
**Last updated:** 2026-03-05 (America/Los_Angeles)

---

## 1. Purpose

This document defines the **Temporal Platform** foundation for MoonMind: how we deploy, secure, observe, and operate Temporal as the **primary workflow manager and scheduling tool for migrated flows**, and what durable platform contracts MoonMind will rely on.

MoonMind aligns to Temporal’s core abstractions:

* **Workflow Execution / Workflow Type**
* **Activity / Activity Type**
* **Worker**
* **Task Queue** (routing plumbing only)
* **Namespace**
* **Visibility** (list/query/count), **Search Attributes**
* **Schedules** (preferred over cron-style systems)

---

## 2. Decisions and constraints

### Locked decisions (per project clarifications)

* **Deployment mode:** **purely self-hosted** (no Temporal Cloud).
* **Deployment runtime:** **Docker Compose** (required for MoonMind deployments).
* **Visibility store:** **PostgreSQL** (SQL-based advanced visibility).
* **Retention intent:** not compliance-driven; keep records indefinitely until Temporal storage usage reaches a configured cap.
* **Worker versioning policy:** **Auto-Upgrade** is the default behavior.
* **History shards:** target **1 shard by default** if feasible to keep things simple, with explicit acknowledgment of the immutability tradeoff.

---

## 3. Deployment model (self-hosted only)

### 3.1 Deployment profile

* Run a **self-hosted Temporal cluster** in a private network (no public exposure).
* Use PostgreSQL-backed persistence and PostgreSQL-backed visibility.
* Deploy and operate Temporal through Docker Compose in all MoonMind environments.

### 3.2 Runtime topology

Temporal Server is logically composed of multiple services (Frontend/History/Matching/Worker). In self-hosted operation, MoonMind deploys these services with Docker Compose and scales according to Compose service boundaries and host capacity.

---

## 4. Persistence and Visibility (PostgreSQL-first)

Temporal has two distinct storage concerns:

1. **Persistence store**: authoritative workflow state/history.
2. **Visibility store**: indexing + query/list/count.

MoonMind will use **PostgreSQL for both**, and specifically choose **PostgreSQL 12+** to support **Advanced Visibility** on SQL backends. Advanced Visibility supports custom SQL-like List Filters and custom Search Attributes and is available on PostgreSQL 12+ (and MySQL 8+, SQLite in local/test setups) with Temporal Server v1.20+. ([DeepWiki][1])

### 4.1 SQL-based visibility schema management

Because we are using **SQL-based visibility**, we must manage visibility schema upgrades as part of Temporal upgrades. Temporal release notes explicitly call out required visibility schema upgrades for SQL visibility (including PostgreSQL) on server upgrades. ([Temporal][2])

**MoonMind platform contract**

* Visibility schema upgrades are mandatory steps in Temporal server upgrade playbooks.
* Upgrades must be rehearsed in a pre-rollout validation environment before rollout.

---

## 5. Namespaces and retention (troubleshooting-first)

### 5.1 Namespaces

We will operate with:

* `moonmind`

### 5.2 Retention policy (non-compliance, storage-cap driven)

We want enough history to support:

* troubleshooting,
* evals,
* improvement proposal generation,
* “what happened?” investigations.

We will **not** keep sensitive/large payloads in Temporal history as a strategy; those go in MoonMind’s Artifact Store (separate doc). Retention is primarily about workflow execution history and visibility records.

**Retention contract**

* Retain records indefinitely by default (no time-based operational pruning target).
* Enforce a cluster storage cap through env var `TEMPORAL_RETENTION_MAX_STORAGE_GB`.
* Default `TEMPORAL_RETENTION_MAX_STORAGE_GB=100`.
* When usage reaches the cap, run automated pruning of the oldest closed execution records until usage drops below the cap.

**Operational note:** Some deployments and tooling default namespaces to a **3-day retention** if not explicitly managed (this commonly shows up in Helm-based setups), so retention management must be explicit and idempotent in deployment automation. ([Temporal Community Forum][3])

---

## 6. Visibility contract for MoonMind

For **Temporal-managed work**, Temporal Visibility is the list/query/count source of truth.

During migration, MoonMind may still expose unified task-oriented surfaces that combine:

* queue-backed tasks
* system-backed tasks
* Temporal-backed executions

The platform contract here is narrower: Temporal-managed records should be listed from **Temporal Visibility**, not from mirrored Postgres dashboard tables.

### 6.1 Search Attributes

Because we’re using SQL Advanced Visibility (Postgres 12+), we rely on:

* custom Search Attributes for filtering and UI views,
* SQL-like list filters for querying. ([DeepWiki][1])

**Foundation contract**

* A dedicated “Visibility & UI Query Model” doc will define:

  * required Search Attributes,
  * allowed filters,
  * paging token handling,
  * count strategy.

---

## 7. Task Queues (routing only; not a product queue)

Temporal Task Queues are required plumbing for dispatching work to workers, but **MoonMind does not treat them as user-visible queues**. They are purely:

* routing labels,
* worker-fleet segmentation boundaries.

### 7.1 Naming convention (proposal)

Use a small stable set of queues:

* Workflow tasks:

  * `mm.workflow`

* Activity tasks (capability-based):

  * `mm.activity.llm`
  * `mm.activity.sandbox`
  * `mm.activity.integrations`
  * `mm.activity.artifacts`

Optional lanes (only if needed): `:high`, `:normal`, `:low`

---

## 8. Worker fleet strategy and versioning (Auto-Upgrade default)

### 8.1 Worker segmentation

Workers are separated by:

* secrets needed (LLM keys vs GitHub/Jules tokens),
* isolation level (sandbox execution),
* cost profile (LLM vs non-LLM).

### 8.2 Auto-Upgrade default

We will use **Auto-Upgrade** as the default versioning behavior. In Temporal’s worker versioning model, `AutoUpgrade` and `Pinned` are versioning behaviors; Auto-Upgrade is the path that allows existing workflows to move to new versions when you roll a new worker deployment/version (with the usual compatibility discipline). ([GitHub][4])

**Foundation contract**

* Default: **Auto-Upgrade**
* Exception: workflow types that must never change mid-flight can opt into pinned behavior (rare; separate workflow doc defines when).

---

## 9. History shards (simplicity-first, with hard constraint)

### 9.1 Default shard count

We will target **`numHistoryShards = 1`** *only if feasible for MoonMind’s expected scale* and we accept the limitations. This is a “keep it simple” posture for early rollout.

### 9.2 Immutability constraint (critical)

Changing `numHistoryShards` after a cluster is provisioned is effectively a **cluster migration problem** (stand up a new cluster and migrate / cut over). Temporal community guidance is explicit that you can’t simply modify shard count in-place; you’d need to stand up a new cluster if you want a different shard count. ([Temporal Community Forum][5])

**Foundation contract**

* Shard count is treated as a pre-rollout gate decision.
* If we launch with 1 shard, scaling beyond that is not “a config tweak”; it is a migration project.

---

## 10. Scheduling foundation (Temporal Schedules)

MoonMind will standardize on Temporal-native scheduling mechanisms (Schedules) for periodic triggers, sweepers, and recurring automation **once a flow is Temporal-managed**.

**Foundation contract**

* No cron/beat-style external schedulers for Temporal-driven workflows.
* Scheduling definitions are managed as Temporal Schedule objects and controlled via Temporal CLI/automation.

The legacy DB-polling `moonmind-scheduler` has been removed. Temporal Schedules and Timers are now the authoritative mechanism for all recurring and time-based workflow starts.

(Details live in a separate "Scheduling" system doc.)

---

## 11. Security foundation (self-hosted posture)

### 11.1 Network isolation

* Temporal endpoints are not exposed publicly.
* Access restricted to internal network / private cluster connectivity.

### 11.2 AuthN/Z expectations

* MoonMind API is the primary client of Temporal in runtime operation.
* Worker processes authenticate as trusted internal clients.
* We will not operate Temporal as an open service reachable by untrusted clients.

(Exact TLS/mTLS and authorization configuration is deferred to a dedicated “Security” system doc.)

---

## 12. Observability foundation

**Minimum requirements**

* Server metrics scraped and graphed (Temporal service health, matching backlog, history pressure).
* Worker metrics and logs correlated by:

  * Workflow ID
  * Run ID
  * Workflow Type
  * Task Queue
* Cluster alerting covers:

  * worker “no pollers” conditions,
  * activity retry storms,
  * visibility query failures.

Worker versioning and deploy safety improvements are a major motivation for worker versioning; we’ll treat it as an operational capability and monitor it. ([Temporal][6])

---

## 13. Upgrade strategy (SQL visibility-aware)

**Upgrade contract**

* Pre-rollout rehearsal is required before upgrades.
* Because we use SQL-based visibility, visibility schema upgrades are part of upgrade sequencing. ([Temporal][2])

**Practical guidance**

* Maintain explicit versions for:

  * Temporal server
  * temporal CLI/admin tools
  * SQL visibility schemas (Postgres)

---

## 14. Platform acceptance criteria

The Temporal Platform Foundation is “done” when:

1. A self-hosted Temporal cluster exists with private connectivity.
2. Postgres persistence + **Postgres SQL visibility** configured and validated:

   * list/filter works with custom Search Attributes (advanced visibility). ([DeepWiki][1])
3. Namespace retention management is explicit and automated:

   * namespace `moonmind` is managed with a storage-cap policy (`TEMPORAL_RETENTION_MAX_STORAGE_GB`, default `100`) and idempotent retention automation. ([Temporal Community Forum][3])
4. Worker fleets deployed (workflow + activity fleets) with clear task queue routing.
5. Worker versioning policy set: **Auto-Upgrade default**. ([GitHub][4])
6. Shard count decision recorded and signed off; if 1 shard is chosen, the migration implications are acknowledged. ([Temporal Community Forum][5])
7. SQL visibility schema upgrade path rehearsed in pre-rollout validation. ([Temporal][2])
