# Task Runs API

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-16

## 1. Purpose

Define the REST API surface for creating, monitoring, and controlling MoonMind task runs. The API acts as a translation layer between the Mission Control UI and the Temporal Workflow engine: incoming HTTP requests are normalized into the canonical task payload and dispatched as `MoonMind.Run` Temporal Workflow Executions.

## 2. API Surface

Task runs are served by two routers:

- **`/api/queue/jobs`** — Job lifecycle (create, list, detail, update, cancel, resubmit, events, artifacts, attachments).
- **`/api/task-runs`** — Live session controls and operator interaction during an active run.

### 2.1 Job Lifecycle (`/api/queue/jobs`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/queue/jobs` | Create a new task run from canonical payload |
| `POST` | `/api/queue/jobs/with-attachments` | Create with file attachments (multipart) |
| `GET`  | `/api/queue/jobs` | List task runs (filterable by `type`, `limit`) |
| `GET`  | `/api/queue/jobs/{jobId}` | Get task run detail |
| `PUT`  | `/api/queue/jobs/{jobId}` | Update a queued task run before execution |
| `POST` | `/api/queue/jobs/{jobId}/resubmit` | Clone a terminal task run into a new queued run |
| `POST` | `/api/queue/jobs/{jobId}/cancel` | Cancel a running or queued task run |
| `GET`  | `/api/queue/jobs/{jobId}/events` | List run events |
| `GET`  | `/api/queue/jobs/{jobId}/events/stream` | SSE stream of run events |
| `GET`  | `/api/queue/jobs/{jobId}/artifacts` | List run artifacts |
| `GET`  | `/api/queue/jobs/{jobId}/artifacts/{artifactId}/download` | Download an artifact |
| `GET`  | `/api/queue/jobs/{jobId}/attachments` | List run attachments |
| `GET`  | `/api/queue/jobs/{jobId}/attachments/{attachmentId}/download` | Download an attachment |
| `GET`  | `/api/queue/jobs/{jobId}/finish-summary` | Get completion summary |

### 2.2 Worker Callbacks (`/api/queue`)

These endpoints are authenticated with worker tokens and used by agent workers during execution.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/queue/jobs/claim` | Worker claims a queued job |
| `POST` | `/api/queue/jobs/{jobId}/heartbeat` | Worker heartbeat while executing |
| `POST` | `/api/queue/jobs/{jobId}/runtime-state` | Worker reports runtime state |
| `POST` | `/api/queue/jobs/{jobId}/complete` | Worker marks job succeeded |
| `POST` | `/api/queue/jobs/{jobId}/fail` | Worker marks job failed |
| `POST` | `/api/queue/jobs/{jobId}/cancel/ack` | Worker acknowledges cancellation |
| `POST` | `/api/queue/jobs/{jobId}/recover` | Worker requests recovery |

### 2.3 Live Session & Control (`/api/task-runs`)

These endpoints manage real-time operator interaction with an in-progress run (live session metadata, grants, and related controls).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/task-runs/{taskRunId}/live-session` | Create/enable live session tracking |
| `GET`  | `/api/task-runs/{taskRunId}/live-session` | Get live session state |
| `POST` | `/api/task-runs/{taskRunId}/live-session/grant-write` | Grant temporary RW attach credentials |
| `POST` | `/api/task-runs/{taskRunId}/live-session/revoke` | Force-revoke a live session |
| `POST` | `/api/task-runs/{taskRunId}/live-session/report` | Worker reports live session metadata |
| `POST` | `/api/task-runs/{taskRunId}/live-session/heartbeat` | Worker heartbeat for live sessions |
| `GET`  | `/api/task-runs/{taskRunId}/live-session/worker` | Worker-token view of session state |
| `POST` | `/api/task-runs/{taskRunId}/control` | Apply pause/resume/takeover controls |
| `POST` | `/api/task-runs/{taskRunId}/operator-messages` | Append operator message to event stream |

### 2.4 Task Control via Temporal Signals

Control operations (`POST .../control`) translate the REST request into a Temporal Signal targeted at the Workflow Execution ID for the task run. Supported actions include `pause`, `resume`, and `takeover`.

## 3. Canonical Task Payload

Tasks submitted via `POST /api/queue/jobs` are normalized into the canonical payload shape defined in `moonmind.workflows.agent_queue.task_contract`. The contract supports three ingest formats (`task`, `codex_exec`, `codex_skill`), all of which are normalized into the same canonical structure.

```json
{
  "repository": "owner/repo",
  "requiredCapabilities": ["git", "codex"],
  "targetRuntime": "codex",
  "auth": {
    "repoAuthRef": null,
    "publishAuthRef": null
  },
  "task": {
    "instructions": "Implement feature and run tests",
    "skill": {
      "id": "auto",
      "args": {}
    },
    "runtime": {
      "mode": "codex",
      "model": null,
      "effort": null
    },
    "git": {
      "startingBranch": null,
      "targetBranch": null
    },
    "publish": {
      "mode": "pr",
      "prBaseBranch": null,
      "commitMessage": null,
      "prTitle": null,
      "prBody": null
    },
    "proposeTasks": true,
    "steps": [],
    "container": null,
    "proposalPolicy": null
  }
}
```

### 3.1 Key Payload Sections

- **`task.runtime`** — Selects the agent runtime (`codex`, `gemini`, `claude`, `jules`) and optional model/effort overrides.
- **`task.skill`** — Selects a skill from the skill catalog. `"auto"` lets the system pick based on instructions.
- **`task.publish`** — Controls how results are published (`pr`, `branch`, `none`). Certain skills force specific publish modes.
- **`task.steps`** — Optional ordered list of execution steps, each with its own instructions and optional skill override. Steps receive sequential IDs (`step-1`, `step-2`, …).
- **`task.container`** — Optional custom container execution with `image`, `command`, `env`, and `timeout_seconds`. Mutually exclusive with `task.steps`.
- **`task.proposalPolicy`** — Controls task proposal emission targets (`project`, `moonmind`) and per-target limits.
- **`task.proposeTasks`** — Whether the worker should generate follow-up task proposals.
- **`requiredCapabilities`** — Auto-populated from runtime, publish mode, skill requirements, and container settings.

### 3.2 Legacy Format Support

The contract includes backward-compatible normalization for:

- **`codex_exec`** jobs — Flat payloads with top-level `instruction`, `publish`, and `codex` blocks.
- **`codex_skill`** jobs — Payloads with `skillId` and `inputs` blocks from the older skill execution API.
- **Top-level `targetRuntime`** — Lifted into `task.runtime.mode` during normalization.

## 4. Temporal Dispatch

Once the payload is normalized, the system creates a `MoonMind.Run` Temporal Workflow Execution. Activity execution is dispatched across specialized Temporal Task Queues grouped by worker fleet:

| Fleet | Task Queue | Capabilities | Purpose |
|-------|-----------|--------------|---------|
| `workflow` | `mm.workflow` | Workflow orchestration | Workflow code only; no side effects |
| `artifacts` | `mm.activity.artifacts` | Artifact I/O, auth profiles | IO-bound artifact storage and metadata |
| `llm` | `mm.activity.llm` | LLM calls, plan generation, proposals | Rate-limited by provider quotas |
| `sandbox` | `mm.activity.sandbox` | Repo checkout, patch, tests, commands | CPU/memory heavy; strict concurrency limits |
| `integrations` | `mm.activity.integrations` | External provider calls (Jules) | Protected with rate limiting and circuit breakers |
| `agent_runtime` | `mm.activity.agent_runtime` | Supervised agent execution, artifact publish | Long-lived runtime executions |

The `activity_catalog.py` module defines the full routing contract including per-activity timeout and retry policies.

## 5. Related Documentation

- [TaskArchitecture.md](TaskArchitecture.md) — Overall task system design
- [TasksStepSystem.md](TasksStepSystem.md) — Multi-step task execution
- [TaskProposalSystem.md](TaskProposalSystem.md) — Task proposal generation
- [TaskCancellation.md](TaskCancellation.md) — Cancellation flow
- [TemporalArchitecture.md](../Temporal/TemporalArchitecture.md) — Temporal infrastructure
