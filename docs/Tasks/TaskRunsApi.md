# Task Runs API

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27

## 1. Purpose

Define the REST API surface for creating, monitoring, and controlling MoonMind
task runs.

The API is a translation layer between Mission Control's task-oriented UX and
Temporal-backed workflow execution.

## 2. API Surface

Task execution is primarily served by two router families:

- **`/api/executions`** — execution lifecycle
- **`/api/task-runs`** — live session and operator interaction for active runs

### 2.1 Execution lifecycle (`/api/executions`)

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/executions` | Create a new execution from task- or workflow-shaped payload |
| `GET` | `/api/executions` | List executions |
| `GET` | `/api/executions/{workflowId}` | Get execution detail |
| `POST` | `/api/executions/{workflowId}/update` | Apply workflow update such as `UpdateInputs` or `SetTitle` |
| `POST` | `/api/executions/{workflowId}/signal` | Deliver workflow signals such as `Approve`, `Pause`, `Resume`, or `ExternalEvent` |
| `POST` | `/api/executions/{workflowId}/cancel` | Cancel an execution |
| `POST` | `/api/executions/{workflowId}/reschedule` | Reschedule a deferred execution |
| `GET` | `/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts` | List artifacts for a specific execution run |

### 2.2 Live session and operator control (`/api/task-runs`)

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/api/task-runs/{taskRunId}/live-session` | Create or enable live session tracking |
| `GET` | `/api/task-runs/{taskRunId}/live-session` | Get live session state |
| `POST` | `/api/task-runs/{taskRunId}/live-session/grant-write` | Grant temporary RW attach credentials |
| `POST` | `/api/task-runs/{taskRunId}/live-session/revoke` | Force-revoke a live session |
| `POST` | `/api/task-runs/{taskRunId}/live-session/report` | Worker reports live session metadata |
| `POST` | `/api/task-runs/{taskRunId}/live-session/heartbeat` | Worker heartbeat for live sessions |
| `GET` | `/api/task-runs/{taskRunId}/live-session/worker` | Worker-token view of session state |
| `POST` | `/api/task-runs/{taskRunId}/control` | Apply pause/resume/takeover controls |
| `POST` | `/api/task-runs/{taskRunId}/operator-messages` | Append operator messages |

### 2.3 Control via Temporal

Task control operations translate HTTP requests into Temporal lifecycle actions:

- create/start
- update
- signal
- cancel
- reschedule

## 3. Canonical Task Payload

Task-shaped create requests submit through `POST /api/executions` and are
normalized into the execution payload consumed by `MoonMind.Run`.

```json
{
  "type": "task",
  "payload": {
    "repository": "owner/repo",
    "requiredCapabilities": ["git", "codex"],
    "task": {
      "instructions": "Implement feature and run tests",
      "tool": {
        "type": "skill",
        "name": "pr-resolver",
        "version": "1.0"
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
        "mode": "pr"
      },
      "proposeTasks": true,
      "steps": []
    }
  }
}
```

Key sections:

- `task.runtime` — selects runtime and optional model/effort overrides
- `task.tool` — canonical executable field; `skill` may remain as a compatibility
  alias
- `task.publish` — publish behavior (`pr`, `branch`, `none`)
- `task.steps` — optional multi-step execution plan inputs
- `requiredCapabilities` — derived capability requirements

## 4. Temporal Dispatch

Once normalized, the system creates a `MoonMind.Run` Temporal workflow
execution.

Activity execution is routed across specialized worker fleets:

| Fleet | Task Queue | Purpose |
| --- | --- | --- |
| `workflow` | `mm.workflow` | Workflow orchestration |
| `artifacts` | `mm.activity.artifacts` | Artifact I/O |
| `llm` | `mm.activity.llm` | LLM calls, planning, proposals |
| `sandbox` | `mm.activity.sandbox` | Repo work, commands, tests |
| `integrations` | `mm.activity.integrations` | External providers |
| `agent_runtime` | `mm.activity.agent_runtime` | Managed/external runtime supervision |

## 5. Related Documentation

- `docs/Tasks/TaskArchitecture.md`
- `docs/Tasks/TaskCancellation.md`
- `docs/Tasks/TaskFinishSummarySystem.md`
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalAgentExecution.md`
