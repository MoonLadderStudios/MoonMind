# MoonMind Workflow Language Hard Switch Plan

**Status:** Proposed hard-switch implementation plan  
**Owner:** MoonMind Platform / Mission Control / Runtime  
**Scope:** Remove MoonMind's product-level `task` terminology and replace it with Temporal-aligned workflow terminology. Replace `Step Attempt` with `Step Execution`.  
**Non-goal:** Preserve legacy task routes, names, pipelines, or compatibility payloads.

---

## 1. Executive Decision

MoonMind will no longer define a first-class product/runtime entity named **Task**.

The canonical top-level entity is:

```text
Workflow Execution
```

The canonical product identity is:

```text
workflowId
```

The canonical current run identity is:

```text
runId
```

The canonical orchestration category is:

```text
workflowType
```

The canonical nested work model is:

```text
Workflow Execution
  -> Step
      -> Step Execution
          -> Activity / Child Workflow Execution / AgentRun / runtime operation
```

In UI copy, **Workflow** may be used as shorthand for **Workflow Execution** when the context is clear. In APIs, schemas, docs, and code, prefer the exact term **Workflow Execution**.

The word **Task** is reserved for explicitly qualified external or Temporal-internal concepts:

```text
Temporal Task
Temporal Workflow Task
Temporal Activity Task
Temporal Task Queue
Jira task
Codex provider task
```

MoonMind-owned product work must not be called an unqualified `task`.

---

## 2. Motivation

The current `MoonMind task` term conflicts with too many other active concepts:

```text
Temporal Task
Temporal Workflow Task
Temporal Activity Task
Temporal Task Queue
Jira task
Codex task
MoonMind task
```

The existing MoonMind docs already show the desired direction:

- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` states that once work is represented inside Temporal, it is treated as a **Workflow Execution**.
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md` states that Workflow Executions orchestrate, Activities perform side effects, and Task Queues are internal routing labels rather than product semantics.
- `docs/Temporal/TaskExecutionCompatibilityModel.md` exists as a bridge from task-oriented surfaces to Temporal-backed Workflow Executions. This plan removes that bridge rather than preserving it.
- `docs/UI/MissionControlArchitecture.md` currently frames Mission Control as a task console backed by Temporal. This plan replaces that posture with a Workflow Execution console.
- `docs/Steps/StepAttemptsAndCheckpointing.md` defines Step Attempt as one semantic execution of a logical step. This plan renames that concept to **Step Execution**.

The hard switch reduces ambiguity, makes MoonMind feel Temporal-native, and prevents future APIs, docs, and UI flows from reintroducing parallel task semantics.

---

## 3. Guiding Principles

1. **Workflow Execution is the product entity.** There is no separate MoonMind Task object.
2. **Workflow ID is the stable product handle.** Use `workflowId` everywhere as the route key, API key, and durable identity.
3. **Run ID is the current Temporal run instance.** Use `runId` for the current/latest run. Do not route primary product pages by `runId`.
4. **Workflow Type is the root orchestration category.** Do not introduce provider-specific workflow categories such as `Codex workflow` or `Gemini workflow`.
5. **Step remains MoonMind's user-facing unit of work.** A Step is not a Temporal Activity and not a Temporal Task.
6. **Step Execution replaces Step Attempt.** One semantic execution of a logical Step is a Step Execution.
7. **Activity remains a Temporal implementation concept.** Activities perform side effects and nondeterministic work.
8. **Task Queue remains internal routing plumbing.** Never present Task Queues as product-level queues.
9. **No legacy aliases.** Do not preserve `taskId`, `/tasks/*`, task dashboards, or task-shaped payloads.
10. **External tasks must be qualified.** It is acceptable to say `Jira task` or `Codex provider task`, but not `MoonMind task`.

---

## 4. Canonical Glossary

| Term | Meaning |
| --- | --- |
| **Workflow** | UI shorthand for a MoonMind Workflow Execution when ambiguity is low. |
| **Workflow Execution** | The top-level MoonMind product/runtime entity. A durable Temporal-backed execution identified by `workflowId`. |
| **Workflow Type** | The root orchestration category, such as `MoonMind.UserWorkflow`, `MoonMind.AgentRun`, or `MoonMind.ManifestIngest`. |
| **Workflow Entry** | A short, URL-safe slug representing a `Workflow Type` for API payloads and routing (for example `user_workflow` for `MoonMind.UserWorkflow`). Surfaced as the `entry` field on canonical responses. |
| **Workflow ID** | The stable product identity and route key. Preserved across Continue-As-New. |
| **Run ID** | The current/latest Temporal run instance for a Workflow Execution. Useful for debugging, artifacts, and run history. |
| **Latest Run** | The current run for a Workflow Execution. The default detail view follows it automatically. |
| **Step** | A user-visible unit of work inside a Workflow Execution. |
| **Step Type** | The authoring discriminator for a Step: `tool`, `skill`, or `preset`. |
| **Step Execution** | One semantic execution of a logical Step, scoped to `(workflowId, runId, logicalStepId, executionOrdinal)`. |
| **Retry** | A low-level retry of an Activity, provider call, or idempotent operation inside the same Step Execution. |
| **Step Re-execution** | A new Step Execution for the same logical Step. |
| **Checkpoint** | Durable evidence sufficient to restore or validate state at a step boundary. |
| **Recover from Failed Step** | A product recovery action that validates checkpointed state and creates a new Step Execution at the failed Step. |
| **Activity** | Temporal implementation primitive for side-effecting or nondeterministic work. |
| **Tool** | A typed, schema-backed, policy-checked operation MoonMind can run directly. |
| **Skill** | Agent-facing reusable behavior, instruction bundle, execution mode, or operating procedure. |
| **Preset** | Authoring-time reusable composition that expands into executable Steps. |
| **Task** | Reserved for Temporal internals and explicitly qualified external systems only. |

---

## 5. Terms to Remove

Remove these as MoonMind terms:

```text
MoonMind task
task
taskId
task_id
TaskId
taskRunId
task_run_id
TaskRun
task detail
task list
task status
task source
task queue
task create
task payload
task route
task dashboard
task lifecycle
task-oriented
task-first
task-compatible
```

Replace them with:

```text
Workflow Execution
workflow
workflowId
workflow_id
WorkflowId
runId
run_id
WorkflowRun only when explicitly referring to a Temporal run-history surface
workflow detail
workflow list
workflow state
workflow entry (the URL-safe slug for workflow type; see Glossary)
workflow start
workflow input
workflow route
workflow console
workflow lifecycle
workflow-native
workflow-first
workflow execution payload
```

Allowed qualified uses:

```text
Temporal Task
Workflow Task
Activity Task
Task Queue
Jira task
Codex provider task
```

---

## 6. Target Product Model

### 6.1 Entity Model

```text
Workflow Execution
  workflowId: stable product identity
  runId: current/latest Temporal run
  workflowType: root orchestration category
  state: MoonMind domain state
  temporalStatus: raw Temporal runtime/close status
  closeStatus: raw Temporal close status when terminal
  title: display title
  summary: display summary
  ownerType: user | system | service
  ownerId: owning principal
  searchAttributes: indexed list/filter metadata
  memo: compact human/debug metadata
  steps: latest-run step ledger
  artifacts: evidence and outputs
  externalRefs: Jira, GitHub, Codex, provider, or other external references
```

### 6.2 Identity Rules

Use this everywhere:

```text
workflowId = product identity
runId      = current/latest run instance
```

Do not keep:

```text
taskId
taskId == workflowId
temporalRunId
```

If a client needs the current Temporal run, return:

```json
{
  "workflowId": "mm:01J...",
  "runId": "temporal-run-id"
}
```

Do not return:

```json
{
  "taskId": "mm:01J...",
  "temporalRunId": "temporal-run-id"
}
```

### 6.3 External Reference Model

Do not make Jira, Codex, GitHub, or provider IDs part of MoonMind identity. Store them as external references:

```json
{
  "workflowId": "mm:01J...",
  "externalRefs": [
    {
      "system": "jira",
      "type": "issue",
      "id": "MM-123"
    },
    {
      "system": "codex",
      "type": "provider_task",
      "id": "codex-task-456"
    },
    {
      "system": "github",
      "type": "pull_request",
      "id": "1234"
    }
  ]
}
```

Preferred copy:

```text
Workflow started from Jira issue MM-123.
Workflow launched Codex provider task codex-task-456.
Workflow is waiting on an external callback.
```

---

## 7. Workflow Type Catalog Changes

### 7.1 Rename the Standard User Workflow Type

Current:

```text
MoonMind.Run
```

Recommended replacement:

```text
MoonMind.UserWorkflow
```

Rationale:

- `MoonMind.Run` collides conceptually with `runId`.
- `MoonMind.Workflow` is readable but awkward in exact docs: `Workflow Execution of Workflow Type MoonMind.Workflow`.
- `MoonMind.UserWorkflow` makes clear that this is the user-submitted, step-ledger-owning workflow type.

### 7.2 Target Catalog

| Workflow Type | Status | Meaning |
| --- | --- | --- |
| `MoonMind.UserWorkflow` | Rename from `MoonMind.Run` | User-requested, Step-ledger-owning Workflow Execution. |
| `MoonMind.ManifestIngest` | Keep | Manifest ingest, validation, compilation, and orchestration. |
| `MoonMind.AgentRun` | Keep | Durable lifecycle wrapper for one true managed or external agent execution. |
| `MoonMind.AgentSession` | Keep | Managed runtime session workflow. |
| `MoonMind.ManagedSessionReconcile` | Keep | Internal managed-session reconciliation workflow. |
| `MoonMind.ProviderProfileManager` | Keep | Internal provider-profile coordination workflow. |
| `MoonMind.OAuthSession` | Keep | Auth/session support workflow. |
| `MoonMind.MergeAutomation` | Keep | PR readiness and resolver orchestration workflow. |

---

## 8. API Contract

### 8.1 Canonical Endpoints

Use:

```text
GET    /api/executions
POST   /api/executions
GET    /api/executions/{workflowId}
POST   /api/executions/{workflowId}/update
POST   /api/executions/{workflowId}/signal
POST   /api/executions/{workflowId}/cancel
GET    /api/executions/{workflowId}/steps
GET    /api/executions/{workflowId}/artifacts
POST   /api/executions/{workflowId}/recover-from-failed-step
GET    /api/executions/{workflowId}/runs       # optional future/history surface
```

Delete:

```text
/tasks/*
/api/tasks/*
/api/task-runs/*
task_dashboard routes
task create endpoints
task detail endpoints
task compatibility routes
```

If `/api/task-runs/*` currently means managed runtime observability, rename it to one of:

```text
/api/agent-runs/*
/api/runtime-runs/*
/api/executions/{workflowId}/agent-runs/*
```

### 8.2 Canonical List Response

```json
{
  "items": [
    {
      "workflowId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
      "runId": "temporal-run-id",
      "workflowType": "MoonMind.UserWorkflow",
      "entry": "user_workflow",
      "title": "Implement MM-123",
      "summary": "Running implementation step",
      "state": "executing",
      "temporalStatus": "running",
      "ownerType": "user",
      "ownerId": "user-id",
      "createdAt": "2026-05-17T12:00:00Z",
      "updatedAt": "2026-05-17T12:05:00Z",
      "closedAt": null,
      "progress": {
        "total": 6,
        "pending": 2,
        "running": 1,
        "succeeded": 3,
        "failed": 0,
        "canceled": 0,
        "skipped": 0
      },
      "links": {
        "self": "/api/executions/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
        "steps": "/api/executions/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB/steps",
        "ui": "/workflows/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB"
      }
    }
  ],
  "nextPageToken": null
}
```

### 8.3 Canonical Detail Response

```json
{
  "workflowId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
  "runId": "temporal-run-id",
  "workflowType": "MoonMind.UserWorkflow",
  "entry": "user_workflow",
  "title": "Implement MM-123",
  "summary": "Running implementation step",
  "state": "executing",
  "temporalStatus": "running",
  "closeStatus": null,
  "ownerType": "user",
  "ownerId": "user-id",
  "waitingReason": null,
  "attentionRequired": false,
  "createdAt": "2026-05-17T12:00:00Z",
  "updatedAt": "2026-05-17T12:05:00Z",
  "closedAt": null,
  "progress": {
    "total": 6,
    "pending": 2,
    "running": 1,
    "succeeded": 3,
    "failed": 0,
    "canceled": 0,
    "skipped": 0,
    "currentStepTitle": "Run tests"
  },
  "inputRef": "art_input",
  "planRef": "art_plan",
  "artifactRefs": [],
  "actions": {
    "updateInputs": true,
    "setTitle": true,
    "requestNewRun": true,
    "pauseWorkflow": true,
    "resumePausedWorkflow": false,
    "recoverFromFailedStep": false,
    "cancelWorkflow": true
  },
  "debug": {
    "namespace": "moonmind",
    "workflowId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
    "runId": "temporal-run-id",
    "workflowType": "MoonMind.UserWorkflow"
  }
}
```

### 8.4 Fields to Remove from Schemas

Delete all fields named:

```text
taskId
task_id
taskRunId
task_run_id
mm_task_run_id
taskStatus
dashboardTaskStatus
taskSource
taskType
taskPayload
taskHref
```

Replace with:

```text
workflowId
workflow_id
runId
run_id
workflowState
state
workflowType
entry
workflowInput
detailHref
```

---

## 9. UI Route Model

### 9.1 New Routes

Use:

```text
/workflows
/workflows/new
/workflows/{workflowId}
/workflows/{workflowId}/steps
/workflows/{workflowId}/artifacts
/workflows/{workflowId}/runs
/proposals
/schedules
```

Delete:

```text
/tasks/list
/tasks/new
/tasks/queue/new
/tasks/{taskId}
```

### 9.2 No Redirects

Because this is a hard switch, do not preserve legacy route redirects.

```text
/tasks/* should be removed from the route table.
```

Old routes may return a normal 404 or hard-switch error page, but they must not route into the new workflow console.

### 9.3 Navigation and Copy

Replace:

```text
Tasks
Create Task
New Task
Task Detail
Task Status
Rerun Task
Resume Task
Cancel Task
Task Inputs
Task Artifacts
Task Steps
```

With:

```text
Workflows
Start Workflow
Start Workflow
Workflow Detail
Workflow State
Start New Run
Recover from Failed Step / Resume Paused Workflow
Cancel Workflow
Workflow Inputs
Workflow Artifacts
Workflow Steps
```

---

## 10. Frontend Rename Plan

Rename:

```text
frontend/src/entrypoints/task-create.tsx
  -> frontend/src/entrypoints/workflow-start.tsx

frontend/src/entrypoints/task-detail.tsx
  -> frontend/src/entrypoints/workflow-detail.tsx

frontend/src/entrypoints/task-detail.test.tsx
  -> frontend/src/entrypoints/workflow-detail.test.tsx

api_service/static/task_dashboard/dist/
  -> api_service/static/workflow_console/dist/

api_service/api/routers/task_dashboard.py
  -> api_service/api/routers/workflow_console.py

api_service/api/routers/task_dashboard_view_model.py
  -> api_service/api/routers/workflow_console_view_model.py
```

Replace component/CSS names:

```text
TaskDashboard -> WorkflowConsole
TaskDetail    -> WorkflowDetail
TaskCreate    -> WorkflowStart
TaskList      -> WorkflowList
task-dashboard -> workflow-console
task-detail    -> workflow-detail
task-create    -> workflow-start
```

---

## 11. Backend Service Rename Plan

Rename:

```text
TaskService          -> WorkflowExecutionService
TaskDashboardService -> WorkflowConsoleService
TaskCreateService    -> WorkflowStartService
TaskDetailService    -> WorkflowDetailService
TaskArtifactService  -> WorkflowArtifactService
TaskStepService      -> WorkflowStepService
```

Keep:

```text
TemporalExecutionService
```

if it already operates over `workflowId` and execution semantics.

Replace method names:

```text
create_task() -> start_workflow()
get_task()    -> get_execution()
list_tasks()  -> list_executions()
cancel_task() -> cancel_workflow()
rerun_task()  -> request_new_run()
resume_task() -> recover_from_failed_step()
```

Replace event names:

```text
task.created         -> workflow_execution.started
task.updated         -> workflow_execution.updated
task.completed       -> workflow_execution.completed
task.failed          -> workflow_execution.failed
task.canceled        -> workflow_execution.canceled
task.rerun_requested -> workflow_execution.new_run_requested
```

---

## 12. Step Execution Model

### 12.1 Decision

Replace **Step Attempt** with **Step Execution**.

The current model defines Step Attempt as one semantic execution of a logical Step. That is exactly the concept that should now be named **Step Execution**.

The new hierarchy is:

```text
Workflow Execution
  -> Step
      -> Step Execution
          -> Activity / Child Workflow Execution / AgentRun
```

### 12.2 Why Step Execution Is Better

**Step Attempt** sounds like a retry/failure concept. It makes a successful first pass sound like `Attempt 1 succeeded`.

**Step Execution** is neutral and aligns with **Workflow Execution**. It says one execution of a logical Step occurred, regardless of whether it succeeded, failed, was superseded, or was recovered from checkpoint.

### 12.3 Canonical Distinctions

```text
Step Execution
  One semantic execution of a logical Step.

Retry
  A low-level retry of an Activity, provider call, or idempotent operation
  inside the same Step Execution.

Step Re-execution
  A new Step Execution for the same logical Step.

Recover from Failed Step
  A workflow recovery action that restores checkpointed state and creates a new
  Step Execution at the failed logical Step.
```

### 12.4 Identity

Old key:

```text
(workflowId, runId, logicalStepId, attempt)
```

New key:

```text
(workflowId, runId, logicalStepId, executionOrdinal)
```

Recommended JSON:

```json
{
  "stepExecutionId": "mm:abc:run-1:implement-story-S004:execution:3",
  "workflowId": "mm:abc",
  "runId": "temporal-run-id",
  "logicalStepId": "implement-story-S004",
  "executionOrdinal": 3,
  "reason": "quality_gate_failed",
  "status": "succeeded"
}
```

### 12.5 Document Rename

Rename:

```text
docs/Steps/StepAttemptsAndCheckpointing.md
```

To:

```text
docs/Steps/StepExecutionsAndCheckpointing.md
```

New title:

```text
# Step Executions and Checkpointing
```

### 12.6 Schema Rename Map

```text
StepAttemptManifestModel        -> StepExecutionManifestModel
StepAttemptIdentityModel        -> StepExecutionIdentityModel
StepAttemptLineageModel         -> StepExecutionLineageModel
StepAttemptSummaryRefModel      -> StepExecutionSummaryRefModel
StepAttemptReason               -> StepExecutionReason
StepAttemptStatus               -> StepExecutionStatus
StepAttemptTerminalDisposition  -> StepExecutionTerminalDisposition
StepAttemptSemanticOperation    -> StepExecutionSemanticOperation
```

### 12.7 Field Rename Map

```text
stepAttemptId        -> stepExecutionId
attempt              -> executionOrdinal
attemptScope         -> executionScope
attemptOrdinalInLoop -> executionOrdinalInLoop
remainingAttempts    -> remainingExecutions or remainingReexecutions
sourceAttempt        -> sourceExecutionOrdinal
lineageAttemptOrdinal -> lineageExecutionOrdinal
```

### 12.8 Content Type Rename Map

```text
application/vnd.moonmind.step-attempt+json;version=1
  -> application/vnd.moonmind.step-execution+json;version=1

application/vnd.moonmind.step-attempt-checkpoint+json;version=1
  -> application/vnd.moonmind.step-execution-checkpoint+json;version=1

application/vnd.moonmind.step-attempt-context+json;version=1
  -> application/vnd.moonmind.step-execution-context+json;version=1
```

### 12.9 Step Execution Reason Values

Use:

```text
initial_execution
quality_gate_failed
tests_failed
runtime_recovered
recover_from_failed_step
remediation_context
operator_requested
dependency_invalidated
policy_revalidation
```

Replace:

```text
resume_from_failed_step
```

With:

```text
recover_from_failed_step
```

### 12.10 Workspace Policy Rename

Replace:

```text
continue_from_previous_attempt
restore_pre_attempt
apply_previous_diff_to_clean_baseline
```

With:

```text
continue_from_previous_execution
restore_pre_execution
apply_previous_execution_diff_to_clean_baseline
```

Keep:

```text
start_from_last_passed_commit
fresh_branch_from_source
```

---

## 13. Recovery and New Run Behavior

### 13.1 Request New Run

Rename:

```text
RequestRerun
```

To:

```text
RequestNewRun
```

Behavior:

```text
RequestNewRun:
  - preserves workflowId
  - generates a new runId
  - clears terminal/transient run-local state where allowed
  - preserves workflow-level metadata
  - preserves or explicitly replaces input refs
  - records a new-run event/summary
```

Rename:

```text
rerun_count
```

To:

```text
continue_as_new_count
```

or:

```text
new_run_count
```

Prefer `continue_as_new_count` if the counter counts automatic Continue-As-New transitions, not only user-requested new runs.

### 13.2 Recover from Failed Step

Rename:

```text
Resume
resume-from-failed-step
failed-step Resume
```

To:

```text
RecoverFromFailedStep
recover-from-failed-step
failed-step recovery
```

Endpoint:

```text
POST /api/executions/{workflowId}/recover-from-failed-step
```

Behavior:

```text
RecoverFromFailedStep:
  - validates source workflowId and runId
  - requires source execution to be recovery-eligible
  - identifies failed logicalStepId
  - validates workflow input snapshot
  - validates plan ref/digest
  - validates recovery checkpoint
  - creates a linked follow-up Workflow Execution unless an explicit in-place model is designed
  - materializes preserved prior Steps as provenance
  - creates a new Step Execution at the failed Step
  - never silently degrades into a full new run
```

### 13.3 Pause / Resume

Use plain `resume` only for paused workflows:

```text
PauseWorkflow
ResumePausedWorkflow
```

Do not use `Resume` for failed-step recovery.

---

## 14. Database and Projection Migration

### 14.1 Table Renames

Rename:

```text
tasks                 -> workflow_executions
task_steps            -> workflow_steps
task_artifacts        -> workflow_artifacts
task_runs             -> agent_runs
task_dependencies     -> workflow_dependencies
```

The canonical step table name is `workflow_steps` (consistent with `workflow_executions`); do not introduce a parallel `execution_steps` table. The historical `task_runs` table tracks managed-agent runtime invocations and becomes `agent_runs`; if any installation has used the table for a different meaning, archive that data separately rather than reintroducing alternative names.

### 14.2 Column Renames

Rename:

```text
task_id         -> workflow_id
task_run_id     -> agent_run_id (managed-agent runtime invocations); use run_id only where the column already referred to the Temporal run identifier
task_status     -> state
task_title      -> title
task_summary    -> summary
task_created_at -> created_at or started_at
task_updated_at -> updated_at
```

### 14.3 Index and FK Renames

Rename:

```text
idx_tasks_task_id        -> idx_workflow_executions_workflow_id
fk_task_steps_task_id    -> fk_workflow_steps_workflow_id
```

### 14.4 Data Semantics

Do not create a second Workflow row from an old Task row. The old row becomes the Workflow Execution row.

If existing rows do not have valid Temporal workflow IDs, choose one hard-switch behavior:

1. Backfill `workflow_id` and `run_id` from `TemporalExecutionRecord`.
2. Archive the row outside new workflow execution tables.
3. Fail migration and require cleanup.

Do not keep mixed-source compatibility rows.

---

## 15. Search Attributes, Memo, and Projection Names

### 15.1 Keep

```text
mm_owner_id
mm_owner_type
mm_state
mm_updated_at
mm_entry
mm_repo
mm_integration
```

### 15.2 Remove

```text
mm_task_run_id
taskRunId
task_run_id
taskId
```

### 15.3 Entry Values

Replace:

```text
mm_entry = "run"
```

With:

```text
mm_entry = "user_workflow"
```

Recommended values:

```text
user_workflow
manifest
agent_run
agent_session
provider_profile_manager
managed_session_reconcile
oauth_session
merge_automation
```

---

## 16. Documentation Migration

### 16.1 Delete or Replace

| Current doc | Hard-switch action |
| --- | --- |
| `docs/Temporal/TaskExecutionCompatibilityModel.md` | Delete or replace with `docs/Temporal/WorkflowExecutionProductModel.md`. Do not keep as normative. |
| `docs/UI/MissionControlArchitecture.md` | Rewrite as `docs/UI/WorkflowConsoleArchitecture.md`. |
| `docs/Temporal/RunHistoryAndRerunSemantics.md` | Rename to `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`. |
| `docs/Steps/StepAttemptsAndCheckpointing.md` | Rename to `docs/Steps/StepExecutionsAndCheckpointing.md`. |
| `docs/Tasks/TaskArchitecture.md` | Delete or replace with `docs/Temporal/WorkflowExecutionArchitecture.md`. |
| `docs/Tasks/TaskPresetsSystem.md` | Rename to `docs/Steps/WorkflowPresetsSystem.md` or fold into Step/Preset docs. |
| `docs/Tasks/TaskRemediation.md` | Rename to `docs/Temporal/WorkflowRemediation.md` or `docs/Steps/FailedStepRecovery.md`. |

### 16.2 New Primary Docs

Create or update:

```text
docs/Temporal/WorkflowExecutionProductModel.md
docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md
docs/UI/WorkflowConsoleArchitecture.md
docs/Steps/StepTypes.md
docs/Steps/StepExecutionsAndCheckpointing.md
docs/Temporal/WorkflowTypeCatalogAndLifecycle.md
docs/Temporal/ActivityCatalogAndWorkerTopology.md
```

### 16.3 Standard Statement for New Docs

Add this to the product model doc:

```text
MoonMind does not define a separate product entity named Task.

A user-submitted unit of work is a Workflow Execution. In informal UI copy, it may be called a Workflow. The exact API and documentation term is Workflow Execution.

The term Task is reserved for Temporal Tasks, Temporal Task Queues, and explicitly qualified external systems such as Jira tasks or Codex provider tasks.
```

---

## 17. Testing Plan

### 17.1 Rename Tests

Rename:

```text
test_task_create_submit_browser.py
  -> test_workflow_start_submit_browser.py

test_task_detail*
  -> test_workflow_detail*

test_temporal_execution_api.py
  -> keep, but update payload expectations
```

### 17.2 Contract Tests

Add assertions that API responses do not contain:

```text
taskId
taskRunId
taskStatus
detailHref: /tasks/...
stepAttemptId
attemptScope
```

Add assertions that API responses contain:

```text
workflowId
runId
workflowType
state
links.ui: /workflows/{workflowId}
stepExecutionId
executionOrdinal
executionScope
```

### 17.3 UI Tests

Assert visible copy includes:

```text
Start Workflow
Workflows
Workflow ID
Current Run ID
Workflow Type
Step Execution
```

Assert visible copy excludes:

```text
Task ID
Create Task
Task Detail
Step Attempt
```

### 17.4 Documentation Tests

Add docs linting for unqualified `task`.

Allowed:

```text
Temporal Task
Workflow Task
Activity Task
Task Queue
Jira task
Codex task
```

Forbidden:

```text
MoonMind task
task detail
create task
task status
task-oriented
task-first
task-compatible
```

### 17.5 Schema Tests

Add schema tests that fail on:

```text
taskId
taskRunId
mm_task_run_id
stepAttemptId
attempt
```

where `attempt` refers to MoonMind Step Execution identity rather than low-level retry metadata.

---

## 18. Enforcement

### 18.1 Banned-Term CI Check

Add a repository lint check for identifier-shaped tokens that are unambiguous when matched literally:

```text
taskId
task_id
taskRunId
task_run_id
TaskDashboard
task_dashboard
TaskDetail
task-detail
task-create
MoonMind task
task-oriented
task-first
StepAttempt
stepAttemptId
step-attempt
```

The route-shaped banned terms `/tasks` and `/api/tasks` MUST be scoped, not matched against the entire repository tree. Scope the check to:

1. Runtime route definitions: FastAPI/Pydantic route decorators, OpenAPI path strings, frontend router config, and reverse-proxy / nginx route maps.
2. Runtime response schemas and link fields that publish URLs (for example `links.self`, `links.ui`, OpenAPI `paths`, generated client URL builders).

The check MUST NOT fail on:

- Banned-term lint rule documentation itself (this section is a built-in allowlisted location).
- Historical examples, migration notes, and changelogs under `docs/**` and `specs/**`.
- Tests/fixtures that explicitly assert the absence or removal of the legacy routes.
- Strings inside repo-internal code comments that quote the legacy path for context only.

Implementations should provide an explicit per-rule allowlist (file globs or context predicates) and fail closed on new occurrences outside those locations, so the gate stays actionable.

### 18.2 Allowlist

Allow only qualified or third-party use:

```text
Temporal Task
Workflow Task
Activity Task
Task Queue
Jira task
Codex task
```

Allow code that references upstream APIs if the upstream field really is named task, but require a qualified wrapper name in MoonMind code:

```text
jiraTaskId
codexTaskId
temporalTaskQueue
```

Do not allow:

```text
taskId
```

for MoonMind identity.

---

## 19. Cutover Sequence

Because this is a hard switch, execute as one coordinated branch/release.

### Phase 1: Freeze Old Naming

Stop feature work that introduces:

```text
taskId
/tasks
TaskService
task dashboard
StepAttempt
```

### Phase 2: Update Docs First

Create/replace canonical docs:

```text
WorkflowExecutionProductModel.md
WorkflowConsoleArchitecture.md
WorkflowRunHistoryAndNewRunSemantics.md
StepExecutionsAndCheckpointing.md
```

Delete/de-norm task compatibility docs.

### Phase 3: Rename API Schemas

Update Pydantic/OpenAPI models to use:

```text
workflowId
runId
workflowType
state
entry
progress
stepExecutionId
executionOrdinal
```

Remove task alias fields and Step Attempt fields.

Regenerate frontend OpenAPI types.

#### Phase 3.1: In-flight Workflow Compatibility

Removing task-shaped and Step Attempt fields from API schemas, workflow update/signal payloads, and serialized activity inputs is a non-additive Temporal contract change. Already-running workflow histories and any signal/update messages enqueued before the cutover are deserialized against the prior shapes, so a bare deletion will break in-flight runs on the new worker code.

Before any field deletion ships, this hard switch requires an explicit versioned cutover plan covering:

1. **Workflow version boundary.** Identify each affected workflow and gate the new payload shapes behind `workflow.patched` / `GetVersion` markers, or a `MoonMind.UserWorkflow.v2` workflow type, so that pre-cutover histories continue to replay on the old branch while new runs use the renamed contracts.
2. **Worker/task-queue split.** Run the previous worker build on its existing Task Queue until in-flight runs drain, and route new starts to a new Task Queue served by the renamed-contract worker. Do not allow a single worker build to serve both shapes silently.
3. **Drain or terminate plan.** For each environment, document whether in-flight runs are drained to completion, paused and resumed on the new branch, or explicitly terminated and restarted. Hard-switch installs must record the chosen option per environment before deploy.
4. **Activity/signal/update payload coverage.** Apply the same versioning to activity input/output payloads, signal names/shapes, and update names/shapes whose fields are being renamed or removed.

This is consistent with the constitution's Temporal-facing contract rules in `.specify/memory/constitution.md`: workflow/activity/update/signal payload shapes are compatibility-sensitive, and any non-additive change must preserve worker-bound invocation compatibility for in-flight runs or be versioned with an explicit migration/cutover plan. The Phase 10 release notes MUST link to the per-environment cutover record before the breaking release is published.

### Phase 4: Rename Backend Services and Routes

Remove:

```text
/tasks/*
/api/tasks/*
```

Keep:

```text
/api/executions/*
```

Add:

```text
/workflows/*
```

for UI routes.

### Phase 5: Rename Frontend

Rename entrypoints, route modules, static bundle paths, CSS namespaces, and visible copy.

### Phase 6: Rename Data Model

Run table, column, index, and foreign-key migrations.

### Phase 7: Rename Step Execution Artifacts

Update content types, manifest models, checkpoint models, artifact link metadata, and runtime refs.

### Phase 8: Rename Tests

Update unit, contract, integration, and e2e tests.

### Phase 9: Add Enforcement

Add banned-term lint checks and OpenAPI response assertions.

### Phase 10: Ship as Breaking Release

Release note:

```text
MoonMind no longer exposes Tasks as a product/runtime concept. Use Workflow Execution, workflowId, runId, and Step Execution.
```

Do not keep compatibility redirects or alias payloads.

---

## 20. Acceptance Criteria

The hard switch is complete when:

1. No MoonMind API response contains `taskId`, `taskRunId`, or unqualified task-shaped fields.
2. No MoonMind UI route starts with `/tasks`.
3. No primary navigation item says `Tasks`.
4. No docs describe MoonMind work as `task-oriented` or `task-first`.
5. `Workflow Execution` is the canonical product/runtime entity.
6. `workflowId` is the only product route key.
7. `runId` is the current/latest Temporal run identifier.
8. `MoonMind.Run` is replaced by `MoonMind.UserWorkflow`, or at minimum is no longer described as standard task execution.
9. Step docs say Workflow Executions are composed from Steps.
10. `Step Execution` fully replaces `Step Attempt`.
11. Step execution artifacts use `stepExecutionId`, `executionOrdinal`, and step-execution content types.
12. Failed-step recovery says `RecoverFromFailedStep`, not bare `Resume task`.
13. CI fails on reintroduction of unqualified MoonMind task terminology.
14. CI fails on reintroduction of Step Attempt terminology.
15. External systems are qualified: Jira task, Codex task, Temporal Task, Workflow Task, Activity Task, Task Queue.

---

## 21. Final Target Architecture

```text
MoonMind Workflow Console
  /workflows
  /workflows/new
  /workflows/{workflowId}

MoonMind Execution API
  /api/executions
  /api/executions/{workflowId}
  /api/executions/{workflowId}/steps
  /api/executions/{workflowId}/artifacts
  /api/executions/{workflowId}/update
  /api/executions/{workflowId}/signal
  /api/executions/{workflowId}/cancel
  /api/executions/{workflowId}/recover-from-failed-step

Workflow Execution
  workflowId
  runId
  workflowType
  state
  searchAttributes
  memo
  steps
  artifacts
  externalRefs

Steps
  Step Type: tool | skill | preset

Step Executions
  stepExecutionId
  executionOrdinal
  executionScope
  reason
  status
  terminalDisposition
  checkpoints
  evidence artifacts
  side-effect disposition

Execution internals
  Activities
  Child Workflow Executions
  AgentRun Workflow Executions
  Task Queues only as Temporal worker routing
```

The guiding sentence for the new docs should be:

```text
MoonMind work is represented as Temporal-backed Workflow Executions. MoonMind does not define a separate product entity named Task. The word Task is reserved for Temporal internals and explicitly qualified external systems. A logical Step may have one or more Step Executions; retries are low-level operations inside a Step Execution.
```
