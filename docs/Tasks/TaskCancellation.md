# Task Cancellation

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27

## 1. Purpose

This document defines task cancellation in MoonMind so that:

- tasks can be cancelled while **scheduled**
- tasks can be cancelled while **running as Temporal workflows**
- cancellation is exposed consistently through:
  - Mission Control UI
  - REST APIs
  - MCP/tooling layers when applicable

---

## 2. Goals

1. A user can request cancellation from the normal task UI.
2. The API translates that request into a Temporal cancellation request.
3. The workflow exits gracefully and releases shared resources.
4. Cancellation is visible in execution history, projections, and UI summaries.
5. Cleanup covers auth profile slots, live sessions, and other leased resources.

---

## 3. Architecture

MoonMind task runs are durably orchestrated by Temporal workflows such as
`MoonMind.Run`.

Cancellation flow:

1. Mission Control calls `POST /api/executions/{workflowId}/cancel`.
2. The API authorizes the caller and submits a Temporal cancellation request.
3. The workflow observes cancellation, runs cleanup/finalization logic, and
   exits.
4. The API/UI observe the resulting `canceled` terminal state via Temporal and
   the execution projection.

### 3.1 Workflow behavior

- the workflow should honor cancellation cooperatively
- finalization should still emit finish summaries and cleanup side effects when
  safe
- cancellation must release provider profile slots and end live-session state

---

## 4. REST API Surface

### User-facing cancellation

`POST /api/executions/{workflowId}/cancel`

Auth:

- standard authenticated user flow via the control plane

Body:

```json
{
  "reason": "optional operator note",
  "graceful": true
}
```

Behavior:

- if the execution is active, send Temporal cancellation
- if the execution is already terminal, return an idempotent no-op or
  state-appropriate rejection
- default to graceful cancellation in product flows

Response:

- updated execution payload with `state: "canceled"` when already resolved, or
- transitional execution state while cancellation is still being observed

---

## 5. Activity Cancellation

Long-running Activities should be cancellation-aware.

1. **LLM activities** should heartbeat so cancellation is observed promptly.
2. **Sandbox/CLI activities** should terminate subprocesses cooperatively first,
   then force-stop if needed.
3. **Integration activities** should stop polling loops and persist a useful
   final summary.

---

## 6. Provider Profile Slot Cleanup

Managed agent runs acquire provider profile slots before launch. Cancellation
must not leak those slots.

Defense-in-depth layers:

1. **Workflow-initiated release** on normal cancellation/failure paths
2. **Manager-side lease eviction** for expired or abandoned leases
3. **Manager-side active probing** so terminal workflows do not retain slots

Invariant:

At steady state, every leased provider profile slot must correspond to a running
managed execution.
