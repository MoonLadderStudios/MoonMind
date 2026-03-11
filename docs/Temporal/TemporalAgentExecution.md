# Temporal Agent Execution

**Status:** Active design  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-10  
**Audience:** backend, workflow authors, operators

## 1. Purpose

This document explains how the Temporal-based execution system is designed to
receive an agent task — whether a registered **skill** invocation (like
`pr-resolver`) or a **generic LLM instruction** — and execute it end to end.

It maps every step from HTTP submission through the Temporal workflow, into the
relevant activity workers, and back. It also documents which pieces are
**implemented**, which are **stubs**, and what remains to reach a fully
functional system.

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
        await self._run_execution_stage(parameters, plan_ref)        # ⚠️ STUB

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

### 2.4 Execution stage (⚠️ STUB — current state)

```python
# CURRENT implementation — this is the stub
async def _run_execution_stage(self, *, parameters, plan_ref):
    sandbox_result = await workflow.execute_activity(
        "sandbox.run_command",
        {"principal": ..., "cmd": "echo executing", "timeout_seconds": 300},
        task_queue="mm.activity.sandbox",
    )
```

The execution stage currently runs `echo executing` via `sandbox.run_command`
and returns immediately. **It does not invoke `mm.skill.execute` or dispatch
any real work.**

### 2.5 Execution stage (🎯 target design)

```
_run_execution_stage()
  │
  │  1. Read the resolved plan from plan_ref
  │  2. For each node in plan.nodes (a SkillInvocation):
  │     a. Resolve routing via TemporalActivityCatalog.resolve_skill()
  │     b. Build the invocation payload
  │     c. Execute activity:
  │
  ▼
workflow.execute_activity("mm.skill.execute", {
    invocation_payload: {
        id:           "<node-id>",
        skill:        { name: "pr-resolver", version: "1.0" },
        inputs:       { repo: "...", pr: "42", branch: "..." },
        options:      { ... }
    },
    registry_snapshot_ref: "<artifact-ref>",
    principal:            "<owner-id>",
    context:              { workflow_id, run_id, ... }
})
  │
  ▼
TemporalSkillActivities.mm_skill_execute()       ← activity_runtime.py
  │
  │  1. Resolve registry snapshot (from ref or inline)
  │  2. Parse invocation_payload as SkillInvocation
  │  3. Delegate to execute_skill_activity() → SkillActivityDispatcher
  │  4. Return SkillResult { status, outputs, output_artifacts }
  │
  ▼
SkillActivityDispatcher.execute()                ← skill_dispatcher.py
  │
  │  1. Look up handler by activity_type ("mm.skill.execute")
  │     or by skill name+version from registry
  │  2. Execute the handler (the actual skill logic)
  │  3. Return SkillResult
```

For **generic LLM text instructions** (no specific skill), the plan generator
would produce a plan node with `skill.name = "auto"` and the instructions as
inputs. The dispatcher would route this to a generic LLM execution handler.

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
      "skill": {
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

### 3.2 `SkillInvocation` contract (plan node level)

From `moonmind/workflows/skills/skill_plan_contracts.py`:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✅ | Unique node identifier within the plan |
| `skill_name` | string | ✅ | Registered skill name (e.g. `"pr-resolver"`) |
| `skill_version` | string | ✅ | Registry version (e.g. `"1.0"`) |
| `inputs` | object | ✅ | Skill-specific input parameters |
| `options` | object | ❌ | Optional execution options |

Serialized payload form: `{ id, skill: { name, version }, inputs: {...} }`

### 3.3 Activity catalog

| Activity Type | Fleet | Task Queue | Description |
|---------------|-------|------------|-------------|
| `plan.generate` | llm | `mm.activity.llm` | LLM-driven plan generation |
| `plan.validate` | llm | `mm.activity.llm` | Plan validation against registry |
| `mm.skill.execute` | by_capability | `mm.activity.llm` (default) | Skill dispatch via registry |
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
| **Execution stage** (`_run_execution_stage`) | ⚠️ **Stub** | Runs `echo executing` only |
| **`mm.skill.execute` activity** | ✅ Implemented | `TemporalSkillActivities.mm_skill_execute` is wired up |
| **`SkillActivityDispatcher`** | ✅ Implemented | Routing by activity type and skill name/version |
| **`SkillInvocation` contract** | ✅ Implemented | Validated dataclass in `skill_plan_contracts.py` |
| **Activity catalog** | ✅ Implemented | 18 activities, 5 fleets, correct routing |
| **Worker runtime bindings** | ✅ Implemented | `build_activity_bindings` wires handlers to catalog |
| **Temporal↔DB state sync** | ✅ Implemented | Projection sync from Temporal visibility |
| **Batch-PR-resolver payload** | ✅ Fixed | Now sends `skill.name` + `skill.version` + `inputs` |
| **Integration polling** (Jules) | ✅ Implemented | Durable waiting loop with status polling |
| **Pause/Resume/Cancel signals** | ✅ Implemented | Workflow signal handlers |
| **`plan.validate` activity** | ✅ Implemented | Registry-aware plan validation |

---

## 5. Remaining Work — Tasks and Acceptance Criteria

### Task 1: Wire `_run_execution_stage` to `mm.skill.execute`

**The critical missing piece.** Replace the `echo executing` stub with real
skill dispatch.

**Implementation:**
1. Read the plan from `plan_ref` (via `artifact.read` activity)
2. Parse plan nodes as `SkillInvocation` objects
3. For each node, call `workflow.execute_activity("mm.skill.execute", ...)`
4. Handle node-level success/failure and aggregate results

**Acceptance criteria:**
- [ ] `_run_execution_stage` reads and parses the plan artifact
- [ ] Each plan node is dispatched as an `mm.skill.execute` activity call
- [ ] Activity routing uses `TemporalActivityCatalog.resolve_skill()` for correct
      task queue and timeout selection
- [ ] `SkillResult` from each node is captured and logged
- [ ] Plan-level failure policy (`FAIL_FAST` vs `CONTINUE`) is honored
- [ ] Node dependency edges from `plan.edges` are respected (sequential ordering)
- [ ] Workflow memo is updated with execution progress

### Task 2: Handle generic LLM instruction execution (no explicit skill)

When a task has `instructions` but no specific `skill.name` (or `skill.name = "auto"`),
the system should route to a generic LLM execution path.

**Implementation:**
1. `plan.generate` activity produces a plan node with `skill.name = "auto"`
2. The dispatcher routes `"auto"` to a generic LLM handler
3. The handler invokes the appropriate CLI (Codex/Gemini/Claude) based on
   `targetRuntime` from `initial_parameters`

**Acceptance criteria:**
- [ ] Tasks with only `instructions` (no explicit skill) produce a valid plan
- [ ] The `"auto"` skill handler invokes the correct runtime CLI
- [ ] `targetRuntime`, `model`, and `effort` are forwarded to the CLI invocation
- [ ] Sandbox activities (`checkout_repo`, `run_command`) are used for actual
      code execution within the handler
- [ ] Execution outputs (patches, logs) are stored as artifacts

### Task 3: Skill registry availability at runtime

The `mm.skill.execute` handler requires a `SkillRegistrySnapshot` to resolve
skill definitions, timeouts, and retry policies.

**Acceptance criteria:**
- [ ] A registry snapshot artifact is created during workflow initialization
      or planning
- [ ] The snapshot ref is passed through to execution-stage activity calls
- [ ] Skills not found in the registry produce a clear `ContractValidationError`

### Task 4: End-to-end acceptance test

**Acceptance criteria:**
- [ ] A test submits a `batch-pr-resolver` style payload via `POST /api/queue/jobs`
- [ ] The `MoonMind.Run` workflow starts on Temporal
- [ ] `plan.generate` produces a valid plan with a `pr-resolver` skill node
- [ ] `mm.skill.execute` dispatches the `pr-resolver` skill
- [ ] The skill handler executes (even if mocked) and returns a `SkillResult`
- [ ] Workflow reaches `succeeded` state
- [ ] Temporal↔DB sync reflects the final status in the dashboard

### Task 5: Artifact linkage for execution outputs

**Acceptance criteria:**
- [ ] Skill execution outputs are stored as artifacts linked to the workflow
- [ ] Logs, patches, and command outputs are captured via `artifact.write_complete`
- [ ] Artifact refs are stored in workflow memo for UI access
- [ ] `artifact.list_for_execution` returns all artifacts for a given workflow ID

### Task 6: Error handling and retry semantics

**Acceptance criteria:**
- [ ] `SkillFailure` exceptions are caught and mapped to `SkillResult.status = "FAILED"`
- [ ] Retryable failures respect the retry policy from the skill definition
- [ ] Non-retryable error codes (`INVALID_INPUT`, `PERMISSION_DENIED`, etc.)
      are propagated without retry
- [ ] Workflow transitions to `failed` state with an error summary on
      unrecoverable failure

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
│   ┌─────────────────────────────────────────────────────────────┐         │
│   │ initialize ──► plan ──► execute ──► finalize                │         │
│   │                  │          │                                │         │
│   │                  │          │                                │         │
│   │    ┌─────────────┘          └──────────────┐                │         │
│   │    ▼                                       ▼                │         │
│   │  plan.generate              mm.skill.execute (per node)     │         │
│   │  (mm.activity.llm)          (routed by capability)          │         │
│   │                                       │                     │         │
│   │                              ┌────────┼────────┐            │         │
│   │                              ▼        ▼        ▼            │         │
│   │                           sandbox    llm   integrations     │         │
│   │                           fleet     fleet     fleet         │         │
│   └─────────────────────────────────────────────────────────────┘         │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```
