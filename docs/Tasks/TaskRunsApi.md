# Task Runs API

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-04-02

## 1. Purpose

Define the REST API surfaces used to create, monitor, and observe MoonMind task runs in the Temporal-first architecture.

MoonMind now splits this responsibility across:

- **`/api/executions`** for Temporal-backed execution lifecycle operations
- **`/api/task-runs`** for managed-run observability (logs, diagnostics, live follow)

Mission Control still presents these executions as **tasks** in the product UI, but the active lifecycle API is execution-oriented.

## 2. API Surface

Task runs are served by two active router families:

- **`/api/executions`** — Execution lifecycle for Temporal-backed work.
- **`/api/task-runs`** — Artifact-backed managed-run observability.

### 2.1 Execution Lifecycle (`/api/executions`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/executions` | Create/start a Temporal-backed execution |
| `GET`  | `/api/executions` | List executions visible to the caller |
| `GET`  | `/api/executions/{workflowId}` | Get execution detail |
| `POST` | `/api/executions/{workflowId}/update` | Apply a workflow update such as input edits or rerun requests |
| `POST` | `/api/executions/{workflowId}/signal` | Send an asynchronous workflow signal such as pause, resume, or approve |
| `POST` | `/api/executions/{workflowId}/cancel` | Cancel or terminate an execution |

### 2.2 Auxiliary Execution Routes (`/api/executions`)

These routes extend the main lifecycle surface for specific execution types:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/executions/{workflowId}/manifest-status` | Fetch manifest-run status summary |
| `GET` | `/api/executions/{workflowId}/manifest-nodes` | Page manifest node state |
| `POST` | `/api/executions/{workflowId}/integration` | Register/update integration monitoring state |
| `POST` | `/api/executions/{workflowId}/integration/poll` | Record integration poll results |
| `POST` | `/api/executions/{workflowId}/reschedule` | Change the scheduled time of a scheduled execution |
| `POST` | `/api/executions/{workflowId}/rerun` | Create a fresh execution with the original parameters |

### 2.3 Managed-Run Observability (`/api/task-runs`)

These endpoints expose artifact-backed observability for managed runs. The legacy `/live-session*` family is not part of the active API.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/task-runs/{taskRunId}/observability-summary` | Get observability metadata and artifact refs |
| `GET` | `/api/task-runs/{taskRunId}/logs/stream` | Stream active live logs over SSE when supported |
| `GET` | `/api/task-runs/{taskRunId}/logs/stdout` | Read the stdout log artifact |
| `GET` | `/api/task-runs/{taskRunId}/logs/stderr` | Read the stderr log artifact |
| `GET` | `/api/task-runs/{taskRunId}/logs/merged` | Read the merged log view or synthesized fallback |
| `GET` | `/api/task-runs/{taskRunId}/diagnostics` | Read the diagnostics artifact |

## 3. Identity Model

MoonMind currently uses three related identifiers around task runs:

- **`workflowId`** — the canonical durable execution identifier for `/api/executions`
- **`taskId`** — the task-oriented product identifier; for Temporal-backed work, `taskId == workflowId`
- **`taskRunId`** — the managed-run observability record identifier used by `/api/task-runs`

The normal control-plane flow is:

1. Create or list work through `/api/executions`
2. Use `workflowId` for lifecycle actions and detail fetches
3. Read `taskRunId` from execution detail when managed-run observability is available
4. Use `/api/task-runs/{taskRunId}` for logs, diagnostics, and live follow

## 4. Observability Behavior

These routes are artifact-first:

- `observability-summary` reports whether live follow is appropriate for the current run.
- `logs/stream` is the SSE live-follow endpoint for active runs only.
- `logs/stdout`, `logs/stderr`, and `logs/merged` remain available for historical and terminal runs.
- `diagnostics` exposes the persisted supervision payload for postmortem inspection.

Full log bodies and diagnostics come from managed-run artifact storage and spool files, not from workflow history or raw Temporal event history.

## 5. Request Model Posture

`POST /api/executions` is the active create surface. It accepts the execution-oriented request model and, during migration, may also normalize task-shaped compatibility payloads into the same Temporal-backed execution contract.

Execution requests ultimately dispatch into `MoonMind.Run` or another allowed workflow type, then fan out across Temporal worker fleets grouped by capability and security boundary:

| Fleet | Task Queue | Capabilities | Purpose |
|-------|-----------|--------------|---------|
| `workflow` | `mm.workflow` | Workflow orchestration | Workflow code only; no side effects |
| `artifacts` | `mm.activity.artifacts` | Artifact I/O, auth profiles | IO-bound artifact storage and metadata |
| `llm` | `mm.activity.llm` | LLM calls, plan generation, proposals | Rate-limited by provider quotas |
| `sandbox` | `mm.activity.sandbox` | Repo checkout, patch, tests, commands | CPU/memory heavy; strict concurrency limits |
| `integrations` | `mm.activity.integrations` | External provider calls (Jules) | Protected with rate limiting and circuit breakers |
| `agent_runtime` | `mm.activity.agent_runtime` | Supervised agent execution, artifact publish | Long-lived runtime executions |

The `activity_catalog.py` module defines the full routing contract including per-activity timeout and retry policies.

## 6. Legacy Queue Posture

The legacy `/api/queue/jobs` lifecycle routes and `/api/queue` worker callback routes are historical migration references only. They are not the active task-run lifecycle API, and the execution router explicitly rejects falling back to the old queue substrate when Temporal submission is disabled.

## 7. Related Documentation

- [../Api/ExecutionsApiContract.md](../Api/ExecutionsApiContract.md) — Direct execution lifecycle contract
- [TaskArchitecture.md](TaskArchitecture.md) — Overall task system design
- [../UI/MissionControlArchitecture.md](../UI/MissionControlArchitecture.md) — Task-oriented UI over execution APIs
- [TaskProposalSystem.md](TaskProposalSystem.md) — Task proposal generation
- [TaskCancellation.md](TaskCancellation.md) — Cancellation flow
- [../ManagedAgents/LiveLogs.md](../ManagedAgents/LiveLogs.md) — Managed-run log and observability design
- [../Temporal/TemporalArchitecture.md](../Temporal/TemporalArchitecture.md) — Temporal infrastructure
