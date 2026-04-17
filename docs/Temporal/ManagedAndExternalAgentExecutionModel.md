# Managed and External Agent Execution Model

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-ManagedAndExternalAgentExecutionModel.md`](../tmp/remaining-work/Temporal-ManagedAndExternalAgentExecutionModel.md)

Status: **Implemented** (runtime live; contract hardening in progress)
Last updated: 2026-04-09
Related:
- [`docs/Tools/SkillSystem.md`](../Tools/SkillSystem.md)
- [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](./ActivityCatalogAndWorkerTopology.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/ManagedAgents/LiveLogs.md`](../ManagedAgents/LiveLogs.md) — canonical design for artifact-first log capture, live observability streaming, and the MoonMind-native log viewer UI
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](../ManagedAgents/CodexCliManagedSessions.md) — desired-state contract for the Codex task-scoped managed session plane

---

## 1. Objective and document boundary

Define MoonMind’s unified Temporal execution model for **true agent runtimes**.

This document covers:

- the lifecycle of one true agent execution
- the workflow and activity boundaries used for that execution
- the shared runtime contracts used by managed and external agents
- the difference between adapter responsibilities and workflow responsibilities
- how provider-profile sloting and managed runtime supervision fit into the model
- how resolved agent skill snapshots are delivered to runtimes

Use this document for:

- `MoonMind.AgentRun`
- `AgentExecutionRequest`
- `AgentRunHandle`
- `AgentRunStatus`
- `AgentRunResult`
- adapter responsibilities
- runtime preparation and supervision boundaries

Use [`docs/Tools/SkillSystem.md`](../Tools/SkillSystem.md) for:

- `AgentSkillDefinition`
- `SkillSet`
- `ResolvedSkillSet`
- `.agents/skills` path policy
- source precedence
- versioning and snapshot rules

This document does **not** define:

- the storage model for agent skills
- source precedence rules across built-in, deployment, repo, and local skills
- ordinary one-shot LLM activity behavior (`mm.activity.llm`)
- generic executable tool contracts outside true agent runtime execution

Docker-backed workload tools are ordinary executable tools. They stay on the `tool.type = "skill"` path described by [`docs/Tasks/SkillAndPlanContracts.md`](../Tasks/SkillAndPlanContracts.md) and are not new `MoonMind.AgentRun` instances unless the launched runtime is itself a true managed agent runtime. [`docs/ManagedAgents/DockerOutOfDocker.md`](../ManagedAgents/DockerOutOfDocker.md) defines that workload-container boundary.

MoonMind explicitly separates long-lived, stateful agent execution from plain one-shot model calls. True agent execution is treated as a first-class orchestration concept built around:

- child workflows
- asynchronous supervision
- structured provider profiles
- artifact-based input and output exchange
- canonical runtime contracts

---

## 2. Core design: `MoonMind.AgentRun`

`MoonMind.Run` remains the root workflow. It represents a **task**: the top-level unit of work. Each task contains a **plan** consisting of one or more ordered **steps**. When a step requires a true agent runtime, `MoonMind.Run` starts a dedicated child workflow: `MoonMind.AgentRun`.

Parent/child ownership rule:

- `MoonMind.Run` owns task-level orchestration, step ordering, compact step status, checks, and refs
- `MoonMind.AgentRun` owns the true runtime/provider lifecycle, detailed observability, and runtime result artifacts

```text
Task (MoonMind.Run workflow)
 └─ Plan (generated or provided)
     ├─ Step 1: sandbox.run_command        (activity)
     ├─ Step 2: MoonMind.AgentRun          (child workflow) → e.g. Gemini CLI
     ├─ Step 3: MoonMind.AgentRun          (child workflow) → e.g. Jules
     └─ Step 4: sandbox.run_tests          (activity)
````

This hierarchy allows a single task to mix:

* ordinary activities
* managed agent steps
* external delegated agent steps
* post-run validation or publishing work

`MoonMind.Run` owns the task-level envelope: planning, execution ordering, task state, dashboard visibility, cancellation propagation, and post-run task handling.

`MoonMind.AgentRun` owns exactly one true agent execution lifecycle.

### 2.1 Unified lifecycle

Both managed and external agents follow the same lifecycle shape:

1. **Prepare context**
   Materialize execution inputs, workspace context, runtime parameters, and any resolved skill snapshot references needed for the run.

2. **Start run**
   Launch the agent asynchronously and receive an `AgentRunHandle`.

3. **Wait**
   Suspend workflow progress while waiting for completion, approval, intervention, timeout, or cancellation. Waiting is modeled with Signals, Updates, and durable timers rather than long-blocking activities.

4. **Read status**
   Poll or consume callback-driven state transitions using canonical `AgentRunStatus` payloads.

5. **Fetch result**
   Retrieve final outputs, diagnostics, and logs as a canonical `AgentRunResult`.

6. **Publish outputs**
   Persist output artifacts and register any enriched artifact references without placing large payloads into workflow history.

7. **Cancel or intervene**
   On cancellation or operator intervention, invoke the adapter/runtime cancel surface and allow best-effort cleanup.

### 2.2 Design intent

`MoonMind.AgentRun` exists so that MoonMind can treat agent execution as a durable orchestration concern without embedding provider-specific runtime logic into the root workflow.

The split is intentional:

* the **child workflow** owns lifecycle orchestration
* the **adapter** owns provider/runtime translation
* the **activities** own side effects
* the **artifact system** owns large data
* the **provider-profile manager** owns slot and cooldown coordination for managed runtimes

### 2.3 Dispatch from `MoonMind.Run`

Agent dispatch happens **per step**, not per task.

The plan execution loop in `MoonMind.Run._run_execution_stage()` iterates ordered plan nodes and chooses one of two paths:

* **Agent step**
  `MoonMind.Run` starts `MoonMind.AgentRun` as a child workflow, constructing an `AgentExecutionRequest` from the node inputs.

* **Activity step**
  `MoonMind.Run` executes a standard Temporal activity directly.

This preserves one consistent task model while allowing each step to use the correct execution primitive.

### 2.4 Cancellation propagation

Cancellation propagates naturally through Temporal child workflows.

When `MoonMind.Run` is canceled, any in-flight `MoonMind.AgentRun` receives a `CancelledError`. `MoonMind.AgentRun` must still make a best-effort attempt to:

* cancel the underlying managed runtime or external run
* release any held provider-profile slot
* avoid leaving orphaned external work behind

### 2.5 Retry and rerun boundary

Source precedence and resolved agent instruction snapshot construction belong to upstream resolution activities or control-plane preparation. `MoonMind.AgentRun` consumes immutable refs and does not re-resolve them ad hoc.

Rules:

* Temporal activity retries reuse the same execution request
* agent-run retry loops reuse the same logical request unless explicitly rebuilt
* reruns reuse the original snapshot by default
* explicit re-resolution is a separate action, not an implicit side effect of retry

---

## 3. Canonical contract rule

This document adopts a strict rule:

> **All agent-facing runtime activities must return canonical agent runtime contracts directly.**
> Workflow code must not depend on provider-shaped or runtime-shaped payload variants.

That means:

* `integration.<provider>.start` returns `AgentRunHandle`
* `integration.<provider>.status` returns `AgentRunStatus`
* `integration.<provider>.fetch_result` returns `AgentRunResult`
* `integration.<provider>.cancel` returns `AgentRunStatus`
* `agent_runtime.status` returns `AgentRunStatus`
* `agent_runtime.fetch_result` returns `AgentRunResult`

Provider-specific or runtime-specific details belong in canonical `metadata` fields, not in alternate top-level response shapes.

### 3.1 Why this rule exists

This rule keeps normalization at the correct boundary.

Normalization belongs in:

* provider adapters
* managed runtime adapter layers
* activity handlers that bridge those adapters into Temporal

Normalization does **not** belong in:

* `MoonMind.AgentRun`
* `MoonMind.Run`
* parent-to-child orchestration glue
* ad hoc workflow coercion helpers

### 3.2 Compatibility direction

Older compatibility code may continue to exist temporarily for replay safety or migration windows, but the target state is:

* no workflow-side contract coercion
* no provider-specific top-level return payloads
* no workflow logic that reconstructs canonical contracts from partial dicts

---

## 4. Shared contracts

A unified contract family represents all true agent executions regardless of whether the target is managed or external.

The canonical schema source of truth is `moonmind/schemas/agent_runtime_models.py`.

## 4.1 `AgentExecutionRequest`

Represents the canonical request to execute one true agent runtime.

Canonical fields:

* `agent_kind`: `external` | `managed`
* `agent_id`
* `execution_profile_ref`
* `correlation_id`
* `idempotency_key`
* `instruction_ref`
* `input_refs[]`
* `expected_output_schema`
* `workspace_spec`
* `parameters`
* `timeout_policy`
* `retry_policy`
* `approval_policy`
* `callback_policy`
* `profile_selector`

Notes:

* `execution_profile_ref` may be omitted or resolved dynamically
* raw credentials must never be embedded in this payload
* large instruction bundles belong in artifacts referenced by `instruction_ref` or `input_refs[]`

### Skill snapshot extension fields

MoonMind may extend the request with runtime-preparation refs such as:

* `resolved_skillset_ref`
* `skill_materialization_mode`
* `skill_prompt_index_ref`
* `skill_manifest_ref`
* `skill_policy_summary`

These fields belong to the same architectural boundary, but they do not change the core rule that the request remains small, structured, and artifact-reference oriented.

## 4.2 `AgentRunHandle`

Returned by `start(...)`.

Canonical fields:

* `run_id`
* `agent_kind`
* `agent_id`
* `status`
* `started_at`
* `poll_hint_seconds`
* `metadata`

This contract represents successful launch and the minimum durable tracking information needed by the workflow.

## 4.3 `AgentRunStatus`

Returned by `status(...)` and `cancel(...)`.

Canonical fields:

* `run_id`
* `agent_kind`
* `agent_id`
* `status`
* `observed_at`
* `poll_hint_seconds`
* `metadata`

Canonical states are:

* `queued`
* `awaiting_slot`
* `launching`
* `running`
* `awaiting_callback`
* `awaiting_feedback`
* `awaiting_approval`
* `intervention_requested`
* `collecting_results`
* `completed`
* `failed`
* `canceled`
* `timed_out`

Terminal states are:

* `completed`
* `failed`
* `canceled`
* `timed_out`

### `awaiting_slot`

`awaiting_slot` is the canonical state for “execution is blocked on a prerequisite execution resource before launch,” primarily a provider-profile slot for managed runtimes.

This replaces vague terms like `awaiting` at the contract level.

Metadata may include values such as:

```json
{
  "awaitingReason": "provider_profile_slot",
  "profileManager": "provider-profile-manager:gemini_cli"
}
```

## 4.4 `AgentRunResult`

Returned by `fetch_result(...)`.

Canonical fields:

* `output_refs[]`
* `summary`
* `metrics`
* `diagnostics_ref`
* `failure_class`
* `provider_error_code`
* `retry_recommendation`
* `metadata`

This result is the canonical terminal output surface for an agent run. Large data stays in artifacts referenced by `output_refs[]` or `diagnostics_ref`.

## 4.5 Idempotency requirements

Any start-like side effect must be idempotent with respect to a stable key such as:

* explicit `idempotency_key`, or
* a deterministic execution tuple like `(workflow_id, step_id, attempt)`
* parent-step refs such as `childWorkflowId`, `childRunId`, and `taskRunId`

This is required so activity retries do not create duplicate external jobs or duplicate managed launches.

---

## 5. Activity contract mapping

The following activity families participate in true agent runtime execution.

### 5.1 External provider activities

For one provider `<provider>`:

* `integration.<provider>.start(request: AgentExecutionRequest) -> AgentRunHandle`
* `integration.<provider>.status({ run_id or external mapping input }) -> AgentRunStatus`
* `integration.<provider>.fetch_result({ run_id or external mapping input }) -> AgentRunResult`
* `integration.<provider>.cancel({ run_id or external mapping input }) -> AgentRunStatus`

These activities may talk to provider APIs, transform provider state names, and enrich metadata, but they must return canonical contracts.

### 5.2 Managed runtime activities

* `agent_runtime.launch(...)` launches the underlying managed process/runtime envelope
* `agent_runtime.status(...) -> AgentRunStatus`
* `agent_runtime.fetch_result(...) -> AgentRunResult`
* `agent_runtime.cancel(...) -> AgentRunStatus`
* `agent_runtime.publish_artifacts(...) -> AgentRunResult` or an enriched canonical result payload compatible with `AgentRunResult`

The important rule is that **status** and **fetch_result** must already be canonical before they reach `MoonMind.AgentRun`.

### 5.3 Workflow helper activities

Activities such as adapter metadata resolution or manager bootstrap helpers may still exist for determinism or startup reasons, but they are not part of the canonical agent execution contract surface.

---

## 6. Two adapters, one interface

MoonMind uses a shared `AgentAdapter` interface for true agent runtimes.

## 6.1 `AgentAdapter`

Common responsibilities:

* `start(request) -> AgentRunHandle`
* `status(run_id) -> AgentRunStatus`
* `fetch_result(run_id) -> AgentRunResult`
* `cancel(run_id) -> AgentRunStatus`

Common rules:

* consume `AgentExecutionRequest`
* return canonical contracts only
* hide provider-specific response shapes from workflow code
* preserve retry safety and idempotency behavior
* keep large content in artifacts, not runtime payloads

Additional optional behavior may exist for logs, intervention, or resume semantics, but those do not change the canonical core.

## 6.2 `ExternalAgentAdapter`

Used when MoonMind delegates execution to a system it does not run.

Examples:

* `jules`
* `openhands`
* future BYOA integrations

Responsibilities:

* translate `AgentExecutionRequest` into provider-specific REST/RPC payloads
* provision external-access artifact exchange mechanisms such as presigned URLs
* translate resolved skill snapshots into provider-compatible bundles when needed
* interpret provider statuses and normalize them into `AgentRunStatus`
* fetch final outputs and diagnostics as `AgentRunResult`
* cancel remote work when supported
* keep provider-specific details in canonical `metadata`

## 6.3 `ManagedAgentAdapter`

Used for MoonMind-managed runtimes.

Examples:

* `gemini_cli`
* `claude_code`
* `codex_cli`

Responsibilities:

* resolve runtime profile and provider profile
* prepare local workspace/runtime context
* materialize any active skill snapshot into the managed runtime environment
* launch the runtime asynchronously
* interpret runtime/supervisor state as canonical `AgentRunStatus`
* fetch final outputs, logs, and diagnostics as `AgentRunResult`
* cancel or terminate managed runs when necessary

The managed adapter delegates to lower-level runtime execution components such as:

* `ManagedRuntimeLauncher`
* `ManagedRuntimeProfile`
* `ManagedAgentProviderProfile`
* `ManagedRunSupervisor`
* `ManagedRunStore`

---

## 7. External agents

External agents are delegated to through provider-specific adapters, but MoonMind remains the top-level orchestrator.

### 7.1 Skill delivery for external agents

External agents may receive resolved skill context through:

* compact prompt bundles
* provider-uploaded bundle artifacts
* presigned URLs to manifests or bundles
* provider-specific translated representations where filesystem access is unavailable

External adapters must not independently re-resolve skill sources. They consume the immutable resolved snapshot handed to them.

### 7.2 Execution model

The external path is callback-first whenever the provider supports it.

1. `start(...)` submits the request to the external system and returns `AgentRunHandle`
2. any input artifacts are made accessible through provider-appropriate mechanisms
3. the external system performs the run out of process
4. MoonMind receives provider callbacks where supported
5. `MoonMind.AgentRun` resumes through Signals or durable timer wakeups
6. `status(...)` returns canonical `AgentRunStatus`
7. `fetch_result(...)` returns canonical `AgentRunResult`

### 7.3 Callback verification

Webhook callbacks must be authenticated and correlated to the correct run.

Verification rules should include:

* signature verification or equivalent provider authentication
* correlation to a known run
* replay protection where practical
* safe normalization into workflow events

### 7.4 Polling fallback

If a provider cannot reliably issue callbacks, `MoonMind.AgentRun` uses durable timers plus short `status(...)` activities.

Polling must be bounded and must not keep worker capacity occupied through long-blocking activity calls.

---

## 8. MoonMind-managed agents

MoonMind-managed agents are true runtimes launched and supervised by MoonMind, but MoonMind still does not own their internal reasoning loops.

Examples:

* Gemini CLI
* Claude Code
* Codex CLI

## 8.1 Key rule

Managed runtimes must **not** be modeled as long-blocking one-shot LLM activities.

They may:

* maintain terminal loops
* use persistent auth state
* operate over a workspace for minutes at a time
* emit logs incrementally
* require approvals or intervention
* need provider-specific concurrency and cooldown controls

Because of that, they must be launched asynchronously and supervised durably.

## 8.2 Managed runtime lifecycle

The managed path follows the same conceptual lifecycle as the external path:

1. acquire any required provider-profile slot
2. `ManagedAgentAdapter.start(...)` persists or binds a durable managed run
3. a runtime launcher starts the CLI runtime asynchronously
4. a supervisor tracks process/container state, logs, and heartbeat-equivalent state
5. the workflow polls `agent_runtime.status(...)` or receives completion signals
6. `agent_runtime.fetch_result(...)` returns canonical `AgentRunResult`
7. any held provider-profile slot is released
8. artifacts are published

## 8.3 Managed runtime supervisor

A dedicated supervisor should own the active lifecycle of managed runs.

Responsibilities include:

* persist run metadata before launch
* spawn runtime processes or containers
* track identifiers and workspace paths
* stream stdout/stderr to artifact-backed storage (durability comes first)
* emit live log records for active subscribers via the shared cross-process observability transport (secondary; must not break artifact persistence or run completion if live publish fails)
* update `last_log_at` and `last_log_offset` metadata after each captured chunk, for use by the observability summary API
* generate `system` event annotations where needed (e.g. run start, truncation notices, timeout classification)
* hand off live log chunks to the shared live-stream transport boundary as they are captured
* record state transitions
* classify exit states
* support cancellation and restart reconciliation

**Priority rule:** artifact persistence is authoritative. Live stream emission is a secondary concern. If live publication fails, the supervisor must continue capturing artifacts and completing the run normally.

## 8.4 Cross-process observability transport boundary

**Critical architectural constraint:** Managed runtime supervision may run in a different process or container from the API service.

Therefore:

* live log publication must target a shared MoonMind observability transport (e.g. Redis pub/sub, shared append-only spool, DB-backed tailing), not an API-local memory singleton
* the runtime model does not assume same-process UI/API delivery of live events
* a process-local replay buffer may exist as a performance optimization, but it is not the architecture boundary
* the shared transport mechanism must be documented and agreed before the live-emit path is implemented (see `docs/tmp/009-LiveLogsPlan.md` Phase 3 pre-step)

This is the key constraint for live log delivery. An API-local in-memory publisher is not sufficient.

## 8.5 Recovery and reconciliation

Supervisor recovery must not assume a prior PID can always be reattached after restart.

On worker or container restart, the system should:

* reattach if the runtime is still valid and reachable
* mark the run as lost or unrecoverable if not
* trigger cancellation or degraded completion behavior where appropriate

## 8.6 Wait-phase safety

`MoonMind.AgentRun` must not wait indefinitely on callback paths alone.

Even for managed callback-first behavior, the workflow should maintain a durable timer or bounded status-read fallback so it can wake up and inspect current state.

## 8.7 Polling and status reads

For managed runtimes, `status(...)` should remain a short activity that reads durable supervisor state.

The detached runtime itself should not be represented as one giant heartbeating Temporal activity.

## 8.8 Failure-mode rule

If live streaming is unavailable or degraded:

* managed run execution continues normally
* artifacts and final diagnostics still define the authoritative run record
* Mission Control must be able to observe completed runs without ever having had a live stream connection
* live streaming failure is never a root cause of run failure

---

## 9. First-class provider profiles

MoonMind-managed runtimes require formalized provider-profile handling. See [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md).

### 9.1 `ManagedAgentProviderProfile`

Represents a named provider, credential, and execution policy binding for a managed runtime.

Key fields include:

* `profile_id`
* `runtime_id`
* `provider_id`
* `volume_ref`
* `account_label`
* `max_parallel_runs`
* `cooldown_after_429_seconds`
* `rate_limit_policy`
* `enabled`

### 9.2 Rules

* raw credentials must never be placed in workflow payloads, artifacts, or logs
* execution requests reference profiles indirectly through `execution_profile_ref`
* OAuth-based auth state should live in durable runtime-specific volumes or homes
* runtime-specific environment shaping is allowed, but raw secrets must not leak into durable workflow state

### 9.3 Concurrency enforcement

Concurrency and cooldown are enforced **per provider profile**, not merely per runtime family.

Examples:

* `gemini_oauth_user_a` may allow only one parallel run
* `claude_code_team_profile` may allow two parallel runs
* one profile may enter cooldown after repeated `429` responses while another remains usable

---

## 10. Artifact and log discipline

Large data must remain out of workflow history.

The following belong in artifacts or artifact-backed blobs rather than workflow payloads:

* prompts and instruction bundles
* resolved skill manifests
* prompt indexes
* runtime materialization bundles
* hydrated context bundles
* stdout/stderr streams
* transcripts
* patches and diffs
* generated files
* diagnostics bundles

Workflow payloads should contain only compact metadata and refs such as:

* artifact refs
* run IDs
* statuses
* summaries
* small metrics
* compact metadata dictionaries

This rule applies equally to managed and external agent runs.

## 10.1 `AgentRunResult` vs observability APIs

`AgentRunResult` is the terminal workflow contract: it represents the final outcome of an agent run as seen by `MoonMind.AgentRun` and the workflow history.

**Live logs and tailed observation are not delivered through `AgentRunResult`.** They belong to the observability API surface.

Specifically:

* `AgentRunResult.output_refs[]` contains durable output artifact refs for the workflow result
* `AgentRunResult.diagnostics_ref` is the final diagnostics artifact for the run
* live log events, artifact-backed tails, and per-stream retrieval are served through observability APIs, not through workflow payloads or `AgentRunResult`
* Mission Control uses the observability APIs for task detail live/tailed observation, not the workflow result surface
* the parent step ledger should carry only bounded refs back to that observability surface, not duplicate managed-run log state

## 10.2 Observability metadata expectations for managed runs

The following fields are operational observability metadata, not the authoritative data itself. They enable efficient discovery without requiring artifact downloads.

Expected fields on the managed run record or observability summary:

| Field | Purpose |
| --- | --- |
| `stdout_artifact_ref` | Ref to the durable stdout artifact |
| `stderr_artifact_ref` | Ref to the durable stderr artifact |
| `merged_log_artifact_ref` | Optional; ref to a pre-merged log artifact |
| `diagnostics_ref` | Ref to the diagnostics artifact |
| `last_log_at` | Timestamp of the most recently captured log chunk |
| `last_log_offset` | Byte offset of the most recently captured log chunk |
| `live_stream_id` | Identifier for the live stream session, if active |
| `live_stream_status` | Current stream status (`available`, `ended`, `unavailable`) |
| `supports_live_streaming` | Whether a live stream can be connected for this run |

These fields are metadata only. The artifact refs and the observability APIs are the actual data access paths.

---

## 11. Worker fleet topology

MoonMind keeps execution responsibilities separated by capability and latency boundary.

### 11.1 Relevant fleets

* `mm.workflow`
  Workflow code

* `mm.activity.integrations`
  External provider communication and provider adapters

* `mm.activity.agent_runtime`
  Managed runtime supervision, status, result collection, artifact publication, and cancellation

### 11.2 Why `agent_runtime` is separate

The dedicated `agent_runtime` fleet exists because managed runtimes have distinct requirements:

* persistent auth volume mounts
* provider-specific concurrency controls
* runtime supervision
* longer-lived execution
* richer logging and intervention flows
* stronger isolation boundaries
* runtime preparation responsibilities

### 11.3 Helper-activity note

Some lightweight helper activities may still be placed on the workflow fleet for determinism-safe metadata resolution or startup coordination. Those helpers are not part of the agent contract surface and should remain small exceptions rather than grow into a second activity plane.

---

## 12. Human-in-the-loop and runtime events

Both external and managed agent runs must integrate with workflow-native HITL handling.

### 12.1 Supported event types

Examples include:

* approval required
* intervention requested
* clarification required
* escalation requested
* cancellation requested
* completion reported
* failure reported

### 12.2 Temporal mapping

These runtime events should be translated into Temporal-native mechanisms such as:

* **Signals** for asynchronous state transitions
* **Updates** where synchronous acknowledgement or validation is needed
* **Timers** for bounded waiting and polling fallback

`MoonMind.AgentRun` remains the durable authority for state progression. Adapters and supervisors translate runtime events into workflow events; they do not own orchestration truth.

### 12.3 Cancellation cleanup

When `MoonMind.AgentRun` is canceled, the workflow must still make a best-effort attempt to cancel the underlying external or managed work, even while cancellation is in progress.

---

## 13. Implemented components and current hardening work

### 13.1 Implemented components

**Contracts and workflow**

* `AgentExecutionRequest`
* `AgentRunHandle`
* `AgentRunStatus`
* `AgentRunResult`
* `FailureClass`
* `MoonMind.AgentRun`

**Adapters**

* `JulesAgentAdapter`
* `ManagedAgentAdapter`

**Managed runtime layer**

* `ManagedRuntimeLauncher`
* `ManagedRunStore`
* `ManagedRunSupervisor`
* runtime log streaming and result publication support

**Auth and fleet**

* `MoonMindProviderProfileManagerWorkflow`
* dedicated `agent_runtime` activity family

**Root workflow**

* `MoonMind.Run` dispatches `tool.type == "agent_runtime"` plan nodes to `MoonMind.AgentRun`

### 13.2 Hardening direction

The current contract-hardening direction is:

* require all agent-facing activities to emit canonical contracts directly
* remove workflow-side coercion glue over time
* keep provider-specific fields inside canonical `metadata`
* reject unknown or non-canonical runtime/provider statuses at the adapter or activity boundary
* keep replay-compatibility shims only where needed for history safety

### 13.3 Remaining work

Ongoing or pending work includes:

* normalized metrics and dashboard surfaces
* callback verification hardening
* richer intervention/operator tooling
* failure classification refinement
* resolved skill snapshot propagation and materialization work
* elimination of legacy contract-shape compatibility glue where replay-safe
* live log stream producer-to-API plumbing (supervisor must emit into shared cross-process transport)
* Mission Control observability panel implementation (artifact-backed tail + live-follow)

### 13.4 Legacy session-based observability

Legacy terminal/session metadata (`TaskRunLiveSession`, `tmate web_ro`, socket paths, `attachRo`, `webRo`) may remain in the database for historical runs created before the MoonMind-native observability model was in place.

Migration rules:

* legacy session metadata is not the target model for managed-run observability
* new managed runs must be modeled through observability metadata and artifacts (`stdout_artifact_ref`, `stderr_artifact_ref`, `live_stream_id`, etc.)
* code paths that use terminal-session fields for managed-run log viewing are migration targets, not supported architecture paths
* historical runs that only have legacy session data should degrade gracefully in the new UI

---

## 14. Summary

MoonMind treats true agent execution as a first-class orchestration concept distinct from ordinary one-shot LLM activities.

The unifying model is:

* `MoonMind.Run` remains the root task workflow
* `MoonMind.AgentRun` is the child workflow for one true agent execution
* `AgentAdapter` defines the shared lifecycle interface
* `ExternalAgentAdapter` handles delegated external agents
* `ManagedAgentAdapter` handles MoonMind-managed runtimes
* both paths share the same lifecycle shape: start, wait, read status, fetch result, publish outputs, cancel or intervene
* managed runtimes rely on asynchronous supervision, durable run tracking, structured provider profiles, and artifact-backed logs
* all agent-facing runtime activities return canonical contracts directly
* workflow code should not depend on provider-shaped response payloads

This gives MoonMind a consistent Temporal-native execution model for both BYOA integrations and MoonMind-managed CLI runtimes without collapsing true agent execution into oversimplified plain model-call abstractions.
