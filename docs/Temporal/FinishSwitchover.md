# Finish Temporal Switchover

Status: Draft
Owners: MoonMind Engineering, MoonMind Platform
Last Updated: 2026-03-06

## 1. Purpose

This document defines the remaining work required to move MoonMind from a **Temporal-shaped staging implementation** to a **real Temporal-backed execution system** that Mission Control operators can use daily.

The main problem today is straightforward:

- Mission Control already contains substantial Temporal UI plumbing.
- `/api/executions` already exposes a broad execution API.
- Temporal services already exist in Docker Compose.
- But the runtime is still not fully switched over because the system is not yet consistently using Temporal as the authority for workflow start, lifecycle transitions, actions, and reads.

This document is written as an execution checklist. Each item is intended to be standalone enough that an engineer can implement it directly from the task description.

## 2. Current State Summary

The repository already includes:

- Temporal stack services in [docker-compose.yaml](/home/nsticco/MoonMind/docker-compose.yaml)
- worker fleet topology modeling in [workers.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/workers.py)
- artifact services in [artifacts.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/artifacts.py)
- activity handler implementations in [activity_runtime.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/activity_runtime.py)
- execution API endpoints in [executions.py](/home/nsticco/MoonMind/api_service/api/routers/executions.py)
- Mission Control Temporal source wiring in [task_dashboard_view_model.py](/home/nsticco/MoonMind/api_service/api/routers/task_dashboard_view_model.py), [task_dashboard.py](/home/nsticco/MoonMind/api_service/api/routers/task_dashboard.py), and [dashboard.js](/home/nsticco/MoonMind/api_service/static/task_dashboard/dashboard.js)

The repository does **not** yet consistently include:

- a checked-in Temporal worker runtime that polls queues by default
- real Temporal workflow definitions for `MoonMind.Run` and `MoonMind.ManifestIngest`
- service methods that use Temporal as the authority for create, update, signal, cancel, and list/detail behavior
- a proven end-to-end path from Mission Control submit to Temporal execution to operator action

## 3. Definition Of Done

MoonMind can be considered switched over to usable Temporal workflows only when all of the following are true:

- a developer can start the local stack and create a Temporal-backed task without ad hoc worker commands
- `/api/executions` starts and controls real Temporal workflows
- Temporal workers poll queues and execute workflow/activity code continuously
- Mission Control can list, view, submit, and act on Temporal-backed tasks
- local DB records act as projections or compatibility caches, not execution authority
- at least one end-to-end acceptance test proves the complete user path

## 4. Work Items

### 4.1 Implement the real Temporal worker runtime

**Goal**

Create the missing long-running worker process that connects to Temporal Server and polls the configured task queues for each fleet.

**Why this is needed**

Today [start-worker.sh](/home/nsticco/MoonMind/services/temporal/scripts/start-worker.sh) prints topology information using [workers.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/workers.py) and then only runs whatever is in `TEMPORAL_WORKER_COMMAND`. In Compose, that currently falls back to `sleep infinity`, which means the containers can start without actually doing any Temporal work.

**Implementation requirements**

- Add a checked-in Python entrypoint under `moonmind/workflows/temporal/` that:
- connects to `settings.temporal.address`
- uses `settings.temporal.namespace`
- determines the current fleet from `settings.temporal.worker_fleet`
- starts a `temporalio.worker.Worker` for that fleet
- registers workflows on the workflow fleet
- registers activities on non-workflow fleets
- uses the task queue names already defined in [settings.py](/home/nsticco/MoonMind/moonmind/config/settings.py) and [workers.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/workers.py)
- preserves the existing topology bootstrap output for observability, but changes the default startup path so a worker process is launched without custom overrides
- update [start-worker.sh](/home/nsticco/MoonMind/services/temporal/scripts/start-worker.sh) so the script remains useful for debugging but no longer defaults to an idle container in normal operation

**Acceptance tests**

- Starting `temporal-worker-workflow`, `temporal-worker-artifacts`, `temporal-worker-llm`, `temporal-worker-sandbox`, and `temporal-worker-integrations` creates active Temporal pollers for their configured task queues.
- Worker logs clearly identify fleet, namespace, task queues, and registered workflows or activities.
- Removing `TEMPORAL_WORKER_COMMAND` from `.env` does not cause workers to idle; they still run.
- Stopping and restarting a worker container does not require any manual in-container command to resume polling.

### 4.2 Create the real `MoonMind.Run` workflow

**Goal**

Implement the actual Temporal workflow definition for the standard MoonMind task execution lifecycle.

**Why this is needed**

The current system models workflow state transitions in [service.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/service.py), but that code is still functioning as an application-managed state machine. A real Temporal workflow must own state progression, wait behavior, and action handling.

**Implementation requirements**

- Add a workflow class registered under the workflow type `MoonMind.Run`
- define a stable workflow input model matching the execution API contract already accepted by [executions.py](/home/nsticco/MoonMind/api_service/api/routers/executions.py)
- support the lifecycle phases already reflected in compatibility state:
- `initializing`
- `planning`
- `executing`
- `awaiting_external`
- `finalizing`
- terminal success/failure/canceled outcomes
- invoke activities rather than embedding side effects inside the workflow
- avoid passing large blobs through workflow history; use artifact references instead
- expose or derive memo and search attributes needed by Mission Control:
- owner metadata
- workflow type
- entry type
- repository
- integration name
- compatibility state
- updated timestamp
- provide signal and update handlers needed later for pause, resume, approve, rerun, title updates, and parameter/input updates

**Acceptance tests**

- A `MoonMind.Run` workflow can be started using a stable `workflowId` and shows up in Temporal as a real execution.
- Workflow history reflects actual transitions through the expected phases.
- A workflow can complete successfully and close with Temporal completion status.
- A workflow can fail and close with a failure status that the API can map to Mission Control.
- Workflow memo/search attributes are sufficient for the existing execution serializer in [executions.py](/home/nsticco/MoonMind/api_service/api/routers/executions.py) to render a correct detail model.

### 4.3 Create the real `MoonMind.ManifestIngest` workflow

**Goal**

Implement the dedicated Temporal workflow for manifest ingest and child-run orchestration.

**Why this is needed**

Manifest logic already exists in [manifest_ingest.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/manifest_ingest.py), but it is not yet owned by a real Temporal workflow that can process updates, schedule child work, and emit durable status through Temporal.

**Implementation requirements**

- Add a workflow class registered under the workflow type `MoonMind.ManifestIngest`
- use the manifest planning and projection helpers already present in [manifest_ingest.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/manifest_ingest.py)
- support manifest-specific update operations already recognized by [service.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/service.py)
- generate manifest summary and run-index artifacts through the artifact service
- start child executions as real Temporal child workflows or real Temporal client starts, not by only writing local records
- preserve compatibility with existing API endpoints:
- `/api/executions/{workflowId}/manifest-status`
- `/api/executions/{workflowId}/manifest-nodes`

**Acceptance tests**

- Starting a manifest ingest execution creates a real `MoonMind.ManifestIngest` workflow.
- The workflow can compile a manifest and emit node status data consumable by the existing manifest endpoints.
- Child runs are visible as real Temporal executions with correct parent or lineage metadata.
- Manifest-specific updates change the live workflow behavior rather than only mutating local rows.
- Run index and manifest summary artifacts are linked to the execution and retrievable through the artifact APIs.

### 4.4 Add a real Temporal client layer and stop inventing execution IDs locally

**Goal**

Introduce a runtime-facing client layer that talks to Temporal Server for starts, describes, list operations, signals, updates, and cancellations.

**Why this is needed**

`TemporalExecutionService.create_execution()` in [service.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/service.py) currently creates `workflow_id` and `run_id` locally before any real Temporal interaction. That makes the local application record the authority, which is the opposite of the target architecture.

**Implementation requirements**

- Add a Temporal client adapter under `moonmind/workflows/temporal/`
- centralize connection creation and reuse
- centralize workflow type naming and task queue routing
- preserve the `mm:` workflow ID convention used by Mission Control route handling in [task_dashboard.py](/home/nsticco/MoonMind/api_service/api/routers/task_dashboard.py)
- have the client return real Temporal `workflowId` and `runId` values from workflow start responses
- use this client from the execution service rather than generating local IDs first
- ensure idempotent create behavior still works when an idempotency key is provided

**Acceptance tests**

- Creating an execution through the API produces a workflow ID that exists in Temporal, not only in local DB state.
- The returned `runId` corresponds to the actual Temporal run.
- Repeating the same create request with the same idempotency key returns the same logical execution without starting duplicates.
- If the local projection row is deleted, the execution can still be rediscovered from Temporal using its workflow ID.

### 4.5 Make `TemporalExecutionService` Temporal-authoritative

**Goal**

Refactor `TemporalExecutionService` so its public behavior is an adapter over Temporal, not the implementation of workflow lifecycle itself.

**Why this is needed**

[service.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/service.py) currently handles create, list, describe, update, signal, and cancel primarily by mutating or querying local records. That makes the DB authoritative and leaves Temporal as a partially modeled concept.

**Implementation requirements**

- rework `create_execution()` to start workflows through Temporal
- rework `describe_execution()` to read authoritative workflow state from Temporal describe or synced projection data
- rework `list_executions()` to use Temporal visibility semantics
- rework `update_execution()` to call real workflow updates
- rework `signal_execution()` to call real workflow signals
- rework `cancel_execution()` to request cancellation or termination through Temporal
- keep API request and response contracts as stable as possible so Mission Control changes stay minimal
- if projection sync is needed on each read, make it explicit and idempotent

**Acceptance tests**

- A running workflow remains visible and controllable even if its local projection is stale.
- Canceling an execution updates Temporal first and local compatibility state second.
- Signal and update rejections come from workflow/runtime eligibility, not only from a local if-statement.
- Listing executions returns records that correspond to real Temporal workflows currently known to the namespace.

### 4.6 Implement projection sync from Temporal into MoonMind records

**Goal**

Convert the local execution tables into read projections and compatibility caches derived from Temporal.

**Why this is needed**

Mission Control and other MoonMind APIs still depend on MoonMind-shaped execution records. Those records are fine as compatibility layers, but they must be downstream mirrors of Temporal rather than the source of truth.

**Implementation requirements**

- define a single mapping from Temporal workflow state, memo, search attributes, and artifact refs into `TemporalExecutionRecord` fields in [models.py](/home/nsticco/MoonMind/api_service/db/models.py)
- populate compatibility fields used by [executions.py](/home/nsticco/MoonMind/api_service/api/routers/executions.py), including:
- `status`
- `dashboard_status`
- `state`
- `raw_state`
- `temporal_status`
- `close_status`
- `waiting_reason`
- `attention_required`
- `entry`
- workflow and run identifiers
- artifact references
- implement idempotent upsert behavior so the projection can be rebuilt safely
- support missing-row recovery and stale-row refresh
- ensure local rows can be reconstructed from Temporal without replaying business logic in the application layer

**Acceptance tests**

- Deleting a local projection row and then reading the execution detail recreates a correct local representation from Temporal data.
- Local list and detail rows converge to real Temporal state after a workflow action or phase transition.
- Stale local terminal state does not hide an actually running Temporal workflow.
- Projection sync does not create duplicate rows for the same workflow ID.

### 4.7 Move action semantics onto real workflow updates and signals

**Goal**

Back Mission Control operator actions with real Temporal update and signal handlers.

**Why this is needed**

The UI in [dashboard.js](/home/nsticco/MoonMind/api_service/static/task_dashboard/dashboard.js) already knows how to call Temporal action endpoints, but those endpoints currently rely on the staging service implementation in [service.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/service.py).

**Implementation requirements**

- preserve the existing API shapes in [executions.py](/home/nsticco/MoonMind/api_service/api/routers/executions.py)
- map `SetTitle`, `UpdateInputs`, and `RequestRerun` to workflow updates
- map `Approve`, `Pause`, `Resume`, and `ExternalEvent` to signals or updates as appropriate
- implement workflow-side validation so action eligibility is determined by the workflow’s current real state
- convert workflow rejections and validation failures into the existing MoonMind response envelope
- ensure action results eventually refresh Mission Control through authoritative read paths

**Acceptance tests**

- Each operator action is recorded in workflow history.
- Pausing a workflow causes it to stop making forward progress until resumed or otherwise handled.
- Approving a workflow waiting on approval causes it to continue.
- Requesting rerun produces a new run or continue-as-new behavior consistent with the designed lifecycle.
- Invalid actions return a stable API error without corrupting local projection state.

### 4.8 Wire activities to real side effects and durable artifact output

**Goal**

Run the existing activity handlers from real workflows and ensure all meaningful side effects produce artifact references instead of oversized workflow payloads.

**Why this is needed**

The repo already has meaningful handler logic in [activity_runtime.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/activity_runtime.py) and storage logic in [artifacts.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/artifacts.py), but the value of those components is limited until real workflows invoke them and Mission Control can inspect the outputs.

**Implementation requirements**

- register activity handlers on the correct worker fleets
- invoke them from workflows using Temporal activity execution APIs
- use artifact references for large inputs and outputs
- make sure all major execution phases produce useful artifacts where appropriate:
- input instructions
- plan output
- manifest summaries
- run indexes
- sandbox command output
- test output
- integration payload snapshots
- keep workflow history compact by storing references, summaries, and metadata rather than raw blobs

**Acceptance tests**

- A successful Temporal-backed task produces at least one artifact visible through the execution detail API.
- Large activity outputs are stored in the artifact backend rather than directly in workflow history.
- Artifact download links returned to Mission Control are valid for Temporal-backed executions.
- Restarting workers does not orphan already-written artifacts or break execution detail rendering.

### 4.9 Implement integration waiting, polling, and callbacks as workflow behavior

**Goal**

Make integration monitoring a real part of workflow execution rather than a local record mutation pattern.

**Why this is needed**

[service.py](/home/nsticco/MoonMind/moonmind/workflows/temporal/service.py) already models integration state, callback correlation, and polling semantics, but this logic must be owned by the workflow so that waiting and resumption are durable and replay-safe.

**Implementation requirements**

- keep the existing integration correlation model if it is useful for inbound callback routing
- when an integration is started, store only the metadata needed to resume or monitor it
- have the workflow enter a real waiting state when external action is pending
- route callback API events to the target workflow via Temporal signals
- implement polling through activities and workflow timers rather than local-only state transitions
- maintain `waiting_reason`, `attention_required`, and summary fields for compatibility UI rendering

**Acceptance tests**

- A workflow can enter a waiting-for-integration state and remain durable across worker restarts.
- An inbound callback advances the workflow without any manual DB repair.
- A polling-based integration can resume execution after several timer cycles.
- Mission Control detail reflects the waiting reason and then clears it when the workflow resumes.

### 4.10 Finish Temporal list and detail authority for Mission Control

**Goal**

Ensure the Temporal execution APIs provide fully authoritative data for Mission Control list and detail rendering.

**Why this is needed**

The Mission Control client already has substantial Temporal support in [dashboard.js](/home/nsticco/MoonMind/api_service/static/task_dashboard/dashboard.js). The remaining problem is not basic route support, but making sure the API returns authoritative values instead of staging approximations.

**Implementation requirements**

- preserve the runtime config structure already emitted by [task_dashboard_view_model.py](/home/nsticco/MoonMind/api_service/api/routers/task_dashboard_view_model.py)
- preserve route handling and source resolution already present in [task_dashboard.py](/home/nsticco/MoonMind/api_service/api/routers/task_dashboard.py)
- make sure list and detail payloads reliably include:
- `taskId`
- `source`
- `workflowId`
- `temporalRunId`
- `status`
- `rawState`
- `temporalStatus`
- `closeStatus`
- `waitingReason`
- `attentionRequired`
- `workflowType`
- `entry`
- artifact refs
- manifest status where applicable
- remove client reliance on fragile fallback assumptions wherever server-side fields are available

**Acceptance tests**

- `/tasks/list?source=temporal` renders without requiring any manual API patching or local assumptions.
- `/tasks/{workflowId}?source=temporal` renders a complete detail view for both standard and manifest workflows.
- Workflow IDs using the `mm:` format resolve correctly in the dashboard shell.
- Temporal list filters such as `workflowType`, `state`, and `entry` return correct filtered results.

### 4.11 Enable Mission Control Temporal actions for daily use

**Goal**

Turn on Temporal operator actions in the UI only after the backend is genuinely authoritative and stable.

**Why this is needed**

The actions UI is already scaffolded in [dashboard.js](/home/nsticco/MoonMind/api_service/static/task_dashboard/dashboard.js), but the feature flag should not be enabled until the backend action semantics are real.

**Implementation requirements**

- complete task 4.7 first
- enable `TEMPORAL_DASHBOARD_ACTIONS_ENABLED`
- verify that action capabilities returned by the API correctly drive button visibility and disabled reasons
- keep user-facing action behavior consistent with queue and orchestrator expectations where practical
- ensure action-triggered refreshes read back authoritative workflow state

**Acceptance tests**

- Action buttons appear only when the execution reports the corresponding capability.
- Clicking `Pause`, `Resume`, `Approve`, `Cancel`, `Set Title`, and `Rerun` performs the expected server action and refreshes the page correctly.
- Disabled actions show consistent failure behavior and do not produce silent UI no-ops.
- No action requires direct Temporal UI usage or out-of-band repair to complete.

### 4.12 Enable Mission Control Temporal submission for real user traffic

**Goal**

Allow users to create new Temporal-backed tasks from the main Mission Control submit flow.

**Why this is needed**

The client already contains Temporal submit support and `/api/executions` already accepts task-shaped payloads in [executions.py](/home/nsticco/MoonMind/api_service/api/routers/executions.py). The missing piece is a backend that starts real workflows and returns durable execution records suitable for immediate navigation.

**Implementation requirements**

- complete task 4.4 and task 4.5 first
- ensure the submit path can create any required artifacts before or during workflow start
- preserve the existing task-shaped request model so the UI does not need a separate product concept for Temporal
- return a canonical task ID and redirect path that Mission Control can navigate to immediately
- enable `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` only after the end-to-end path is stable

**Acceptance tests**

- Submitting a Temporal-backed task from `/tasks/new` creates a real Temporal workflow and redirects to the correct detail page.
- The detail page loads successfully immediately after create.
- Uploaded or referenced inputs appear as artifacts or execution metadata on the resulting detail page.
- Repeating a create request with the same idempotency key does not create duplicate workflows.

### 4.13 Add a supported local developer bring-up path

**Goal**

Make local Temporal development a standard, documented, reproducible path.

**Why this is needed**

The repo already contains Temporal infrastructure in [docker-compose.yaml](/home/nsticco/MoonMind/docker-compose.yaml), but local usage is not complete until a new engineer can bring up the stack and run a Temporal-backed task without inventing commands.

**Implementation requirements**

- update Compose defaults so Temporal services and worker fleets start in a useful state
- ensure `.env` values needed for local Temporal are documented and sane by default
- document exact bring-up commands, expected logs, and troubleshooting guidance
- include health expectations for:
- Temporal namespace bootstrap
- worker pollers
- MinIO reachability
- API readiness
- execution creation
- keep the path compatible with existing MoonMind local development conventions

**Acceptance tests**

- A fresh local environment can be started using documented commands only.
- The namespace exists, workers poll, and `/api/executions` is responsive after startup.
- A developer can create one Temporal-backed task end to end without entering any container manually.
- The documented bring-up path works on a clean machine or clean clone with only the documented prerequisites.

### 4.14 Add a full end-to-end Temporal acceptance test

**Goal**

Add a single high-value integration test that proves the user-visible Temporal path actually works.

**Why this is needed**

Without a full acceptance test, it is too easy for the repo to keep a polished staging surface while the actual runtime path is broken.

**Implementation requirements**

- cover the real user path through the API, not only isolated internal helpers
- test at least:
- create execution
- confirm a real workflow exists
- let at least one activity execute
- verify artifact linkage
- fetch detail successfully
- perform one operator action such as cancel or approve
- keep the assertions focused on product-visible outcomes, not Temporal implementation trivia
- place the test in the repo’s existing integration testing structure and use project-standard test commands

**Acceptance tests**

- The test fails if workers are not polling.
- The test fails if `/api/executions` only writes local rows without starting a workflow.
- The test fails if action endpoints do not affect the real workflow.
- The test passes on a healthy stack without manual intervention.

### 4.15 Define and implement production routing policy

**Goal**

Make backend routing to queue, orchestrator, or Temporal explicit, deterministic, and maintainable.

**Why this is needed**

Mission Control should remain task-oriented. Users should not need to understand execution substrates, but the backend must have a clear policy for where a given request runs and how mixed-source tasks resolve in one UI.

**Implementation requirements**

- document which task classes or feature flags route work to Temporal
- keep `temporal` out of the worker runtime picker, consistent with [TemporalDashboardIntegration.md](/home/nsticco/MoonMind/docs/UI/TemporalDashboardIntegration.md)
- make source resolution through [task_dashboard.py](/home/nsticco/MoonMind/api_service/api/routers/task_dashboard.py) deterministic for queue, orchestrator, and Temporal items
- define migration behavior for existing tasks and identifiers
- define rollout behavior for partial enablement, fallback, and operator visibility
- ensure support and debugging paths still allow engineers to determine the real execution source when needed

**Acceptance tests**

- A task submitted under the Temporal routing policy consistently appears as a Temporal-backed task in Mission Control.
- Mixed-source list and detail routes resolve the correct backend source without manual query-string forcing in normal operation.
- Operators can work with `/tasks*` without knowing the execution substrate.
- Engineers can still inspect the resolved source deterministically through the existing source-resolution API behavior.

## 5. Recommended Execution Order

The shortest safe path to usable Temporal workflows is:

1. implement the worker runtime
2. implement `MoonMind.Run`
3. implement `MoonMind.ManifestIngest`
4. add the real Temporal client layer
5. make `TemporalExecutionService` Temporal-authoritative
6. implement projection sync
7. wire actions onto workflow signals and updates
8. wire activities and artifacts end to end
9. implement integration waiting and callbacks
10. finish Mission Control list and detail authority
11. enable Mission Control actions
12. enable Mission Control submit
13. document local bring-up
14. add the end-to-end acceptance test
15. finalize production routing policy

## 6. Release Gate

Do not claim Temporal workflows are fully switched over until all of the following are demonstrated on one branch or release candidate:

- real workers poll Temporal queues by default
- a task can be created through Mission Control and started in Temporal
- the task is visible in Temporal-backed list and detail pages
- at least one operator action succeeds from Mission Control
- artifact links work for the Temporal-backed task
- the end-to-end acceptance test passes
