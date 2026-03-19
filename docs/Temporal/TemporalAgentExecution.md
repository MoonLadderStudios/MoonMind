# Temporal Agent Execution

**Status:** Active design  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-19  
**Audience:** backend, workflow authors, operators

## 1. Purpose

This document explains how the Temporal-based execution system is designed to
receive an agent task - whether a registered **tool** invocation (currently a
`skill` subtype like `pr-resolver`) or a **generic LLM instruction** - and
execute it end to end.

It maps every step from HTTP submission through the Temporal workflow, into the
relevant activity workers, and back. Open migration gaps are tracked in
`docs/Temporal/RemainingWork.md`.

For broader architecture context see
[TemporalArchitecture.md](TemporalArchitecture.md) and
[RemainingWork.md](RemainingWork.md).

---

## 2. End-to-End Execution Flow

### 2.1 Job submission

```
Caller (batch script / UI / API client)
  │
  ▼
POST /api/queue/jobs   { type: "task", payload: { ... } }
  │
  │  routing logic in agent_queue.py
  │  checks settings.temporal_dashboard.submit_enabled
  │  target == "temporal"
  │
  ▼
_create_execution_from_task_request()       ← executions.py
  │
  │  extracts initial_parameters from payload:
  │    requestType, repository, requiredCapabilities,
  │    targetRuntime, model, effort, publishMode,
  │    proposeTasks, stepCount, priority, maxAttempts
  │
  ▼
TemporalExecutionService.create_execution()  ← service.py
  │
  │  1. Creates TemporalExecutionRecord in Postgres
  │  2. Starts Temporal workflow via TemporalClientAdapter
  │     workflow_type = "MoonMind.Run"
  │     task_queue    = "mm.workflow"
  │     input = RunWorkflowInput { workflowType, title,
  │               initialParameters, inputArtifactRef, planArtifactRef }
  │
  ▼
MoonMind.Run workflow starts on Temporal Server
```

### 2.2 `MoonMind.Run` workflow lifecycle

```python
# moonmind/workflows/temporal/workflows/run.py

@workflow.defn(name="MoonMind.Run")
class MoonMindRunWorkflow:

    async def run(self, input_payload):
        # Phase 1: Initialize
        workflow_type, parameters, input_ref, plan_ref = self._initialize_from_payload(...)
        self._set_state("initializing")

        await workflow.wait_condition(lambda: not self._paused)  # honors pause signal

        # Phase 2: Plan
        self._set_state("planning")
        resolved_plan_ref = await self._run_planning_stage(...)

        # Phase 3: Execute
        self._set_state("executing")
        await self._run_execution_stage(parameters, plan_ref)

        # Phase 4: Finalize
        self._set_state("finalizing")
        self._set_state("succeeded")
        return {"status": "success"}
```

The workflow also has signal/update handlers for `pause`, `resume`, `cancel`,
`approve`, and `update_parameters`, plus an integration polling loop for
external providers like Jules.

### 2.3 Planning stage (implemented ✅)

```
_run_planning_stage()
  │
  ▼
workflow.execute_activity("plan.generate", ...)
  │  task_queue = "mm.activity.llm"
  │  timeout   = 15 min
  │
  ▼
TemporalPlanActivities.plan_generate()       ← activity_runtime.py
  │
  │  1. Read input artifact (if any) from artifact store
  │  2. Read skill registry snapshot (if any)
  │  3. Call the configured PlanGenerator callback
  │  4. Validate the resulting plan against parse_plan_definition()
  │  5. Write validated plan as a JSON artifact
  │  6. Return PlanGenerateActivityResult { plan_ref }
  │
  ▼
Workflow stores plan_ref in memo for UI visibility
```

The planning activity is fully implemented. If a `plan_artifact_ref` is already
provided in the input payload (pre-planned job), this stage is skipped.

### 2.4 Execution stage (implemented)

The execution stage dispatches plan steps using two paths depending on the
step's `tool.type`:

- **`tool.type = "agent_runtime"`** — dispatched as a `MoonMind.AgentRun`
  child workflow via `workflow.execute_child_workflow("MoonMind.AgentRun", ...)`.
  This is the primary path for all agent execution (both managed and external).
- **Other tool types** (e.g. `"skill"`) — dispatched as a standard Temporal
  activity via `workflow.execute_activity()`.

```
_run_execution_stage()
  │
  │  1. Read the resolved plan from plan_ref
  │  2. For each node in plan.nodes (a Step ToolInvocation):
  │     a. Check tool.type to determine dispatch path
  │     b. Build the execution payload
  │     c. Dispatch:
  │
  ├── tool.type == "agent_runtime":
  │   │
  │   ▼
  │ workflow.execute_child_workflow("MoonMind.AgentRun", AgentExecutionRequest {
  │     agent_kind:    "managed" | "external",
  │     agent_id:      "gemini_cli" | "codex_cli" | "jules" | ...,
  │     instruction_ref, input_refs, workspace_spec, parameters, ...
  │ })
  │   │
  │   ▼
  │ MoonMind.AgentRun child workflow
  │   → selects AgentAdapter (ManagedAgentAdapter or ExternalAgentAdapter)
  │   → start → wait → fetch_result → publish outputs
  │   → returns AgentRunResult { output_refs, summary, diagnostics, ... }
  │
  └── tool.type == "skill" (or other activity-based types):
      │
      ▼
    workflow.execute_activity("mm.skill.execute", ...)
      → returns ToolResult { status, outputs, output_artifacts }
```

For **generic LLM text instructions** (no specific tool), planning produces
`tool = { type: "agent_runtime", name: "auto", version: "1.0" }` and the
workflow dispatches it as a `MoonMind.AgentRun` child workflow. The `runtime.mode`
field (e.g. `codex`, `gemini_cli`, `jules`) determines which `AgentAdapter`
implementation handles the run.

For the full agent execution model including managed runtime supervision, external
agent callbacks, and auth-profile controls, see
[`ManagedAndExternalAgentExecutionModel.md`](ManagedAndExternalAgentExecutionModel.md).

---

## 3. Contract Reference

### 3.1 Payload shape from batch scripts / API clients

```json
{
  "type": "task",
  "priority": 0,
  "maxAttempts": 3,
  "payload": {
    "repository": "owner/repo",
    "targetRuntime": "codex",
    "requiredCapabilities": ["gh"],
    "task": {
      "instructions": "Resolve PR #42 on branch `feature/test`.",
      "tool": {
        "type": "skill",
        "name": "pr-resolver",
        "version": "1.0"
      },
      "inputs": {
        "repo": "owner/repo",
        "pr": "42",
        "branch": "feature/test",
        "mergeMethod": "squash",
        "maxIterations": 3
      },
      "runtime": { "mode": "codex", "model": "...", "effort": "..." },
      "git": { "startingBranch": "feature/test", "newBranch": "feature/test" },
      "publish": { "mode": "none" }
    }
  }
}
```

Legacy compatibility note:

- `task.skill` remains accepted during migration.
- Canonical Temporal-era payloads should emit `task.tool`.

### 3.2 Step ToolInvocation contract (plan node level)

From `moonmind/workflows/skills/skill_plan_contracts.py`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✅ | Unique node identifier within the plan |
| `tool_name` | string | ✅ | Registered tool name (e.g. `"pr-resolver"`) |
| `tool_version` | string | ✅ | Registry version (e.g. `"1.0"`) |
| `inputs` | object | ✅ | Tool-specific input parameters |
| `options` | object | ❌ | Optional execution options |

Serialized payload form (canonical): `{ id, tool: { type, name, version }, inputs: {...} }`
Serialized payload form (legacy accepted): `{ id, skill: { name, version }, inputs: {...} }`

### 3.3 Activity catalog

| Activity Type | Fleet | Task Queue | Description |
|---------------|-------|------------|-------------|
| `plan.generate` | llm | `mm.activity.llm` | LLM-driven plan generation |
| `plan.validate` | llm | `mm.activity.llm` | Plan validation against registry |
| `mm.skill.execute` | by_capability | `mm.activity.llm` (default) | Skill dispatch via registry |
| `agent_runtime.publish_artifacts` | agent_runtime | `mm.activity.agent_runtime` | Publish agent run outputs |
| `agent_runtime.cancel` | agent_runtime | `mm.activity.agent_runtime` | Cancel managed/external agent run |
| `sandbox.run_command` | sandbox | `mm.activity.sandbox` | Shell command execution |
| `sandbox.checkout_repo` | sandbox | `mm.activity.sandbox` | Git repo checkout |
| `sandbox.apply_patch` | sandbox | `mm.activity.sandbox` | Patch application |
| `sandbox.run_tests` | sandbox | `mm.activity.sandbox` | Test runner |
| `artifact.*` | artifacts | `mm.activity.artifacts` | Artifact CRUD |
| `integration.jules.*` | integrations | `mm.activity.integrations` | Jules integration |

### 3.4 Worker fleet topology

| Fleet | Task Queue | Capabilities | Scaling |
|-------|------------|--------------|---------|
| `workflow` | `mm.workflow` | workflow orchestration | Lightweight, no side effects |
| `llm` | `mm.activity.llm` | LLM calls, plan generation | Rate-limited by provider |
| `sandbox` | `mm.activity.sandbox` | Shell exec, git, builds | CPU/memory heavy |
| `agent_runtime` | `mm.activity.agent_runtime` | Managed runtime supervision, auth profiles | Provider rate-limited |
| `artifacts` | `mm.activity.artifacts` | Artifact storage IO | IO-bound |
| `integrations` | `mm.activity.integrations` | Jules, webhooks | Rate-limited |

---

## 4. Current Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Job submission** → Temporal routing | ✅ Implemented | `POST /api/queue/jobs` with `target=temporal` |
| **`TemporalExecutionService.create_execution`** | ✅ Implemented | Creates DB record + starts workflow |
| **`MoonMind.Run` workflow definition** | ✅ Implemented | Full lifecycle with signals/updates |
| **Planning stage** (`plan.generate`) | ✅ Implemented | Activity + planner callback |
| **Execution stage** (`_run_execution_stage`) | ✅ Implemented | Reads plan artifact, dispatches `agent_runtime` nodes as `MoonMind.AgentRun` child workflows, non-agent nodes as activities |
| **`MoonMind.AgentRun` child workflow** | ✅ Implemented | Unified agent execution lifecycle for managed and external agents |
| **`AgentAdapter` protocol** | ✅ Implemented | `ManagedAgentAdapter` + `JulesAgentAdapter` + `CodexCloudAgentAdapter` |
| **`SkillInvocation` contract** | ✅ Implemented | Validated dataclass in `skill_plan_contracts.py` |
| **Activity catalog** | ✅ Implemented | 18 activities, 5 fleets, correct routing |
| **Worker runtime bindings** | ✅ Implemented | `build_activity_bindings` wires handlers to catalog |
| **Temporal↔DB state sync** | ✅ Implemented | Projection sync from Temporal visibility |
| **Batch-PR-resolver payload** | ✅ Fixed | Now sends `skill.name` + `skill.version` + `inputs` |
| **Integration polling** (Jules) | ✅ Implemented | Durable waiting loop with status polling |
| **Pause/Resume/Cancel signals** | ✅ Implemented | Workflow signal handlers |
| **`plan.validate` activity** | ✅ Implemented | Registry-aware plan validation |

---

## 5. Remaining Work

The execution-stage tasks formerly listed here are now implemented. The
canonical open backlog is maintained in
[`docs/Temporal/RemainingWork.md`](RemainingWork.md).

---

## 6. Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────────┐
│                           API Service                                     │
│  POST /api/queue/jobs ──► _create_execution_from_task_request()           │
│                              │                                            │
│                              ▼                                            │
│                     TemporalExecutionService                              │
│                        .create_execution()                                │
│                              │                                            │
│                    ┌─────────┴──────────┐                                 │
│                    │  DB Record Created  │                                 │
│                    └─────────┬──────────┘                                 │
│                              │ starts workflow                            │
└──────────────────────────────┼────────────────────────────────────────────┘
                               ▼
┌─────────────────── Temporal Server ───────────────────────────────────────┐
│                                                                           │
│   MoonMind.Run  (task queue: mm.workflow)                                 │
│   ┌──────────────────────────────────────────────────────────────────┐    │
│   │ initialize ──► plan ──► execute ──► [proposals] ──► finalize    │    │
│   │                  │          │                                    │    │
│   │    ┌─────────────┘          └──────────────────┐                │    │
│   │    ▼                                          ▼                 │    │
│   │  plan.generate          per-step dispatch by tool.type          │    │
│   │  (mm.activity.llm)      ┌──────────────┬──────────────┐         │    │
│   │                         ▼              ▼              ▼         │    │
│   │                  agent_runtime     skill/other     sandbox      │    │
│   │                  (child wf)        (activity)      (activity)   │    │
│   │                      │                                          │    │
│   │                      ▼                                          │    │
│   │               MoonMind.AgentRun                                 │    │
│   │               ├─ ManagedAgentAdapter → Gemini CLI / Codex CLI   │    │
│   │               └─ ExternalAgentAdapter → Jules / BYOA            │    │
│   └──────────────────────────────────────────────────────────────────┘    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```
