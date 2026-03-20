# Technical Design: OpenClaw Gateway (Streaming External Agent)

## 1. Objective

Declare the **desired state** for integrating **OpenClaw** (autonomous agent gateway with an OpenAI-compatible HTTP API) as an **external** agent provider in MoonMind.

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

| Layer | OpenClaw desired responsibility |
|--------|-----------------------------------|
| **1. Configuration and runtime gate** | Typed settings, env-driven enablement, safe defaults for gateway URL, model id, and timeouts. |
| **2. Provider transport** | Async HTTP client: POST `/v1/chat/completions` with `Accept: text/event-stream`, incremental SSE parsing, scrubbed errors. |
| **3. Provider adapter** | Map `AgentExecutionRequest` (and prepared context) into OpenAI-style `messages`; map aggregated assistant text into `AgentRunResult` (and optional artifact extraction later). |
| **4. Workflow orchestration** | **Differs from poll-based providers** — see §7. |
| **5. Optional tooling** | MCP or dashboard helpers may consume the same transport and gate; they are not the execution source of truth. |

---

## 4. Configuration and Secrets

### 4.1 Non-secret configuration

Desired artifacts (names may follow existing patterns such as `moonmind/jules/runtime.py` / `moonmind/codex_cloud/settings.py`):

- **`OPENCLAW_ENABLED`** — boolean gate; when false, OpenClaw must not register in the external adapter registry and activities must not be advertised as available.
- **`OPENCLAW_GATEWAY_URL`** — base URL for the gateway (desired default for local dev: `http://127.0.0.1:18789`).
- **`OPENCLAW_DEFAULT_MODEL`** — model string sent in the chat-completions payload (provider-defined; placeholder default acceptable until gateway contract is fixed).
- **`OPENCLAW_TIMEOUT_SECONDS`** — upper bound for “whole run” timeout semantics (desired default: `3600`). Read timeout on the HTTP client should allow indefinite read while a total/activity boundary enforces cancellation.

Typed settings should live in a dedicated module (for example `moonmind/openclaw/settings.py` or `moonmind/config/openclaw_settings.py`) and be composed into the central settings object if that is the project norm.

### 4.2 Secrets

- **`OPENCLAW_GATEWAY_TOKEN`** — bearer token for `Authorization: Bearer …`. Must never be committed. Resolve through the same MoonMind secret/environment mechanisms used for other integrations (see `moonmind/auth/` and related providers).

---

## 5. Provider Transport (HTTP + SSE)

### 5.1 Desired module

- `moonmind/workflows/adapters/openclaw_client.py` — thin `httpx` async client.

### 5.2 Behavioral requirements

- **Streaming request**: JSON body includes `"stream": true` and OpenAI-style `model` + `messages`.
- **Headers**: `Content-Type: application/json`, `Accept: text/event-stream`, `Authorization` as above.
- **Timeouts**: Avoid a tight **read** timeout that would kill long autonomous loops; rely on combined **activity** / **workflow** timeout and explicit cancellation. Document the chosen `httpx.Timeout` shape in code comments when implementing.
- **SSE parsing**: For each `data:` line, strip prefix; treat `[DONE]` as stream end; parse JSON; extract incremental `choices[0].delta.content` (or documented OpenClaw extensions if they differ — normalize in one place).
- **Errors**: Non-2xx responses must raise a provider-specific, testable exception with secrets scrubbed from messages.
- **Cancellation**: When the consumer stops reading (e.g. Temporal activity cancellation), the HTTP connection should close so the gateway can observe disconnect (best-effort **implicit cancel** without requiring a separate OpenClaw cancel REST call in v1).

---

## 6. Adapter Layer

### 6.1 Desired module

- `moonmind/workflows/adapters/openclaw_agent_adapter.py`

### 6.2 Contract alignment

The shared runtime contract is `AgentAdapter` (`start`, `status`, `fetch_result`, `cancel`) in `moonmind/workflows/adapters/agent_adapter.py`, typically implemented via `BaseExternalAgentAdapter` hooks as described in [`AddingExternalProvider.md`](./AddingExternalProvider.md).

**Desired state for v1 (choose one approach in implementation; document the choice in the PR):**

1. **Streaming-primary (recommended)**
   OpenClaw does not map cleanly to external-id polling. Implement a **dedicated execution path** (§7) that calls transport + translation directly from a single Temporal activity. The class in `openclaw_agent_adapter.py` then focuses on **request translation** and **result normalization** (pure functions or a small service), while `MoonMind.AgentRun` does not use `start` → `status` → `fetch_result` for this provider.

2. **Shim adapter (discouraged)**
   A synthetic external id with no real poll semantics would mislead operators and complicate cancellation; avoid unless a hard compatibility constraint appears.

### 6.3 Translation rules (desired)

- **System message**: Stable instruction that the model acts as the MoonMind-delegated OpenClaw agent for a bounded task.
- **User message**: Combine structured task instructions from `AgentExecutionRequest` with any prepared workspace/context text (equivalent to the former “context files” bundle), using the same artifact/context preparation rules as other external agents where applicable.
- **Result**: Build `AgentRunResult` with terminal success state and aggregated assistant text as the primary output. **Artifact extraction** from tagged tool output is explicitly **deferred** unless OpenClaw defines a stable machine-readable format.

### 6.4 Capability descriptor

Today, `ProviderCapabilityDescriptor` describes poll-oriented providers. **Desired extension** (schema + codegen if needed):

- Add something equivalent to **`execution_style`**: `polling` | `streaming_gateway`.

OpenClaw should register as **`streaming_gateway`** with **`supports_callbacks: false`**, and polling-related hints should be ignored by `MoonMind.AgentRun` when this style is active.

---

## 7. Temporal Orchestration (Required Divergence)

### 7.1 Problem

`MoonMind.AgentRun` today assumes **external** agents use:

1. `integration.<agent_id>.start`
2. Repeated `integration.<agent_id>.status` until terminal
3. `integration.<agent_id>.fetch_result`

That pattern matches Jules and Codex Cloud. It does **not** match a single long-lived SSE connection that **is** the run.

### 7.2 Desired behavior

For providers with `execution_style == streaming_gateway` (initially `openclaw` only):

1. After `integration.resolve_external_adapter` validates registration, the workflow should invoke **one** long-running activity, for example:
   - `integration.openclaw.execute`
2. That activity:
   - Resolves settings and `OPENCLAW_GATEWAY_TOKEN`
   - Opens the SSE stream
   - Appends text chunks to the final buffer
   - Calls **`activity.heartbeat(...)`** with a meaningful payload on each chunk (or on a throttled schedule) so Temporal records liveness and downstream systems can surface **incremental log/output**
   - Returns a serialized **`AgentRunResult`** (or dict equivalent) on successful completion
3. The workflow then continues with the existing **`agent_runtime.publish_artifacts`** step, unchanged.

**Cancellation:** Rely on **activity cancellation** (and thus httpx stream teardown) as the primary cancel path. The workflow’s generic `integration.<id>.cancel` path may be a no-op for OpenClaw if `run_id` is unused, provided cancellation is still correct via the execute activity’s cancellation semantics.

### 7.3 Activity catalog and worker registration

- Register `integration.openclaw.execute` in `moonmind/workflows/temporal/activity_catalog.py` and wire it in `activity_runtime.py` / integrations fleet the same way as other integration activities.
- Register `openclaw` in `build_default_registry()` in `moonmind/workflows/adapters/external_adapter_registry.py` **only when** the runtime gate passes (mirror Jules / Codex Cloud).
- **`integration.openclaw.start` / `status` / `fetch_result` / `cancel`** are **not** required for v1 if the streaming activity is the sole path; avoid registering dead activity types.

### 7.4 Heartbeat and timeout policy

- Configure **heartbeat timeout** for the execute activity so that stalled gateways fail fast relative to product expectations, distinct from the generous **start-to-close** budget for long tasks.
- Document chosen values next to the activity definition when implementing.

---

## 8. Registry and Agent Identity

- **Canonical `agent_id`**: `openclaw` (lowercase), registered in `ExternalAdapterRegistry`.
- Alternate aliases should only be added if product or API compatibility requires them (mirror `jules` / `jules_api` only if necessary).

---

## 9. Testing and Verification (Desired)

1. **Unit tests** — SSE parsing with fixture lines; adapter translation with frozen `AgentExecutionRequest` samples; error paths and JSON decode resilience.
2. **Activity tests** — Mock `openclaw_client` to emit async chunks; assert `heartbeat` calls and final `AgentRunResult` shape.
3. **Integration tests** (optional, environment-gated) — Against a real local OpenClaw gateway in CI only if stable fixtures exist.

Suggested command entrypoints should match repo norms (e.g. `./tools/test_unit.sh` with focused paths under `tests/unit/workflows/adapters/`).

---

## 10. Implementation Checklist

Use this as a merge-ready sequence:

1. OpenClaw settings module + runtime gate + env documentation.
2. `OpenClawHttpClient` with streaming generator and tests.
3. Request/result translation module (adapter or pure helpers).
4. Temporal activity `integration.openclaw.execute` with heartbeats and cancellation behavior.
5. Extend `ProviderCapabilityDescriptor` and `MoonMind.AgentRun` branching for `streaming_gateway`.
6. Activity catalog + integrations fleet wiring.
7. `build_default_registry()` registration behind gate.
8. Dashboard / live-log consumer verification if heartbeat payloads are consumed by existing plumbing.

---

## 11. Summary

| Topic | Desired state |
|--------|----------------|
| **Integration style** | External agent via OpenAI-compatible **streaming** gateway |
| **Transport** | Dedicated async HTTP client, SSE parsing, implicit cancel on disconnect |
| **Orchestration** | **Single** execute activity + workflow branch in `MoonMind.AgentRun`; not the standard external poll loop |
| **Liveness / UX** | Temporal **heartbeats** carrying stream chunks or throttled deltas for live surfaces |
| **Configuration** | Typed settings + `OPENCLAW_GATEWAY_TOKEN` via MoonMind secret resolution |
| **Identity** | `agent_id` = `openclaw` in `ExternalAdapterRegistry` |

OpenClaw is intentionally a **streaming-gateway** profile of the universal external-agent system. Implementing it requires the small, explicit **orchestration extension** in §7; the rest should follow the same layering discipline as Jules and Codex Cloud.
