# Workflow Runs API

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-04-04

## 1. Purpose

Define the REST API surfaces used to create, monitor, and observe MoonMind Workflow runs in the Temporal-first architecture.

MoonMind now splits this responsibility across:

- **`/api/executions`** for Temporal-backed execution lifecycle operations
- **`/api/agent-runs`** for managed-run observability (logs, diagnostics, live follow)

Mission Control still presents these executions as **Workflows** in the product UI, but the active lifecycle API is execution-oriented.

The public `/api/agent-runs` path comes from the `agent_runs` router's `prefix="/agent-runs"`, which FastAPI mounts under the app-level `/api` prefix.

## 2. API Surface

Workflow runs are served by two active router families:

- **`/api/executions`** — Execution lifecycle for Temporal-backed work.
- **`/api/agent-runs`** — Artifact-backed managed-run observability.

### 2.1 Execution Lifecycle (`/api/executions`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/executions` | Create/start a Temporal-backed execution |
| `GET`  | `/api/executions` | List executions visible to the caller |
| `GET`  | `/api/executions/{workflowId}` | Get execution detail |
| `POST` | `/api/executions/{workflowId}/update` | Apply an in-place workflow update such as `UpdateInputs`, `SetTitle`, or `RequestRerun` (Continue-As-New on the same logical execution) |
| `POST` | `/api/executions/{workflowId}/signal` | Send an asynchronous workflow signal such as pause, resume, or approve |
| `POST` | `/api/executions/{workflowId}/cancel` | Cancel or terminate an execution |

### 2.2 Auxiliary Execution Routes (`/api/executions`)

These routes extend the main lifecycle surface for specific execution types:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/executions/{workflowId}/manifest-status` | Fetch manifest-run status summary |
| `GET` | `/api/executions/{workflowId}/manifest-nodes` | Page manifest node state |
| `GET` | `/api/executions/{workflowId}/steps` | Fetch the latest/current run step ledger |
| `POST` | `/api/executions/{workflowId}/integration` | Register/update integration monitoring state |
| `POST` | `/api/executions/{workflowId}/integration/poll` | Record integration poll results |
| `POST` | `/api/executions/{workflowId}/reschedule` | Change the scheduled time of a scheduled execution |
| `POST` | `/api/executions/{workflowId}/rerun` | Create a fresh execution with the original parameters and a new `workflowId` |

### 2.3 Managed-Run Observability (`/api/agent-runs`)

These endpoints expose artifact-backed observability for managed runs. The legacy `/live-session*` family is not part of the active API.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/agent-runs/{agentRunId}/observability-summary` | Get observability metadata and artifact refs |
| `GET` | `/api/agent-runs/{agentRunId}/logs/stream` | Stream active live logs over SSE when supported |
| `GET` | `/api/agent-runs/{agentRunId}/logs/stdout` | Read the stdout log artifact |
| `GET` | `/api/agent-runs/{agentRunId}/logs/stderr` | Read the stderr log artifact |
| `GET` | `/api/agent-runs/{agentRunId}/logs/merged` | Read the merged log view or synthesized fallback |
| `GET` | `/api/agent-runs/{agentRunId}/diagnostics` | Read the diagnostics artifact |

## 3. Identity Model

MoonMind currently uses three related identifiers around Workflow runs:

- **`workflowId`** — the canonical durable execution identifier for `/api/executions`
- **`taskId`** — the legacy product identifier (renames in the hard switch); for Temporal-backed work, `taskId == workflowId`
- **`agentRunId`** — the managed-run observability record identifier used by `/api/agent-runs` (the wire key keeps its legacy name until the MoonMind.UserWorkflow v2 cutover); it may appear on top-level execution detail and on individual step rows

The normal control-plane flow is:

1. Create or list work through `/api/executions`
2. Use `workflowId` for lifecycle actions and detail fetches
3. Read the step ledger from `/api/executions/{workflowId}/steps`
4. Resolve the relevant step's `agentRunId` when managed-run observability is available
5. Use `/api/agent-runs/{agentRunId}` for logs, diagnostics, and live follow

## 4. Observability Behavior

These routes are artifact-first:

- `observability-summary` reports whether live follow is appropriate for the current run.
- `logs/stream` is the SSE live-follow endpoint for active runs only.
- `logs/stdout`, `logs/stderr`, and `logs/merged` remain available for historical and terminal runs.
- `diagnostics` exposes the persisted supervision payload for postmortem inspection.

Full log bodies and diagnostics come from managed-run artifact storage and spool files, not from workflow history or raw Temporal event history.

## 5. Request Model Posture

`POST /api/executions` is the active create surface. It accepts the execution-oriented request model and, during migration, may also normalize legacy `task`-typed payloads into the same Temporal-backed execution contract.

Execution requests ultimately dispatch into `MoonMind.UserWorkflow` or another allowed workflow type, then fan out across Temporal worker fleets grouped by capability and security boundary:

| Fleet | Task Queue | Capabilities | Purpose |
|-------|-----------|--------------|---------|
| `workflow` | `mm.workflow` | Workflow orchestration | Workflow code only; no side effects |
| `artifacts` | `mm.activity.artifacts` | Artifact I/O, provider profiles | IO-bound artifact storage and metadata |
| `llm` | `mm.activity.llm` | LLM calls, plan generation, proposals | Rate-limited by provider quotas |
| `sandbox` | `mm.activity.sandbox` | Repo checkout, patch, tests, commands | CPU/memory heavy; strict concurrency limits |
| `integrations` | `mm.activity.integrations` | External provider calls (Jules) | Protected with rate limiting and circuit breakers |
| `agent_runtime` | `mm.activity.agent_runtime` | Supervised agent execution, artifact publish | Long-lived runtime executions |

The `activity_catalog.py` module defines the full routing contract including per-activity timeout and retry policies.

## 6. Legacy Queue Posture

The legacy `/api/queue/jobs` lifecycle routes and `/api/queue` worker callback routes are historical migration references only. They are not the active Workflow-run lifecycle API, and the execution router explicitly rejects falling back to the old queue substrate when Temporal submission is disabled.

## 7. Related Documentation

- [../Api/ExecutionsApiContract.md](../Api/ExecutionsApiContract.md) — Direct execution lifecycle contract
- [WorkflowArchitecture.md](WorkflowArchitecture.md) — Overall Workflow system design
- [../UI/WorkflowConsoleArchitecture.md](../UI/WorkflowConsoleArchitecture.md) — Workflow-oriented UI over execution APIs
- [WorkflowProposalSystem.md](WorkflowProposalSystem.md) — Workflow proposal generation
- [WorkflowCancellation.md](WorkflowCancellation.md) — Cancellation flow
- [../ManagedAgents/LiveLogs.md](../ManagedAgents/LiveLogs.md) — Managed-run log and observability design
- [../Temporal/TemporalArchitecture.md](../Temporal/TemporalArchitecture.md) — Temporal infrastructure
