# Managed and External Agent Execution Model

## 1. Objective

Define a formalized, unified execution model in Temporal for delegating work to **true agent runtimes**.

This document covers two categories of agent execution:

1. **External agents**: MoonMind delegates work to an external agent system that MoonMind does not run.
2. **MoonMind-managed agents**: MoonMind launches and supervises a managed agent runtime such as Gemini CLI, Claude Code, or Codex CLI.

This model explicitly separates long-lived, stateful agent execution from plain, one-shot LLM model API calls. Agent execution is treated as a first-class lifecycle built around child workflows, asynchronous supervision, structured auth profiles, and artifact-based input/output exchange.

This document does **not** redefine ordinary `mm.activity.llm` behavior. Plain one-shot model calls remain standard LLM activities and are not treated as full agent runs.

---

## 2. Core Design: `MoonMind.AgentRun`

`MoonMind.Run` remains the root workflow. It represents a **task** — the top-level unit of work. Each task contains a **plan** consisting of one or more ordered **steps** (plan nodes). When a step requires a true agent runtime, `MoonMind.Run` starts a dedicated child workflow: `MoonMind.AgentRun`.

```
Task (MoonMind.Run workflow)
 └─ Plan (generated or provided)
     ├─ Step 1: sandbox.run_command        (activity)
     ├─ Step 2: MoonMind.AgentRun          (child workflow) → e.g. Gemini CLI
     ├─ Step 3: MoonMind.AgentRun          (child workflow) → e.g. Jules
     └─ Step 4: sandbox.run_tests          (activity)
```

This hierarchy enables a single task to involve multiple agents (one per step) alongside non-agent steps like sandbox commands, while `MoonMind.Run` provides the consistent task lifecycle envelope: state tracking, search attributes, pause/resume, cancellation, integration stages, and dashboard visibility.

`MoonMind.AgentRun` provides a single workflow-level lifecycle for both external and MoonMind-managed agents. The workflow remains agnostic to whether the underlying execution path is an external HTTP-based agent or a locally supervised managed runtime.

### Unified Lifecycle

Both external and managed agents follow the same high-level lifecycle:

1. **Prepare Context**
   Materialize workspace inputs and runtime context from artifact refs.

   * For external agents, this usually means generating presigned URLs or equivalent temporary artifact-access mechanisms.
   * For managed agents, this usually means hydrating a local workspace or runtime directory.

2. **Start Run**
   Launch the agent asynchronously and receive an `AgentRunHandle`.

   * External agents are typically started through a provider adapter call.
   * Managed agents are typically started through a managed runtime launcher and supervisor.

3. **Wait**
   Suspend workflow progress while waiting for completion, approval, intervention, timeout, or cancellation.
   Waiting should be implemented via Temporal Signals, Updates, and durable timers rather than long-blocking activities.

4. **Fetch Result**
   Retrieve final outputs, diagnostics, and logs from the adapter/runtime layer.

5. **Publish Outputs**
   Persist outputs as MoonMind artifacts and register them through artifact services such as `artifact.write_complete`.

6. **Cancel / Intervene**
   Respond to human-in-the-loop (HITL) events, approvals, cancellation requests, escalation requests, or operator intervention.

### Design Intent

`MoonMind.AgentRun` exists so that MoonMind can treat agent execution as a durable orchestration concern without embedding provider-specific runtime logic into the root workflow. The child workflow owns the execution lifecycle; the adapter/runtime layer owns the actual agent launch and supervision mechanics.

### Dispatch from `MoonMind.Run`

Agent dispatch happens **per step**, not per task. The plan execution loop in `MoonMind.Run._run_execution_stage()` iterates ordered plan nodes and determines the dispatch mechanism for each step:

- **Agent step** — the node's tool/skill type or an explicit `execution_mode` field indicates an agent runtime is required. `MoonMind.Run` starts `MoonMind.AgentRun` as a **child workflow**, constructing an `AgentExecutionRequest` from the node inputs.
- **Activity step** — the node dispatches to a standard Temporal activity (`sandbox.run_command`, `sandbox.run_tests`, integration activities, etc.) via the existing `workflow.execute_activity()` path.

This keeps the plan mechanism intact for multi-step tasks while allowing each step to use the appropriate execution strategy. A task can mix agent steps and non-agent steps freely, and can involve different agents in different steps.

Cancellation propagates automatically through Temporal's child workflow mechanism. When `MoonMind.Run` is cancelled, any in-flight `MoonMind.AgentRun` child workflow receives a `CancelledError`, which triggers its non-cancellable cleanup path for adapter-level cancel operations.

---

## 3. Shared Contracts

A unified contract family represents all agent executions regardless of whether the agent is external or MoonMind-managed.

### `AgentExecutionRequest`

Represents the canonical request to execute a true agent runtime.

Suggested fields:

* `agent_kind`: `external` | `managed`
* `agent_id`: such as `jules`, `openhands`, `gemini_cli`, `claude_code`, `codex_cli`
* `execution_profile_ref`: reference to execution/auth/concurrency policy
* `correlation_id`
* `idempotency_key`
* `instruction_ref`: primary task/prompt artifact ref
* `input_refs[]`: input artifact refs
* `expected_output_schema`
* `workspace_spec`: repository/worktree/branch/checkout instructions
* `parameters`

  * `model`
  * `effort`
  * `allowed_tools`
  * `publish_mode`
  * other runtime-specific knobs
* `timeout_policy`
* `retry_policy`
* `approval_policy`
* `callback_policy`

### `AgentRunHandle`

Returned by `start` operations and used to track a launched run.

Suggested fields:

* `run_id`
* `agent_kind`
* `agent_id`
* `status`
* `started_at`
* `poll_hint_seconds`
* optional callback correlation metadata

### `AgentRunStatus`

Represents the current lifecycle state of an agent run.

Suggested states:

* `queued`
* `launching`
* `running`
* `awaiting_callback`
* `awaiting_approval`
* `intervention_requested`
* `collecting_results`
* `completed`
* `failed`
* `cancelled`
* `timed_out`

Terminal-state semantics should be explicit and stable. `completed`, `failed`, `cancelled`, and `timed_out` should be treated as terminal.

### `AgentRunResult`

Represents the final result surface of an agent execution.

Suggested fields:

* `output_refs[]`
* `summary`
* `metrics`
* `diagnostics_ref`
* `failure_class`
* `provider_error_code`
* `retry_recommendation`

### Idempotency Requirements

Any start-like side-effecting operation must be idempotent with respect to a stable key such as:

* explicit `idempotency_key`, or
* a deterministic execution tuple like `(workflow_id, step_id, attempt)`

This is required so Temporal activity retries do not accidentally create duplicate external jobs or duplicate managed runtime launches.

---

## 4. Two Adapters, One Interface

The system uses a shared `AgentAdapter` interface that defines how MoonMind communicates with any true agent runtime.

### `AgentAdapter`

Common responsibilities:

* `start(request) -> AgentRunHandle`
* `status(run_id) -> AgentRunStatus`
* `fetch_result(run_id) -> AgentRunResult`
* `cancel(run_id)`

Additional optional operations may be added for intervention, log collection, or resume semantics, but the four operations above should define the common minimum contract.

### `ExternalAgentAdapter`

Used for agents that MoonMind does not run.

Examples:

* `jules`
* `openhands`
* future BYOA-style integrations

Responsibilities:

* translate `AgentExecutionRequest` into provider-specific REST/RPC payloads
* provision external-access artifact exchange mechanisms such as presigned URLs
* pass callback metadata or webhook endpoints
* interpret external run states and normalize them into `AgentRunStatus`
* fetch final outputs and diagnostics
* cancel remote work when possible

### `ManagedAgentAdapter`

Used for MoonMind-managed agent runtimes.

Examples:

* `gemini_cli`
* `claude_code`
* `codex_cli`

Responsibilities:

* resolve runtime profile and auth profile
* prepare local workspace/runtime context
* launch the managed runtime asynchronously
* normalize runtime states into `AgentRunStatus`
* fetch final outputs, logs, and diagnostics
* cancel or terminate managed runs when necessary

The managed adapter delegates to runtime-oriented execution components such as:

* `ManagedRuntimeLauncher`
* `ManagedRuntimeProfile`
* `ManagedAgentAuthProfile`
* managed run supervisor / run tracker

This separation keeps the adapter layer agent-oriented while allowing the lower execution layer to remain runtime-oriented.

---

## 5. External Agents

External agents are delegated to through provider-specific adapters, but MoonMind remains the top-level orchestrator.

### Execution Model

The external path should be callback-first whenever the provider supports it.

1. `ExternalAgentAdapter.start(...)` sends the start request to the external system.
2. Input artifacts are exchanged using temporary external-access mechanisms such as presigned URLs.
3. The external system performs the run out-of-process.
4. On completion or state transition, the external system notifies MoonMind through a callback/webhook.
5. MoonMind validates the callback and translates it into a Temporal Signal or Update to resume the waiting `MoonMind.AgentRun` workflow.
6. `fetch_result(...)` retrieves or finalizes outputs and diagnostics.

### Callback Verification

Webhook callbacks must be authenticated and correlated to the correct in-flight agent run. Callback verification rules should include:

* signature verification or equivalent provider authentication
* correlation to a known `run_id`
* replay protection where practical
* safe normalization of provider event types into MoonMind workflow events

### Polling Fallback

If a provider cannot reliably issue callbacks, `MoonMind.AgentRun` may use durable timers plus `status(...)` polling as a fallback. Polling must be bounded and should not hold worker capacity unnecessarily.

---

## 6. MoonMind-Managed Agents

MoonMind-managed agents are true agent runtimes that MoonMind launches and supervises directly, but MoonMind still does not own their internal reasoning loops. MoonMind owns the runtime envelope, not the agent’s cognition.

Examples include:

* Gemini CLI
* Claude Code
* Codex CLI

### Key Rule

Managed agent runtimes must **not** be treated as long-blocking one-shot LLM activities.

They may:

* maintain terminal loops
* use persistent auth state
* operate over a workspace for minutes at a time
* emit logs incrementally
* require approvals or operator intervention
* need provider-specific concurrency and cooldown controls

Because of that, they must be launched asynchronously and supervised durably.

### Managed Runtime Lifecycle

The managed path should follow the same conceptual lifecycle as the external path:

1. `ManagedAgentAdapter.start(...)` persists a durable run record.
2. A runtime launcher starts the CLI runtime as a subprocess or equivalent managed execution unit.
3. A supervisor tracks process/container state, heartbeats, and output streams.
4. On completion or interruption, the supervisor writes final state into the durable run record.
5. MoonMind converts supervisor events into workflow Signals or Updates for the waiting `MoonMind.AgentRun` workflow.
6. `fetch_result(...)` collects final outputs, logs, and diagnostics.

### Managed Runtime Supervisor

A dedicated supervisor component should own the active lifecycle of managed runs.

Suggested responsibilities:

* persist run metadata before launch
* spawn the runtime process/container
* track process/container identifiers
* stream stdout/stderr to artifact-backed log storage
* record heartbeats/status transitions
* classify exit states
* emit completion/intervention events back to MoonMind
* support cancellation/termination
* reconcile in-flight runs after restart

### Recovery and Reconciliation

Supervisor recovery must not assume that a prior PID is always reattachable after restart.

On worker or container restart, the supervisor should reconcile durable run records against actual launcher/runtime state and then do one of the following:

* reattach if the runtime is still valid and reachable
* mark the run as lost/unrecoverable
* trigger cancellation, compensation, or degraded completion handling

The goal is to avoid orphaned runs while remaining honest about what can and cannot be recovered.

### Wait-Phase Safety

`MoonMind.AgentRun` must not wait indefinitely for a supervisor callback alone.

Even when callback-first behavior is used, the workflow should maintain a durable timer or bounded polling fallback so it can wake up, inspect current run state, and fail or recover cleanly if the supervisor or callback path goes dark.

### Polling and Status Reads

For managed runtimes, `status(...)` should remain a short, fast activity that reads durable supervisor state.

The detached managed runtime itself should not be represented as a long-lived heartbeating Temporal activity. Heartbeats belong to activities that are themselves doing long-running work. In this design, the supervisor owns long-running runtime state, and `status(...)` simply exposes that state to the workflow when needed.

---

## 7. First-Class Auth Profiles

MoonMind-managed agent runtimes require formalized auth-profile handling.

### `ManagedAgentAuthProfile`

Represents a named auth and execution policy for a managed runtime.

Suggested fields:

* `profile_id`
* `runtime_id`
* `auth_mode`
* `volume_ref`
* `account_label`
* `max_parallel_runs`
* `cooldown_after_429`
* `rate_limit_policy`
* `enabled`

### Rules

* Raw credentials must never be placed in workflow payloads, artifacts, or logs.
* Runtime execution requests reference an auth profile indirectly through `execution_profile_ref` or equivalent profile references.
* Auth state for OAuth-based CLIs should be stored in persistent runtime-specific volumes or equivalent durable credential homes.
* Runtime-specific environment shaping must be supported. For example, OAuth mode may require clearing API-key variables so the runtime uses the persisted OAuth home as intended.

### Concurrency Enforcement

Concurrency and cooldown rules must be enforced **per auth profile**, not merely per runtime family.

Examples:

* `gemini_oauth_user_a` may allow only `1` parallel run
* `claude_code_team_profile` may allow `2` parallel runs
* a profile may enter cooldown after repeated `429 RESOURCE_EXHAUSTED` responses

This prevents collisions on shared auth homes and allows provider-specific throttling behavior to be modeled explicitly.

---

## 8. Artifact and Log Discipline

Large data must remain out of workflow history.

The following should be stored as artifacts or artifact-backed blobs rather than workflow payloads:

* prompts and instruction bundles
* hydrated context bundles
* stdout/stderr streams
* transcripts
* patches/diffs
* generated files
* diagnostics bundles

Workflow payloads should contain small, structured references such as artifact refs, run IDs, summary status, and compact metadata.

This rule applies equally to external and MoonMind-managed agent runs.

---

## 9. Worker Fleet Topology

MoonMind should keep execution responsibilities separated by capability and latency boundary.

### Current Stable Categories

* `mm.activity.llm`
  Plain, one-shot model calls such as planning, summarization, classification, or validation.

* `mm.activity.sandbox`
  Standard shell commands, tests, isolated Git operations, and generic workspace tasks.

* `mm.activity.integrations`
  External BYOA communication and integration adapters.

### Target-State Addition

* `mm.activity.agent_runtime`
  Dedicated fleet for launching and supervising MoonMind-managed agent runtimes.

This target-state queue/fleet is justified because managed agent runtimes have distinct requirements:

* persistent auth volume mounts
* provider-specific concurrency controls
* runtime supervision
* longer-lived execution
* richer logging and intervention flows
* stronger secrets and isolation boundaries

### Migration Note

Initial implementation may temporarily host managed-agent execution on the existing sandbox fleet if that reduces migration complexity.

However, the target state should be a distinct `agent_runtime` fleet once the managed-runtime lifecycle, auth-profile enforcement, and supervision model are formalized.

---

## 10. Human-in-the-Loop and Runtime Events

Both external and MoonMind-managed agent runs must integrate with workflow-native HITL handling.

### Supported Event Types

Examples include:

* approval required
* intervention requested
* clarification required
* escalation requested
* cancellation requested
* completion reported
* failure reported

### Temporal Mapping

These runtime events should be translated into Temporal-native mechanisms such as:

* **Signals** for asynchronous state transitions
* **Updates** where synchronous acknowledgement or validation is needed
* **Timers** for bounded waiting and polling fallback

The `MoonMind.AgentRun` workflow should remain the durable authority for state progression, while adapters/supervisors merely translate provider/runtime events into workflow events.

### Cancellation Cleanup

When `MoonMind.AgentRun` is cancelled, the workflow must still make a best effort to cancel the underlying external job or managed runtime.

Implementation should include a non-cancellable cleanup path, using the appropriate Temporal SDK mechanism, so adapter-level `cancel(run_id)` logic can run even while workflow cancellation is in progress.

---

## 11. Implementation Strategy

### Phase 1 — Formalize Contracts ✅

Canonical contracts live in `moonmind/schemas/agent_runtime_models.py`:
`AgentExecutionRequest`, `AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`, `FailureClass`.
The `AgentAdapter` protocol is in `moonmind/workflows/adapters/agent_adapter.py`.

### Phase 2 — Add `MoonMind.AgentRun` ✅

`MoonMind.AgentRun` is implemented in `moonmind/workflows/temporal/workflows/agent_run.py`, registered with `@workflow.defn(name="MoonMind.AgentRun")`, and included in `REGISTERED_TEMPORAL_WORKFLOW_TYPES`.

### Phase 3 — Implement Adapters ✅

Production adapters:
- `JulesAgentAdapter` — external agent adapter for Jules
- `ManagedAgentAdapter` — managed runtime adapter with auth-profile controls

Both conform to the `AgentAdapter` protocol.

### Phase 4 — Build Managed Runtime Execution Layer ✅

Runtime components in `moonmind/workflows/temporal/runtime/`:
- `ManagedRuntimeLauncher` (launcher.py)
- `ManagedRunStore` (store.py)
- `ManagedRunSupervisor` (supervisor.py)
- `LogStreamer` (log_streamer.py)

### Phase 4.5 — Remove Legacy Execution Path ✅

The legacy `_auto_skill_handler` (CLI subprocess launcher), `_build_runtime_planner` (plan synthesizer), `SkillActivityDispatcher`, `TemporalSkillActivities`, and `TemporalPlanActivities` have been removed from `worker_runtime.py`. All agent execution now flows through `MoonMind.AgentRun`.

### Phase 5 — Add Auth-Profile and Rate-Limit Controls ✅

`MoonMindAuthProfileManagerWorkflow` manages auth profile slot acquisition and release. The `MoonMind.AgentRun` workflow integrates with it for 429 retry with profile rotation.

### Phase 6 — Promote to Dedicated Runtime Fleet ✅

The `agent_runtime` fleet is defined in `workers.py` and `activity_catalog.py` with:
- `AGENT_RUNTIME_FLEET` and `AGENT_RUNTIME_TASK_QUEUE`
- Activity definitions: `agent_runtime.publish_artifacts`, `agent_runtime.cancel`

### Phase 6.5 — Connect Root Workflow (IN PROGRESS)

`MoonMind.Run` must dispatch to `MoonMind.AgentRun` as a child workflow **per step** when a plan node requires an agent runtime. The plan execution loop in `_run_execution_stage()` gains a dispatch discriminator: agent nodes start a child workflow, non-agent nodes call activities as before. See §2 "Dispatch from `MoonMind.Run`".

### Phase 7 — Harden Observability and HITL

Add:

* normalized run metrics
* status dashboards
* intervention surfaces
* callback verification
* retry/failure classification
* operator tooling for lost/unrecoverable run handling

---

## 12. Summary

MoonMind should treat true agent execution as a first-class orchestration concept distinct from ordinary one-shot LLM activities.

The correct unifying model is:

* `MoonMind.Run` remains the root workflow
* `MoonMind.AgentRun` becomes the child workflow for all true agent-runtime execution
* `AgentAdapter` defines the common execution interface
* `ExternalAgentAdapter` handles external delegated agents
* `ManagedAgentAdapter` handles MoonMind-managed agent runtimes
* both paths share the same lifecycle shape: start, wait, fetch result, publish outputs, cancel/intervene
* managed runtimes rely on asynchronous supervision, durable run tracking, structured auth profiles, and artifact-backed logs
* queue/fleet isolation should eventually reflect the distinct operational needs of managed runtimes

This gives MoonMind a consistent Temporal-native execution model that supports both BYOA integrations and MoonMind-managed CLI runtimes without collapsing either into an oversimplified plain LLM-call abstraction.
