# Orchestrator as a First-Class Temporal Workflow

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-03-14

## 1. Purpose

Align the MoonMind Orchestrator execution model natively with **Temporal Workflows** so that:

* Orchestrator work is surfaced in the unified Mission Control Mission UI as a standard **Workflow Execution** alongside AI agent workflows and manifest ingestions.
* Orchestrator tasks are created using **similar step + skill authoring** approaches as agent tasks.
* The orchestrator can **continue executing** even if the main Postgres application database goes down, leveraging Temporal's native resilience and durable execution guarantees.

## 2. Legacy State (Before Temporal)

### 2.1 Database Queue constraints
Previously, Orchestrator commands were submitted to `POST /orchestrator/runs`, creating an entry in an `orchestrator_runs` Postgres table. This table row then spawned an Agent Queue job (`orchestrator_run`). This dual-write process was fragile and heavily coupled to the availability of the application database.

### 2.2 Orchestrator plan steps were a fixed enum
The orchestrator plan-step enum was fixed (`analyze/patch/build/restart/verify/rollback`), and step-state persistence enforced uniqueness per plan step. Arbitrary N steps were impossible without a tedious database model change.

## 3. Goals

1. **Unified Execution**: “Orchestrator Runs” simply become **Orchestrator Workflows** executed by the Temporal cluster.
2. **Unified Authoring**: Orchestrator tasks can be authored with a **step list** and **skill selection** that maps natively to a sequence of Temporal Activities.
3. **Unified Monitoring**: Remove dedicated orchestrator list/detail database polling routes and render orchestrator tasks in the unified Mission Control workflow list directly from Temporal visibility APIs.
4. **Native Resilience**: By relying on Temporal, if Postgres becomes unreachable mid-execution, the orchestrator continues executing Activities. Checkpoints occur within Temporal's workflow history.

## 4. Proposed Design

### 4.1 Terminology and External Contracts

**User-facing**:
* Rename “Orchestrator run” → “Orchestrator Workflow”

**API consolidation**:
The REST mappings become thin wrappers triggering Temporal workflows:
* `POST /api/workflows/orchestrator` → Triggers `OrchestratorWorkflow` on Temporal.
* `GET /api/workflows` → Fetches from Temporal visibility (returns both Agent tasks and Orchestrator tasks).

### 4.2 Unified Dashboard UX

#### 4.2.1 Route consolidation
**Change**:
* Replace `/tasks/orchestrator` and `/tasks/queue` with a unified `/workflows/list`.
* Unified detail page: `/workflows/:workflowId`.

#### 4.2.2 Unified list data model
The dashboard queries Temporal's visibility API (e.g., Elasticsearch or standard List filters) to fetch all executions, normalizing the display regardless of Workflow Type:
* Type: `AgentTaskWorkflow`, `OrchestratorWorkflow`, etc.
* Status: `Running`, `Completed`, `Failed`, etc.

### 4.3 Unified Authoring: Orchestrator Workflows Use "Skills"

#### 4.3.1 Submit form behavior
The dashboard runtime selector allows picking the Orchestrator runtime. The UI shows:
* Step editor (same as agent tasks)
* Skill selection per step
* Orchestrator-specific fields: `targetService`, `priority`, `approvalRequired`

#### 4.3.2 Backend contract
When the UI submits an Orchestrator workflow, the payload sent to Temporal is simply:
```json
{
  "targetService": "api",
  "approvalRequired": true,
  "steps": [
    {
      "id": "step-1",
      "skill": { "id": "update-moonmind", "args": { "restartOrchestrator": true } }
    }
  ]
}
```

**Interpretation rule**:
* If `steps` is empty: The Temporal Workflow executes the standard internal activity sequence (`analyze -> patch -> build -> restart -> verify`).
* If `steps` is provided: The Temporal Workflow sequentially triggers the mapped OpenHands or script-backed Activities.

### 4.4 Execution Model & Resilience

#### 4.4.1 Temporal Native Resilience
The requirement for "Degraded Mode: Continue Working Without Postgres" is easily satisfied by Temporal.
* If the primary MoonMind application Postgres DB goes down, the orchestrator worker (connected to Temporal via gRPC) continues to lease Activities, execute Docker builds (`tcp://docker-proxy:2375`), and stream logs to artifacts.
* Temporal Workflow History retains the absolute truth of step progress.

#### 4.4.2 Activity Checkpointing
Instead of custom DB writes, the Orchestrator Workflow relies on `activity.heartbeat()`. Logs and artifacts are streamed to object storage or the shared `/work/agent_jobs/<workflow_id>/artifacts` named volume.

### 4.5 Security and Policy Alignment
* Preserve orchestrator approval gates. The `OrchestratorWorkflow` uses Temporal `workflow.wait_condition()` to block on a human-provided approval Signal.
* Role-based access control applies to who can send the `Approved` signal to the execution.

## 5. Migration Plan

### Phase 1 — UI consolidation + Temporal Workflow skeleton
1. Develop `OrchestratorWorkflow` in Python leveraging standard Activities.
2. Update dashboard: Replace `/tasks/orchestrator` with unified `/workflows/list` utilizing Temporal visibility APIs.

### Phase 2 — Remove legacy DB modeling
1. Drop the legacy `orchestrator_runs` REST endpoints and database tables.
2. All execution reporting delegates to Temporal's native UI or the wrapped MoonMind visibility endpoints.

### Phase 3 — Steps + skills authoring
1. Extend `OrchestratorWorkflow` to accept arrays of dynamic skill definitions.
2. Update the Unified Submit Form to allow sequential skill definitions for orchestrator payloads.
