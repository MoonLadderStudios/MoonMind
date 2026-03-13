# Task Cancellation

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  

## 1. Purpose

This document outlines **Task Cancellation** in MoonMind so that:

* Tasks can be cancelled while **queued** (pending execution).
* Tasks can be cancelled while **running natively as Temporal Workflows** (via Temporal Cancellation Requests).
* Cancellation is exposed through:
  * **Mission Control UI** (thin dashboard over REST)
  * **REST API endpoint(s)** (under `/api/queue`)
  * **MCP tool call** (under `/mcp/tools/call`)

---

## 2. Goals and Non-Goals

### Goals

1. **Queued cancellation**: user cancels a job → job transitions to `cancelled`.
2. **Running cancellation**: user requests cancellation for a running workflow → API sends a Temporal Cancellation Request, workflow detects request, stops execution gracefully, and job transitions to `cancelled`.
3. **Unified surfaces**: UI + REST + MCP all map to the **same API service methods**.
4. **Auditability**: cancellation actions are visible in workflow history and UI events.

### Non-Goals

* "Hard kill" guarantees for external CLIs that do not respect SIGINT/SIGTERM. We will do best-effort termination of subprocess Sandbox activities.

---

## 3. Architecture

MoonMind task runs are durably orchestrated by Temporal Workflows (e.g., `MoonMind.Run`). The cancellation flow mirrors standard Temporal patterns.

* Mission Control UI issues a cancel command to the Control Plane API (`POST /api/queue/jobs/{job_id}/cancel`).
* If the task is purely queued in the database and hasn't started a workflow, the API marks it `cancelled` in Postgres directly.
* If a Temporal Workflow Execution `MoonMind.Run` is currently active for this run, the API sends a standard **Temporal Cancellation Request** to the workflow via the Temporal Client.

### 3.1 Temporal Workflow Graceful Cancellation

* The `MoonMind.Run` workflow receives the Cancellation Request.
* The workflow must catch the resulting `CancelledError` (in the Python Temporal SDK).
* The workflow runs compensating actions (uploading incomplete staged artifacts, emitting a final `task.step.failed` event, cleaning up resources).
* The workflow exits.
* The API/UI observes the `CANCELED` status via the Temporal Visibility index or Webhooks and mirrors the `cancelled` state to the user.

---

## 4. REST API Surface

### Request cancellation (user action)

`POST /api/queue/jobs/{job_id}/cancel`

* Auth: `get_current_user()`
* Body: `{ "reason"?: string }`
* Behavior:
  * If the job is in Postgres `queued`: transition to `cancelled` immediately.
  * If a Temporal Workflow is running: Signal/Cancel the Temporal execution.
  * If the job is already terminal: idempotent no-op or 409 state conflict.

Response: updated `JobModel` with `status: cancelled` or `status: cancelling` (if waiting for Temporal tear-down).

---

## 5. Worker Runtime Cancellation (Cooperative)

Temporal Activities executing long-running steps (e.g., calling an LLM or running a CLI via Sandbox) must heartbeat to the Temporal Server if they wish to receive cancellation mid-flight.

1. **LLM Activities**: The activity periodically heartbeats. If the workflow was cancelled, the heartbeat raises `ActivityCancelledError`. The activity catches this, cleans up, and terminates.
2. **Sandbox CLI Activities**: Long-running CLI calls executed via `asyncio.create_subprocess_exec` must pass a cancellation token or trap the `ActivityCancelledError`. On cancellation:
   * Send `SIGINT` (graceful).
   * After a short grace period, send `SIGKILL`.
   * Ensure the child process group is terminated cleanly to prevent orphaned Docker/OS processes.
