# Technical Design: OpenClaw Gateway (Streaming External Agent)

## 1. Objective

Describe how **OpenClaw** (autonomous agent gateway with an OpenAI-compatible HTTP API) is integrated as an **external** agent provider in MoonMind.

OpenClaw runs as an autonomous gateway: long-lived work (terminal access, file edits, multi-step reasoning) is exposed through an **OpenAI-compatible HTTP API**, including **`stream: true`** Server-Sent Events (SSE) on chat completions. MoonMind must treat that stream as the source of execution progress and final output, not as a short request/response RPC.

This document does **not** redefine MoonMind’s shared external-agent model. It narrows that model for one provider and calls out **one deliberate divergence**: OpenClaw is a **streaming gateway** provider, not a poll-by-external-id provider like Jules or Codex Cloud.

### Non-goals

- Defining OpenClaw product behavior inside the gateway (tool policies, sandboxing, model routing).
- Replacing MoonMind-managed CLI agents (Gemini CLI, Claude Code, Codex CLI).
- Mandating a specific OpenClaw release or fork; only the **HTTP contract** (OpenAI-compatible streaming) is assumed.

---

## 2. Canonical References

Implementation must remain consistent with:

- [`ExternalAgentIntegrationSystem.md`](./ExternalAgentIntegrationSystem.md) — universal external-agent stack (configuration gate, transport, adapter, orchestration).
- [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md) — `MoonMind.Run` / `MoonMind.AgentRun` lifecycle and shared contracts (`AgentExecutionRequest`, `AgentRunResult`, etc.).
- [`AddingExternalProvider.md`](./AddingExternalProvider.md) — step-by-step provider checklist (adapt where this doc overrides for streaming).

Operational visibility for streamed chunks should align with MoonMind’s **live task / log tailing** surfaces (dashboard and related APIs) where those exist.

---

## 3. OpenClaw in the Universal External-Agent Stack

OpenClaw should still be described using the same five concerns as every external provider:

| Layer | OpenClaw responsibility |
|--------|-----------------------------------|
| **1. Configuration and runtime gate** | Typed settings, env-driven enablement, safe defaults for gateway URL, model id, and timeouts. |
| **2. Provider transport** | Async HTTP client: POST `/v1/chat/completions` with `Accept: text/event-stream`, incremental SSE parsing, scrubbed errors. |
| **3. Provider adapter** | Map `AgentExecutionRequest` (and prepared context) into OpenAI-style `messages`; map aggregated assistant text into `AgentRunResult` (and optional artifact extraction later). |
| **4. Workflow orchestration** | **Differs from poll-based providers** — see §7. |
| **5. Optional tooling** | MCP or dashboard helpers may consume the same transport and gate; they are not the execution source of truth. |

---

## 4. Configuration and Secrets

### 4.1 Non-secret configuration

Runtime configuration lives in `moonmind/openclaw/settings.py` and includes:

- **`OPENCLAW_ENABLED`** — boolean gate; when false, OpenClaw does not register in the external adapter registry and integration activities are not advertised as available.
- **`OPENCLAW_GATEWAY_URL`** — base URL for the gateway (default for local dev: `http://127.0.0.1:18789`).
- **`OPENCLAW_DEFAULT_MODEL`** — model string sent in the chat-completions payload.
- **`OPENCLAW_TIMEOUT_SECONDS`** — upper bound for whole-run timeout semantics; the HTTP client avoids a tight read timeout so long streams are bounded by activity/workflow cancellation instead.

### 4.2 Secrets

- **`OPENCLAW_GATEWAY_TOKEN`** — bearer token for `Authorization: Bearer …`. Must never be committed. Resolve through the same MoonMind secret/environment mechanisms used for other integrations (see `moonmind/auth/` and related providers).

---

## 5. Provider Transport (HTTP + SSE)

### 5.1 Module

- `moonmind/workflows/adapters/openclaw_client.py` — async `httpx` client with SSE parsing.

### 5.2 Behavioral requirements

- **Streaming request**: JSON body includes `"stream": true` and OpenAI-style `model` + `messages`.
- **Headers**: `Content-Type: application/json`, `Accept: text/event-stream`, `Authorization` as above.
- **Timeouts**: Avoid a tight **read** timeout that would kill long autonomous loops; rely on combined **activity** / **workflow** timeout and explicit cancellation. The `httpx.Timeout` shape is documented in `openclaw_client.py`.
- **SSE parsing**: For each `data:` line, strip prefix; treat `[DONE]` as stream end; parse JSON; extract incremental `choices[0].delta.content` (or documented OpenClaw extensions if they differ — normalize in one place).
- **Errors**: Non-2xx responses must raise a provider-specific, testable exception with secrets scrubbed from messages.
- **Cancellation**: When the consumer stops reading (e.g. Temporal activity cancellation), the HTTP connection should close so the gateway can observe disconnect (best-effort **implicit cancel** without requiring a separate OpenClaw cancel REST call in v1).

---

## 6. Adapter Layer

### 6.1 Module

- `moonmind/workflows/adapters/openclaw_agent_adapter.py` — registry adapter and translation helpers; poll hooks raise because execution uses the streaming activity only.

### 6.2 Contract alignment

The shared runtime contract is `AgentAdapter` in `moonmind/workflows/adapters/agent_adapter.py`. OpenClaw uses **`execution_style="streaming_gateway"`** on `ProviderCapabilityDescriptor` so `MoonMind.AgentRun` takes the **single long-running activity** path (§7) instead of start/status/fetch. See [`AddingExternalProvider.md`](./AddingExternalProvider.md) for the pattern used by poll-based providers; OpenClaw is the streaming variant.

### 6.3 Translation rules

- **System message**: Stable instruction that the model acts as the MoonMind-delegated OpenClaw agent for a bounded task.
- **User message**: Combine structured task instructions from `AgentExecutionRequest` with any prepared workspace/context text (equivalent to the former “context files” bundle), using the same artifact/context preparation rules as other external agents where applicable.
- **Result**: Build `AgentRunResult` with terminal success state and aggregated assistant text as the primary output. **Artifact extraction** from tagged tool output is explicitly **deferred** unless OpenClaw defines a stable machine-readable format.

### 6.4 Capability descriptor

`ProviderCapabilityDescriptor` includes **`execution_style`**: `polling` | `streaming_gateway` (`moonmind/schemas/agent_runtime_models.py`). OpenClaw registers as **`streaming_gateway`** with **`supports_callbacks: false`**; `MoonMind.AgentRun` routes streaming providers to `integration.openclaw.execute` instead of the poll loop.

---

## 7. Temporal orchestration (streaming path)

### 7.1 Poll loop vs streaming

For **`execution_style == polling`**, `MoonMind.AgentRun` uses `integration.<agent_id>.start`, repeated `integration.<agent_id>.status`, and `integration.<agent_id>.fetch_result` (Jules, Codex Cloud).

For **`execution_style == streaming_gateway`** (OpenClaw), a single long-lived SSE stream **is** the run, so the workflow uses one activity instead of that loop.

### 7.2 Behavior

1. After adapter resolution, the workflow invokes **`integration.openclaw.execute`** (see `activity_runtime.py` / `moonmind/openclaw/execute.py`).
2. That activity resolves settings and `OPENCLAW_GATEWAY_TOKEN`, opens the SSE stream, buffers assistant text, emits **`activity.heartbeat(...)`** on chunks (or throttled) for live surfaces, and returns an **`AgentRunResult`**-compatible payload.
3. Execution continues with the existing **`agent_runtime.publish_artifacts`** step.

**Cancellation:** Activity cancellation tears down the httpx stream (implicit cancel on disconnect); this is the primary cancel path for OpenClaw.

### 7.3 Registration

- `integration.openclaw.execute` is registered in the activity catalog and wired on the integrations fleet (`activity_runtime.py`).
- `openclaw` is registered in `build_default_registry()` when the runtime gate passes.
- Separate `integration.openclaw.start` / `status` / `fetch_result` activities are not used; the adapter’s poll hooks raise if called.

### 7.4 Heartbeat and timeout policy

Heartbeat timeout and start-to-close limits for `integration.openclaw.execute` are defined next to the activity registration so stalled gateways fail fast while long autonomous runs remain possible within the start-to-close budget.

---

## 8. Registry and Agent Identity

- **Canonical `agent_id`**: `openclaw` (lowercase), registered in `ExternalAdapterRegistry`.
- Alternate aliases should only be added if product or API compatibility requires them (mirror `jules` / `jules_api` only if necessary).

---

## 9. Testing and verification

Unit coverage:

- `tests/unit/workflows/adapters/test_openclaw_client.py` — SSE parsing and transport errors.
- `tests/unit/workflows/adapters/test_openclaw_agent_adapter.py` — adapter registration and translation.

Run via `./tools/test_unit.sh` (or focused paths under `tests/unit/workflows/adapters/`). Optional integration tests against a live gateway remain environment-gated.

---

## 10. Summary

| Topic | State |
|--------|----------------|
| **Integration style** | External agent via OpenAI-compatible **streaming** gateway |
| **Transport** | Dedicated async HTTP client, SSE parsing, implicit cancel on disconnect |
| **Orchestration** | **Single** execute activity + workflow branch in `MoonMind.AgentRun`; not the standard external poll loop |
| **Liveness / UX** | Temporal **heartbeats** carrying stream chunks or throttled deltas for live surfaces |
| **Configuration** | Typed settings + `OPENCLAW_GATEWAY_TOKEN` via MoonMind secret resolution |
| **Identity** | `agent_id` = `openclaw` in `ExternalAdapterRegistry` |

OpenClaw is the **streaming-gateway** profile of the universal external-agent system: same stack as poll-based providers, with `execution_style="streaming_gateway"` and the single execute activity in §7 instead of the poll loop.
