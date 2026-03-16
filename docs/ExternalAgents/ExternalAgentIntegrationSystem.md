# Technical Design: External Agent Integration System (BYOA)

## 1. Objective

Enable MoonMind to act as a definitive top-level orchestrator by defining a standardized "Bring Your Own Agent" (BYOA) protocol. This pattern leverages `MoonMind.AgentRun` child workflows and the `AgentAdapter` protocol to allow seamless, secure delegation to black-box or gray-box agents (e.g., Jules, OpenHands, future BYOA integrations) without requiring MoonMind to sandbox, containerize, or manage the agent's internal execution runtime.

For the full unified execution model covering both external and managed agents, see [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md).

## 2. Architecture Overview: The 3-Layer Integration Model

Rather than building custom Docker worker fleets for every new agent flavor, MoonMind delegates work through a unified adapter layer. The `JulesAgentAdapter` implementation serves as the reference pattern.

1. **Configuration Layer**: Dynamic endpoint URLs, API keys, and behavioral toggles loaded securely into provider-specific settings (e.g., `JulesSettings`). Feature gating ensures providers are only active when explicitly enabled and configured.

2. **Adapter Layer**: Provider-specific implementations of the `AgentAdapter` protocol (`moonmind/workflows/adapters/agent_adapter.py`). Each adapter normalizes provider interactions into the canonical contracts:
   - `start(request: AgentExecutionRequest) -> AgentRunHandle`
   - `status(run_id) -> AgentRunStatus`
   - `fetch_result(run_id) -> AgentRunResult`
   - `cancel(run_id) -> AgentRunStatus`

   Adapters handle provider-specific concerns: REST/RPC calls, retry behavior for 429s/5xx, timeout management, idempotency, and status normalization.

3. **Workflow Layer**: `MoonMind.AgentRun` child workflow (`moonmind/workflows/temporal/workflows/agent_run.py`), dispatched **per plan step** from `MoonMind.Run._run_execution_stage()` when a plan node has `tool.type == "agent_runtime"`. The child workflow owns the full execution lifecycle: adapter start, polling/callback wait loop, auth slot management (for managed agents), 429 retry with profile rotation, artifact publishing, and cancellation cleanup.

```
Task (MoonMind.Run workflow)
 └─ Plan (generated or provided)
     ├─ Step 1: sandbox.run_command        (activity)
     ├─ Step 2: MoonMind.AgentRun          (child workflow) → e.g. Jules
     ├─ Step 3: MoonMind.AgentRun          (child workflow) → e.g. Gemini CLI
     └─ Step 4: sandbox.run_tests          (activity)
```

This hierarchy enables a single task to involve multiple agents (one per step) alongside non-agent steps, with `MoonMind.Run` providing the consistent task lifecycle envelope.

## 3. The Context & Artifact Exchange Problem

Black-box agents require context (files, logs, plan definitions) and must return results, but they cannot be granted direct access to MoonMind's internal PostgreSQL or MinIO services.

This integration model solves the artifact boundary problem via two secure methods:

### Method A: Presigned URLs (The Default Payload Model)
When MoonMind dispatches an external `MoonMind.AgentRun`:
1. The adapter uses the `TemporalArtifactStore` to generate short-lived, presigned download URLs for the `input_refs` specified in the `AgentExecutionRequest`.
2. It generates presigned upload URLs for the expected `output_refs`.
3. The external agent receives these URLs in its initialization payload, downloads its instructions via standard HTTP GET, performs its work, and HTTP PUTs the results back before completion.
4. MoonMind verifies the uploaded artifacts via `artifact.write_complete`.

### Method B: Reverse MCP (Future — The Interactive Model)
If the external agent supports calling MCP tools during execution:
1. MoonMind provisions an ephemeral, restricted MCP server session specifically tied to the run.
2. This session exposes scoped capabilities, such as `moonmind.artifact.read` and `moonmind.artifact.write_complete`, bounded by the workflow's authorization context.
3. The external agent interactively requests context and submits artifacts directly via the MCP protocol without ever touching the underlying storage infrastructure.

> **Note:** Reverse MCP is not yet implemented. Presigned URLs are the current supported method.

## 4. Execution Flow: Callbacks vs. Polling

`MoonMind.AgentRun` manages the wait-phase for external agents using callback-first execution with a polling fallback.

### Callback-First Path (Preferred)
1. **Trigger**: The `ExternalAgentAdapter.start()` sends the start request to the external system, optionally including a webhook callback URL.
2. **Suspend**: `MoonMind.AgentRun` enters a bounded `wait_condition` loop, awaiting a `completion_signal` or timeout.
3. **Execute**: The external agent runs for minutes or hours independently.
4. **Notify**: The external agent finishes and POSTs results/status to the webhook.
5. **Resume**: MoonMind validates the callback payload and delivers a Temporal Signal to the waiting `MoonMind.AgentRun` workflow.

### Polling Fallback Path
If the external agent lacks webhook capability (the current default for Jules):
1. `MoonMind.AgentRun` uses a bounded `wait_condition` with a configurable timeout (from `AgentRunHandle.poll_hint_seconds`).
2. On timeout, it calls `adapter.status(run_id)` to check current state.
3. This continues until a terminal status is reached or the overall timeout expires.
4. No Temporal worker threads are held open during the wait.

### Wait-Phase Safety
`MoonMind.AgentRun` must not wait indefinitely. Even when callback-first behavior is used, the workflow maintains a bounded timeout so it can wake up, inspect current run state, and fail or recover cleanly if the callback path goes dark.

## 5. Integrating a New External Agent

Adding a new BYOA external agent requires:

1. **Provider settings class** — a `BaseSettings` subclass with API URL, key, enable flag, and operational controls.
2. **HTTP client adapter** — an async `httpx.AsyncClient` wrapper for the provider's REST API, following the `JulesClient` pattern.
3. **`AgentAdapter` implementation** — a class conforming to the `AgentAdapter` protocol that translates `AgentExecutionRequest` into provider-specific payloads, normalizes statuses, and handles idempotency.
4. **Activity registration** (optional) — if the provider needs Temporal activities for integration polling outside of `MoonMind.AgentRun`, activities can be registered on the `mm.activity.integrations` queue.

No changes to `MoonMind.Run`, `MoonMind.AgentRun`, or core orchestration logic are required. The `_agent_kind_for_id()` discriminator in `MoonMind.Run` automatically routes unrecognized agent IDs to `agent_kind="external"`.

## 6. Architectural Benefits

1. **Zero Sandboxing Overhead**: Sandbox isolation becomes the sole responsibility of the external agent or its hosting infrastructure. MoonMind passes instructions over HTTP; it does not map Docker sockets or manage untrusted runtimes.
2. **Orchestrator Focus**: MoonMind focuses on planning (`plan.generate`), artifact versioning, secure delegation, and output validation (`plan.validate`).
3. **True Plug-and-Play**: Integrating a new agent requires only a new adapter class that maps MoonMind's `AgentAdapter` contract to the agent's specific REST payload — no new worker classes, Docker images, or core infrastructure changes.
4. **Resilience**: `MoonMind.AgentRun` durably tracks external runs. A crash in the external system does not orphan the MoonMind workflow — the child workflow retries, times out, or escalates cleanly.
5. **Unified Lifecycle**: External and managed agents share the same `AgentAdapter` protocol and `MoonMind.AgentRun` lifecycle, enabling consistent monitoring, cancellation, and artifact handling across all agent types.
