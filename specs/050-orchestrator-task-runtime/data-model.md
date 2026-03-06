# Data Model: Orchestrator Task Runtime Upgrade

## 1. `OrchestratorTask` (canonical workload record)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | UUID | yes | Canonical task id; exposed as both `taskId` and transitional `runId`. |
| `instruction` | text | yes | Operator-provided objective text. |
| `targetService` | string | yes | Service profile key (`orchestrator`, etc.). |
| `priority` | enum (`normal`, `high`) | yes | Scheduling priority. |
| `status` | enum (`pending`, `running`, `awaiting_approval`, `succeeded`, `failed`, `rolled_back`) | yes | Top-level lifecycle status. |
| `approvalGateId` | UUID nullable | no | Optional approval policy binding. |
| `approvalToken` | encrypted text nullable | no | Approval token snapshot for protected services. |
| `queuedAt` | timestamp | yes | Queueing timestamp. |
| `startedAt` | timestamp nullable | no | First execution start. |
| `completedAt` | timestamp nullable | no | Terminal completion timestamp. |
| `metricsSnapshot` | JSON nullable | no | Runtime progress/metrics snapshots. |
| `artifactRoot` | string nullable | no | Artifact directory root path. |

### Lifecycle transitions
- `pending -> running`
- `pending -> awaiting_approval`
- `awaiting_approval -> pending` (approval granted)
- `running -> succeeded|failed|rolled_back`
- `failed|rolled_back -> pending` (retry)

## 2. `OrchestratorTaskStep` (ordered explicit task runtime step)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | UUID | yes | Internal row id. |
| `taskId` | UUID FK | yes | Parent `OrchestratorTask`. |
| `stepId` | string (1..128) | yes | Stable user-facing identifier; unique per task. |
| `index` | integer (`>=0`) | yes | Execution order. |
| `title` | string | yes | Display title. |
| `instructions` | text | yes | Step objective text. |
| `skill.id` | string | yes | Runnable orchestrator skill id; cannot be `auto`. |
| `skill.args` | JSON object | yes | Per-step skill arguments. |
| `status` | enum (`queued`, `running`, `succeeded`, `failed`, `skipped`) | yes | Step status. |
| `attempt` | integer (`>=1`) | yes | Retry attempt counter. |
| `message` | text nullable | no | Last step status message. |
| `artifactRefs` | JSON list | yes | Artifact paths produced by step. |
| `startedAt` | timestamp nullable | no | Step start time. |
| `finishedAt` | timestamp nullable | no | Step finish time. |

### Validation rules
- Duplicate `stepId` values are rejected.
- Empty `stepId`, `instructions`, or `skill.id` are rejected.
- `skill.id == auto` is rejected for orchestrator task steps.
- Provided step order is preserved by `index`.

## 3. `OrchestratorActionPlan` (legacy plan compatibility)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | UUID | yes | Existing plan id. |
| `steps` | JSON list | yes | Legacy enum-oriented steps. |
| `serviceContext` | JSON object | yes | Target service profile context. |
| `generatedBy` | enum | yes | `operator|llm|system`. |

Used when `steps[]` is absent in create payload; preserved for backward-compatible `orchestrator_run` processing.

## 4. `OrchestratorStateSnapshot` (artifact-backed degraded-mode record)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `recordedAt` | timestamp | yes | Snapshot write time. |
| `type` | enum (`task_status`, `step_status`, `artifact`) | yes | Snapshot event kind. |
| `taskId` | UUID | yes | Task identifier. |
| `stepId` | string nullable | no | Present for `step_status`. |
| `status` | string nullable | no | Task/step status transition. |
| `message` | string nullable | no | Status context message. |
| `artifactRefs` | list<string> | no | Referenced artifact paths. |
| `metadata` | JSON object | no | Additional reconciliation metadata. |

Stored as JSONL under `state-snapshots.jsonl` in the task artifact directory.

## 5. `UnifiedTaskRow` (dashboard list contract)

| Field | Type | Required | Source |
| --- | --- | --- | --- |
| `id` | string | yes | Queue `jobId` or orchestrator `taskId`. |
| `source` | enum (`queue`, `orchestrator`) | yes | Origin discriminator. |
| `runtime` | string nullable | no | Queue runtime mode or orchestrator classification. |
| `status` | normalized enum (`queued`, `running`, `awaiting_action`, `succeeded`, `failed`, `cancelled`) | yes | UI normalized status. |
| `rawStatus` | string | yes | Source-native status. |
| `title` | string | yes | Instruction/service-derived title. |
| `skillId` | string nullable | no | Primary skill marker when available. |
| `createdAt` | timestamp nullable | no | Created/queued time. |
| `startedAt` | timestamp nullable | no | Started time. |
| `finishedAt` | timestamp nullable | no | Terminal time. |

## 6. Request/Response payload shapes

### `POST /orchestrator/tasks` request

```json
{
  "instruction": "Roll out runtime update",
  "targetService": "orchestrator",
  "priority": "normal",
  "approvalToken": "optional-token",
  "steps": [
    {
      "id": "step-1",
      "title": "Refresh services",
      "instructions": "Pull and restart orchestrator stack",
      "skill": {
        "id": "update-moonmind",
        "args": { "restartOrchestrator": true }
      }
    }
  ]
}
```

### `OrchestratorTask` summary response (transitional)

```json
{
  "taskId": "00000000-0000-0000-0000-000000000000",
  "runId": "00000000-0000-0000-0000-000000000000",
  "status": "pending",
  "targetService": "orchestrator",
  "instruction": "Roll out runtime update"
}
```

## 7. Cross-entity constraints
- `OrchestratorTaskStep.taskId` must reference an existing `OrchestratorTask`.
- Step ordering is deterministic by `index`; no duplicate `stepId` per task.
- Reconciliation must be idempotent: replaying state snapshots cannot create duplicate terminal state transitions.
- Queue job payloads must preserve `taskId` and may include `runId` alias during migration.
