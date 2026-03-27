# Temporal Platform Foundation

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-TemporalPlatformFoundation.md`](../tmp/remaining-work/Temporal-TemporalPlatformFoundation.md)  
**Project:** MoonMind  
**Doc type:** System architecture / platform foundation  
**Status:** Active  
**Last updated:** 2026-03-27

---

## 1. Purpose

This document defines the platform-level foundation MoonMind relies on for
Temporal: deployment, persistence, visibility, namespaces, task queues,
scheduling, versioning, and observability.

MoonMind uses Temporal as the platform for **all live task execution**.

---

## 2. Locked decisions

- **Deployment mode:** self-hosted only
- **Deployment runtime:** Docker Compose
- **Persistence store:** PostgreSQL
- **Visibility store:** PostgreSQL advanced visibility
- **Artifact backend:** MinIO / S3-compatible object storage
- **Worker versioning default:** Auto-Upgrade
- **Task Queue posture:** routing only, not product semantics
- **Default history shard posture:** simplicity-first, explicit decision

---

## 3. Deployment model

### 3.1 Runtime profile

- Temporal runs on a private Docker network.
- MoonMind API and workers are the primary Temporal clients.
- Temporal is not exposed as a public multi-tenant service.

### 3.2 Baseline topology

The platform foundation includes:

- Temporal server
- Temporal namespace/bootstrap init
- workflow and activity workers
- shared Postgres for MoonMind + Temporal
- MinIO for artifact bytes
- optional Temporal UI and admin tooling

---

## 4. Persistence and visibility

Temporal has two distinct storage concerns:

1. **Persistence store** for workflow state and history
2. **Visibility store** for indexed list/query/count behavior

MoonMind uses PostgreSQL for both.

Platform contract:

- visibility schema upgrades are part of Temporal upgrade playbooks
- upgrades must be rehearsed before rollout
- custom Search Attributes are part of platform bootstrap, not ad hoc manual
  state

---

## 5. Namespaces and retention

### 5.1 Namespace posture

- local default namespace: `default`
- shared deployments may choose a dedicated namespace such as `moonmind`

### 5.2 Retention posture

Retention exists for troubleshooting, run-history inspection, and operator
observability.

Platform contract:

- retain execution history according to explicit namespace automation
- manage retention as an operator-controlled platform policy
- keep large prompts, logs, and generated files out of workflow history by using
  the artifact system instead

---

## 6. Visibility contract

For Temporal-managed work, **Temporal Visibility is the source of truth for
list/filter/count behavior**.

MoonMind may still maintain app-local projections for compatibility, enrichment,
and degraded-mode reads, but those projections do not replace Visibility as the
runtime query plane.

The platform foundation requires:

- registered custom Search Attributes
- stable list/filter behavior over approved attributes
- explicit count semantics in API contracts

---

## 7. Task Queues

Temporal Task Queues are plumbing for worker routing, not user-facing queues.

Default queue set:

- `mm.workflow`
- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.agent_runtime`
- `mm.activity.integrations`

Add sub-queues only when isolation, scaling, or secret-boundary requirements
justify them.

---

## 8. Worker fleet strategy

Workers are segmented by:

- capability
- security boundary
- secret ownership
- operational scaling needs

Default worker versioning behavior is **Auto-Upgrade**. Exceptions should be
rare and documented at the workflow-contract level.

---

## 9. History shard posture

MoonMind favors a simplicity-first shard count for self-hosted deployments, with
explicit acknowledgment that shard-count changes are migration events rather
than routine config changes.

Platform contract:

- record the chosen shard count as an operational decision
- treat post-launch shard-count changes as explicit migration work

---

## 10. Scheduling foundation

MoonMind standardizes on Temporal-native time controls:

- workflow start without delay for immediate work
- `start_delay` for deferred one-time starts
- workflow timers for reschedulable waiting
- Temporal Schedules for recurring execution

No platform contract should imply a separate scheduler daemon or queue-based
dispatch path for live execution.

Control-plane corollary:

- acknowledged execution mutations use Updates or cancellation endpoints
- asynchronous external events use Signals
- mutable deferred waits use the dedicated `reschedule` signal path

---

## 11. Security foundation

- Temporal endpoints remain private-network only
- MoonMind API is the primary control-plane client
- workers authenticate as trusted internal clients
- artifact access remains brokered by MoonMind authorization rules, not direct
  public Temporal exposure

---

## 12. Observability foundation

Minimum platform observability includes:

- Temporal service health and backlog metrics
- worker health and polling visibility
- logs and metrics correlated by workflow ID, run ID, workflow type, and task
  queue
- alerting for poller loss, retry storms, and visibility failures

---

## 13. Acceptance criteria

The Temporal platform foundation is established when:

1. a private self-hosted Temporal deployment is running under Docker Compose
2. Postgres persistence and advanced visibility are configured and validated
3. namespace bootstrap and search-attribute registration are automated
4. workflow and activity fleets are deployed with clear routing boundaries
5. worker versioning policy is documented and enforced
6. scheduling behavior uses Temporal-native primitives
7. visibility/schema upgrade procedures are part of rollout playbooks
