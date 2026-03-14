# Managed and External Agent Execution Model

## 1. Objective

Define a formalized, unified execution model in Temporal for delegating work to "true agent runtimes"—whether they are external "black-box" systems (like Jules or OpenHands) or internal "white-box" managed CLIs (like Gemini CLI, Claude Code, or Codex CLI).

This model explicitly separates long-lived, stateful agent loops from plain, one-shot LLM model API calls, treating agent execution as a first-class lifecycle built around child workflows, asynchronous supervision, and structured auth profiles.

## 2. The Core Design: `MoonMind.AgentRun`

`MoonMind.Run` remains the root workflow. When a task requires an agent, rather than invoking a long-running blocking activity, it starts a dedicated child workflow: `MoonMind.AgentRun`.

### The Unified Lifecycle
Both external and managed agents share the exact same high-level workflow lifecycle:
1. **Prepare Context**: Materialize workspaces or generate presigned URLs from artifact refs.
2. **Start Run**: Issue an asynchronous launch request (HTTP POST for external, subprocess spawn for managed) and receive an `AgentRunHandle`.
3. **Wait**: Suspend the workflow to await a completion signal (webhook or supervisor callback) or poll via timers.
4. **Fetch Result**: Retrieve final outputs and logs.
5. **Publish Outputs**: Register new artifacts via `artifact.write_complete`.
6. **Cancel / Intervene**: Handle human-in-the-loop (HITL) approval requests or cancellation signals.

## 3. Shared Contracts

A unified contract family represents all agent calls:

### `AgentExecutionRequest`
- `agent_kind`: `external` | `managed`
- `agent_id`: `jules`, `openhands`, `gemini_cli`, `claude_code`, `codex_cli`
- `execution_profile_ref`: Points to auth and concurrency rules.
- `correlation_id` / `idempotency_key`
- `instruction_ref`: Task prompt.
- `input_refs[]` & `expected_output_schema`
- `workspace_spec`: Repository and branch targets.
- `parameters`: Model, effort, allowed_tools, publish_mode.
- `Policies`: Timeout, retry, approval, callback.

### `AgentRunHandle` & `AgentRunStatus`
Returned by `start` activities, establishing a trackable run ID. Standardized states include: `queued`, `launching`, `running`, `awaiting_callback`, `awaiting_approval`, `intervention_requested`, `completed`, `failed`.

### `AgentRunResult`
Returns `output_refs[]`, metrics, summary, diagnostics refs, and explicit failure classification for robust retry handling.

## 4. Two Adapters, One Interface

The system utilizes two explicit registries that implement the `start/status/fetch_result/cancel` interface:

1. **`AgentAdapterRegistry`**: Defines how MoonMind communicates with the agent.
   - *External*: `jules`, `openhands` (Uses REST payloads and presigned URLs).
   - *Managed*: `gemini_cli`, `claude_code`, `codex_cli` (Uses workspace hydration and subprocess supervision).

2. **`AgentRuntimeProfileRegistry`**: Defines how managed runtimes are launched (binary paths, default models, secrets policies, max concurrency).

## 5. Managed Internal Agents (CLI Runtimes)

**Crucial Paradigm Shift**: Managed CLI runtimes (Gemini CLI, Codex) must **not** be executed as single, blocking Temporal activities. They possess state, OAuth profiles, terminal loops, and run for minutes at a time. Blocking a Temporal worker thread for the entire duration degrades scalability.

### The Agent Runtime Supervisor
Instead of blocking, the new `mm.activity.agent_runtime` queue will host a Supervisor:
1. **Async Start**: `agent_runtime.managed.start` accepts the request, persists a durable run record, spawns the CLI subprocess (or container) detached from the immediate activity thread, and immediately returns a `managed_run_id`.
2. **Supervision**: The supervisor tracks the PID, streams stdout/stderr to artifact chunks, and records heartbeats.
3. **Completion**: When the CLI exits, the supervisor updates the run record and posts a completion Signal back to the waiting `MoonMind.AgentRun` workflow.
4. **Resilience**: On worker crash/restart, the supervisor scans the durable run table to reconnect to active PIDs/containers, ensuring no runs are orphaned.

### First-Class Auth Profiles (`ManagedAgentAuthProfile`)
OAuth configuration for CLIs (e.g., Gemini CLI) is strictly managed via persistent volumes, decoupled from standard API keys.
- **Rules**: Raw credentials are NEVER placed in workflow payloads. Requests reference an `auth_profile_ref`.
- **Concurrency**: Rate limits and max parallel runs are enforced *per auth profile* (e.g., limiting `gemini_oauth_user_a` to 1 parallel run) to prevent session collisions on shared volumes.

## 6. Worker Fleet Topology

Execution logic is strictly separated by capability and latency boundaries:
- `mm.activity.llm`: Plain, one-shot model calls (`plan.generate`, summarization).
- `mm.activity.sandbox`: Standard shell commands, tests, isolated Git ops.
- `mm.activity.integrations`: External BYOA communication (HTTP adapters).
- **`mm.activity.agent_runtime`**: Dedicated fleet for launching and supervising managed CLI agents. Contains the supervisor, auth volume mounts, and specific concurrency rules.

## 7. Implementation Strategy

1. **Phase 1**: Formalize the python contracts (`AgentExecutionRequest`, `AgentRunHandle`, `ManagedAgentAuthProfile`).
2. **Phase 2**: Create the `MoonMind.AgentRun` child workflow.
3. **Phase 3**: Implement the managed runtime activities (`start`, `status`, `fetch_result`, `cancel`).
4. **Phase 4**: Build the asynchronous supervisor and durable run tracking.
5. **Phase 5**: Wire auth-profile and rate-limit controls.
6. **Phase 6**: Split managed runtimes into the dedicated `temporal-worker-agent-runtime` fleet.
