# Temporal Agent Execution

**Status:** Active  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-27  
**Audience:** backend, workflow authors, operators

## 1. Purpose

This document explains how MoonMind receives a task-shaped request and executes
it end to end on the Temporal-backed execution plane.

## 2. End-to-End Execution Flow

### 2.1 Submission

```text
Caller (UI / API client / automation)
  │
  ▼
POST /api/executions
  │
  ▼
TemporalExecutionService.create_execution()
  │
  ├─ create TemporalExecutionRecord
  └─ start workflow via TemporalClientAdapter
  ▼
MoonMind.Run starts on Temporal
```

### 2.2 `MoonMind.Run` lifecycle

```python
@workflow.defn(name="MoonMind.Run")
class MoonMindRunWorkflow:
    async def run(self, input_payload):
        self._set_state("initializing")
        await workflow.wait_condition(lambda: not self._paused)

        self._set_state("planning")
        resolved_plan_ref = await self._run_planning_stage(...)

        self._set_state("executing")
        await self._run_execution_stage(...)

        self._set_state("finalizing")
        self._set_state("completed")
```

The workflow also supports control updates and signals such as:

- `UpdateInputs`
- `SetTitle`
- `RequestRerun`
- `Approve`
- `Pause`
- `Resume`
- `ExternalEvent`
- `reschedule` where applicable for in-workflow scheduled waits

Desired-state rule:

- acknowledged execution controls use Temporal Updates,
- asynchronous callbacks use Signals,
- mutable deferred waits use the dedicated `reschedule` signal path.

### 2.3 Planning stage

Planning typically runs through `plan.generate` on `mm.activity.llm`.

Typical behavior:

1. read input artifacts
2. read skill/tool registry context as needed
3. generate or validate a plan
4. write the resulting plan artifact
5. expose plan metadata to the workflow and UI

### 2.4 Execution stage

Execution dispatches plan nodes by `tool.type`:

- **`agent_runtime`** nodes become `MoonMind.AgentRun` child workflows
- **activity-based tools** such as skills run as Temporal activities

```text
_run_execution_stage()
  ├─ read plan
  ├─ build per-node execution payload
  ├─ dispatch child workflow or activity
  └─ collect outputs and publish artifacts
```

## 3. Canonical Payload Shape

```json
{
  "type": "task",
  "payload": {
    "repository": "owner/repo",
    "requiredCapabilities": ["gh"],
    "task": {
      "instructions": "Resolve PR #42.",
      "tool": {
        "type": "skill",
        "name": "pr-resolver",
        "version": "1.0"
      },
      "inputs": {
        "repo": "owner/repo",
        "pr": "42"
      },
      "runtime": {
        "mode": "codex",
        "model": "...",
        "effort": "..."
      },
      "publish": {
        "mode": "none"
      }
    }
  }
}
```

Compatibility note:

- `task.tool` is canonical
- `task.skill` may remain accepted as a compatibility alias in some callers

## 4. Activity and Fleet Topology

| Fleet | Task Queue | Purpose |
| --- | --- | --- |
| `workflow` | `mm.workflow` | workflow orchestration |
| `llm` | `mm.activity.llm` | planning and model calls |
| `sandbox` | `mm.activity.sandbox` | shell exec, git, builds, tests |
| `agent_runtime` | `mm.activity.agent_runtime` | managed runtime supervision |
| `artifacts` | `mm.activity.artifacts` | artifact CRUD |
| `integrations` | `mm.activity.integrations` | provider integrations and callbacks |

## 5. Current Implementation Posture

Implemented components:

- execution creation through `TemporalExecutionService`
- `MoonMind.Run` workflow lifecycle
- planning activity flow
- execution-stage dispatch to child workflows and activities
- `MoonMind.AgentRun` child workflow
- projection sync from Temporal visibility
- pause/resume/cancel/approve style control handlers

## 6. Architecture Diagram

```text
API Service
  POST /api/executions
    -> TemporalExecutionService.create_execution()
      -> create projection row
      -> start MoonMind.Run

Temporal Server
  MoonMind.Run (mm.workflow)
    -> plan.generate
    -> per-step dispatch
       -> MoonMind.AgentRun child workflow
       -> skill/activity execution
       -> sandbox/integration/artifact activities
```
