# Technical Design: External Agent Integration System (BYOA)

## 1. Objective

Enable MoonMind to act as a definitive top-level orchestrator by defining a standardized "Bring Your Own Agent" (BYOA) protocol. This pattern leverages Temporal workflows and MCP tools to allow seamless, secure delegation to black-box or gray-box agents (e.g., OpenHands, OpenClaw, AutoGPT) without requiring MoonMind to sandbox, containerize, or manage the agent's internal execution runtime.

## 2. Architecture Overview: The 4-Layer Integration Model

Rather than building custom Docker worker fleets for every new agent flavor, MoonMind delegates work through a generic integration layer. This mirrors the proven `JulesClientAdapter` implementation but extracts it into a generalized pattern.

1. **Configuration Layer**: Dynamic endpoint URLs, API keys, and behavioral toggles loaded securely (e.g., `EXTERNAL_AGENT_API_URL`, `EXTERNAL_AGENT_API_KEY`) into generic `ExternalAgentSettings`.
2. **Adapter Layer**: A parameterized, async Python HTTP client (`ExternalAgentClient`) that manages generalized interactions: REST/RPC calls, standard retry behavior for 429s/5xx, timeout management, and rate-limiting.
3. **Temporal Activities**: A set of generic activities that run on the `mm.activity.integrations` task queue. These represent the integration lifecycle:
   - `integration.external_agent.start(agent_id, inputs_ref, parameters) -> external_run_id`
   - `integration.external_agent.status(external_run_id) -> status`
   - `integration.external_agent.fetch_result(external_run_id) -> output_refs[]`
   - `integration.external_agent.cancel(external_run_id)`
4. **MCP Tooling Layer**: A dynamic `ExternalAgentToolRegistry` that surfaces the external agent's exposed capabilities as native MoonMind MCP tools (e.g., `openhands.run_task`). This allows MoonMind's internal planning LLMs to discover and delegate work transparently.

## 3. The Context & Artifact Exchange Problem

Black-box agents require context (files, logs, plan definitions) and must return results, but they cannot be granted direct access to MoonMind's internal PostgreSQL or MinIO services.

This integration model solves the artifact boundary problem via two secure methods:

### Method A: Presigned URLs (The Default Payload Model)
When MoonMind calls `integration.external_agent.start`:
1. The integration worker uses the `TemporalArtifactStore` to generate short-lived, presigned download URLs for the `input_refs`.
2. It generates presigned upload URLs for the expected `output_refs`.
3. The external agent receives these URLs in its initialization payload, downloads its instructions via standard HTTP GET, performs its work, and HTTP PUTs the results back before completion.
4. MoonMind verifies the uploaded artifacts via `artifact.write_complete`.

### Method B: Reverse MCP (The Interactive Model)
If the external agent supports calling its own MCP tools during execution:
1. MoonMind provisions an ephemeral, restricted MCP server session specifically tied to the `external_run_id`.
2. This session exposes scoped capabilities, such as `moonmind.artifact.read` and `moonmind.artifact.write_complete`, bounded by the workflow's authorization context.
3. The external agent interactively requests context and submits artifacts directly via the MCP protocol without ever touching the underlying storage infrastructure.

## 4. Execution Flow: Callbacks vs. Polling

Consistent with the `ActivityCatalogAndWorkerTopology.md`, the integration fleet strongly favors **Callback-first** execution to preserve worker capacity.

### Callback-First Path (Preferred)
1. **Trigger**: MoonMind starts the workflow on the external agent and includes a webhook callback URL in the payload (e.g., `https://moonmind-api/callbacks/external_runs/{workflow_id}`).
2. **Suspend**: The Temporal workflow activity completes or suspends, instructing the workflow to await a Signal (`ExternalAgentCompletionSignal`). No workers block waiting for the agent.
3. **Execute**: The external agent runs for minutes or hours independently.
4. **Notify**: The external agent finishes and POSTs results/status to the webhook.
5. **Resume**: MoonMind processes the webhook, validates the payload signature, and Signals the waiting workflow to resume execution and collect artifacts.

### Polling Fallback Path
If the external agent lacks webhook capability:
1. MoonMind's workflow uses Temporal Timers to sleep (e.g., sleep 2 minutes).
2. It wakes up, executes a fast `integration.external_agent.status` activity, and goes back to sleep if the agent is still running.
3. This ensures no Temporal worker threads are held open during long idle periods.

## 5. Architectural Benefits over Custom Worker Containers

By defining this proxy boundary, MoonMind consolidates its identity as an **Orchestrator** rather than a mere application runtime:

1. **Zero Sandboxing Overhead**: Sandbox isolation (seccomp, resource limits, file systems) becomes the sole responsibility of the external agent or the execution infrastructure hosting it. MoonMind passes instructions over HTTP; it does not map Docker sockets or manage untrusted runtimes.
2. **Orchestrator Focus**: MoonMind focuses entirely on its core strengths: planning workflows (`plan.generate`), generating and versioning artifacts, securely delegating tasks, and validating outputs (`plan.validate`), sitting cleanly at the top of the stack.
3. **True Plug-and-Play**: Integrating a new agent paradigm (e.g., OpenClaw versus AutoGPT) no longer requires designing a new Python `Worker` class, managing its Docker image lifecycle, or defining new core infrastructure. It simply requires a lightweight adapter class that maps MoonMind's generic start/stop requests to the agent's specific REST payload.
4. **Resilience**: A crash in a custom Docker agent container no longer severs the Temporal connection or orphans the run. The integration layer explicitly handles external runtime failures and triggers appropriate MoonMind workflow retry policies.
