# Queue Feature Audit Report

**Feature**: `095-queue-substrate-removal`
**Date**: 2026-03-21
**Purpose**: Document every `/api/queue/*` endpoint and its Temporal equivalent or deprecation status.

## Summary

The queue router (`agent_queue.py`, 2241 lines, ~30 endpoints) serves two distinct audiences:
1. **User-facing task submission** — already delegates to Temporal via `get_routing_target_for_task()`
2. **Worker-facing job lifecycle** — claim, heartbeat, complete, fail, recover, events — unused by Temporal workers

Temporal workers poll native task queues (`mm.workflow`, `mm.activity.*`) and never call queue API endpoints.

---

## Endpoint Audit

### Task Submission (User-facing)

| Endpoint | Method | Status | Temporal Equivalent |
|----------|--------|--------|-------------------|
| `/api/queue/jobs` | POST | ✅ **Already delegated** | `create_job()` calls `get_routing_target_for_task()` → `_create_execution_from_task_request()` / `_create_execution_from_manifest_request()` |
| `/api/queue/jobs/with-attachments` | POST | ✅ **Already delegated** | Routes through same path with attachment handling |

### Task List & Detail (User-facing)

| Endpoint | Method | Status | Temporal Equivalent |
|----------|--------|--------|-------------------|
| `/api/queue/jobs` | GET | ⚠️ **Redundant** | `GET /api/executions` provides Temporal-backed list |
| `/api/queue/jobs/{id}` | GET | ⚠️ **Redundant** | `GET /api/executions/{workflowId}` provides Temporal-backed detail |
| `/api/queue/jobs/{id}` | PATCH | ⚠️ **Redundant** | `POST /api/executions/{workflowId}/update` handles edits |
| `/api/queue/jobs/system/metadata` | GET | ⚠️ **Redundant** | System metadata can be derived from Temporal visibility |

### Task Control (User-facing)

| Endpoint | Method | Status | Temporal Equivalent |
|----------|--------|--------|-------------------|
| `/api/queue/jobs/{id}/cancel` | POST | ⚠️ **Redundant** | `POST /api/executions/{workflowId}/cancel` |
| `/api/queue/jobs/{id}/resubmit` | POST | ⚠️ **Redundant** | `POST /api/executions/{workflowId}/update` with rerun semantics |
| `/api/queue/jobs/{id}/control` | POST | ⚠️ **Redundant** | `POST /api/executions/{workflowId}/signal` |

### Worker Lifecycle (Worker-facing, internal)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/jobs/claim` | POST | 🔴 **Deprecated** | Temporal workers poll task queues directly |
| `/api/queue/jobs/{id}/heartbeat` | POST | 🔴 **Deprecated** | Temporal activities heartbeat natively |
| `/api/queue/jobs/{id}/complete` | POST | 🔴 **Deprecated** | Temporal workflows complete via activity return |
| `/api/queue/jobs/{id}/fail` | POST | 🔴 **Deprecated** | Temporal activities raise exceptions for failure |
| `/api/queue/jobs/{id}/recover` | POST | 🔴 **Deprecated** | Temporal has built-in retry + timeout policies |
| `/api/queue/jobs/{id}/cancel-ack` | POST | 🔴 **Deprecated** | Temporal cancellation is handled via workflow context |

### Events & Streaming (Worker/User-facing)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/jobs/{id}/events` | GET | ⚠️ **Redundant** | Dashboard polls Temporal execution APIs |
| `/api/queue/jobs/{id}/events` | POST | 🔴 **Deprecated** | Workers no longer post events; Temporal manages execution state |
| `/api/queue/jobs/{id}/events/stream` | GET (SSE) | ⚠️ **Redundant** | Dashboard uses polling via Temporal source config |

### Artifacts

| Endpoint | Method | Status | Temporal Equivalent |
|----------|--------|--------|-------------------|
| `/api/queue/jobs/{id}/artifacts` | GET | ⚠️ **Redundant** | Temporal artifact system (`/api/executions/{id}/artifacts`) |
| `/api/queue/jobs/{id}/artifacts` | POST | 🔴 **Deprecated** | Temporal artifacts use presigned upload to MinIO |
| `/api/queue/jobs/{id}/artifacts/{artifactId}` | GET | ⚠️ **Redundant** | Temporal artifact download via presigned URL |

### Live Sessions

| Endpoint | Method | Status | Temporal Equivalent |
|----------|--------|--------|-------------------|
| `/api/queue/jobs/{id}/live-session` | GET | ⚠️ **Redundant** | `/api/task-runs/{id}/live-session` serves Temporal tasks |
| `/api/queue/jobs/{id}/live-session/grant-write` | POST | ⚠️ **Redundant** | Same functionality at `/api/task-runs/{id}/live-session/grant-write` |
| `/api/queue/jobs/{id}/live-session/revoke` | POST | ⚠️ **Redundant** | Same functionality at `/api/task-runs/{id}/live-session/revoke` |

### Operator Messages

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/jobs/{id}/operator-messages` | POST | 🔶 **Deferred** | No Temporal equivalent yet. May be implemented via Temporal Signal or Update in future. FR-010 tracks this. |

### Worker Tokens (Internal)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/worker-tokens` | GET | 🔴 **Deprecated** | Temporal workers authenticate via Temporal namespace, not API tokens |
| `/api/queue/worker-tokens` | POST | 🔴 **Deprecated** | No new worker tokens needed for Temporal workers |
| `/api/queue/worker-tokens/{id}` | DELETE | 🔴 **Deprecated** | Token management no longer needed |

### Runtime Capabilities (Internal)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/capabilities/runtimes` | GET | ⚠️ **Redundant** | Runtime capabilities can be derived from Temporal worker registration |
| `/api/queue/capabilities/runtimes` | POST | 🔴 **Deprecated** | Workers register capabilities via Temporal task queue presence |

### Safeguards (Internal)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/safeguards` | GET | 🔴 **Deprecated** | Temporal has built-in timeout, retry, and heartbeat policies |

### Manifest Secret Resolution (Internal)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/jobs/{id}/manifest-secrets` | POST | ⚠️ **Redundant** | Manifest secret resolution should move to Temporal activity context |

### Migration Telemetry (Internal)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/queue/migration-telemetry` | GET | 🔴 **Deprecated** | Migration telemetry was transitional; Temporal is now the primary substrate |

---

## Status Legend

| Status | Meaning |
|--------|---------|
| ✅ **Already delegated** | Endpoint routes to Temporal; no queue-specific behavior |
| ⚠️ **Redundant** | Temporal equivalent exists; queue endpoint can be removed in Phase 2 |
| 🔴 **Deprecated** | No Temporal equivalent needed; endpoint is queue-internal infrastructure |
| 🔶 **Deferred** | No Temporal equivalent yet; tracked for future implementation |

## Gate Assessment

**Phase 1 Gate: "No user-facing action requires the queue path"**

✅ **GATE PASSED** — All user-facing actions (submit, list, detail, cancel, rerun, edit) have Temporal equivalents. The queue `create_job` endpoint already delegates to Temporal. The only deferred item is operator messages (FR-010), which is a non-blocking feature gap.
