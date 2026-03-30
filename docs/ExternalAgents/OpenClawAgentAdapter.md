# Technical Design: OpenClaw Gateway (Streaming External Agent)

Status: **Implemented as streaming-gateway external provider**
Last updated: 2026-03-30
Related:
- [`./ExternalAgentIntegrationSystem.md`](./ExternalAgentIntegrationSystem.md)
- [`./AddingExternalProvider.md`](./AddingExternalProvider.md)
- [`../Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`../Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
- [`../Temporal/ErrorTaxonomy.md`](../Temporal/ErrorTaxonomy.md)

---

## 1. Objective

Describe how **OpenClaw** is integrated as an **external** agent provider in MoonMind.

OpenClaw is treated as an autonomous gateway exposed through an OpenAI-compatible HTTP API, including `stream: true` Server-Sent Events on chat completions. MoonMind must treat that stream as the execution surface for progress and final output rather than as a short request/response RPC.

This document does **not** define a separate OpenClaw-only external-agent model. It narrows MoonMind’s shared external-agent architecture for one provider and calls out one deliberate difference:

> OpenClaw is a **streaming-gateway** provider, not a poll-by-external-id provider like Jules or Codex Cloud.

### Non-goals

- defining OpenClaw product behavior inside the gateway
- replacing MoonMind-managed CLI runtimes
- mandating a specific OpenClaw release or fork beyond the HTTP contract MoonMind depends on

---

## 2. Canonical references

Implementation must remain consistent with:

- [`ExternalAgentIntegrationSystem.md`](./ExternalAgentIntegrationSystem.md)
- [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`AddingExternalProvider.md`](./AddingExternalProvider.md)

Operational visibility for streamed chunks should align with MoonMind’s live log / live execution surfaces where those exist.

---

## 3. OpenClaw in the universal external-agent stack

OpenClaw should still be described using the same five concerns as every external provider.

| Layer | OpenClaw responsibility |
|---|---|
| **1. Configuration and runtime gate** | Typed settings, env-driven enablement, safe defaults for gateway URL, model ID, and timeouts |
| **2. Provider transport** | Async HTTP client that posts to the OpenAI-compatible endpoint and parses SSE |
| **3. Provider adapter** | Map `AgentExecutionRequest` into OpenAI-style messages and declare `execution_style="streaming_gateway"` |
| **4. Workflow orchestration** | Branch to a single execute activity instead of a start/status/fetch polling loop |
| **5. Optional tooling** | Any MCP or dashboard helpers reuse the same transport and runtime-gate rules |

OpenClaw should not be described as a separate architecture. It is the **streaming-gateway profile** of MoonMind’s universal external-agent system.

---

## 4. Configuration and secrets

## 4.1 Non-secret configuration

Runtime configuration lives behind typed settings and should include values such as:

- `OPENCLAW_ENABLED`
- `OPENCLAW_GATEWAY_URL`
- `OPENCLAW_DEFAULT_MODEL`
- `OPENCLAW_TIMEOUT_SECONDS`

These values determine:

- whether OpenClaw is enabled
- where the gateway is located
- which default model to request
- the upper bound for execution behavior at the provider/client level

## 4.2 Secrets

OpenClaw auth should be provided through MoonMind’s normal secret or environment handling.

Representative secret:

- `OPENCLAW_GATEWAY_TOKEN`

Rules:

- never commit it
- never place it in workflow payloads
- never emit it in logs or diagnostics artifacts
- resolve it at the activity boundary, not in deterministic workflow code

---

## 5. Provider transport

## 5.1 Module

Representative module:

- `moonmind/workflows/adapters/openclaw_client.py`

This module is the low-level HTTP + SSE transport wrapper.

## 5.2 Behavioral requirements

The client should:

- send an OpenAI-compatible chat-completions request
- include `stream: true`
- use `Accept: text/event-stream`
- include auth headers
- parse SSE incrementally
- treat `[DONE]` as stream termination
- extract incremental assistant content from stream events
- raise scrubbed, testable transport errors on non-2xx responses or malformed streams

## 5.3 Timeout model

The transport should avoid tight per-read timeouts that would incorrectly kill long autonomous runs.

Instead, OpenClaw streaming should be bounded primarily by:

- activity timeout policy
- workflow timeout policy
- explicit Temporal cancellation
- stream heartbeat/liveness expectations

## 5.4 Cancellation model

When the consuming activity is canceled, the HTTP stream should be closed promptly.

That disconnect is the primary best-effort cancel signal for OpenClaw in v1. MoonMind does not need to pretend it has a richer provider-native cancel protocol if the gateway’s real behavior is “disconnect closes the stream.”

---

## 6. Adapter layer

## 6.1 Module

Representative module:

- `moonmind/workflows/adapters/openclaw_agent_adapter.py`

This adapter participates in the shared external-agent architecture, but OpenClaw is not a normal poll-based provider.

## 6.2 Contract alignment

The shared runtime contract is still `AgentAdapter`, but OpenClaw advertises:

- `execution_style="streaming_gateway"`

That declaration tells `MoonMind.AgentRun` to take the **single execute activity** path rather than the standard external polling loop.

## 6.3 Adapter responsibilities

The OpenClaw adapter should:

- validate that the request is for an external OpenClaw run
- shape `AgentExecutionRequest` into OpenAI-style message input
- declare the correct provider capability descriptor
- preserve MoonMind correlation metadata where appropriate
- keep provider-specific translation logic below the workflow boundary

## 6.4 What the adapter should not do

The adapter should not try to simulate a fake polling model if the provider is truly stream-based.

It should also not force provider-native payloads into workflow code. Even though the orchestration path differs, the workflow-facing result still needs to be canonical.

---

## 7. Canonical runtime contract boundary

The key rule is unchanged:

> Normalize at the adapter/activity boundary, not in workflow code.

For OpenClaw, that means the streaming execute path must return a canonical MoonMind runtime result.

## 7.1 Core contract expectation

The OpenClaw execution activity must return:

- `AgentRunResult`

It should not return:

- raw OpenAI/OpenClaw chunk payloads
- provider-native terminal dicts
- arbitrary stream-aggregation structures that `MoonMind.AgentRun` must repair

## 7.2 Metadata rules

Provider-specific details may be included inside canonical `metadata`, for example:

- provider URL
- model ID
- stream termination details
- chunk counts
- gateway-specific identifiers
- any normalized provider-specific diagnostic summaries

## 7.3 Contract-failure rule

If the OpenClaw activity cannot produce a valid canonical `AgentRunResult`, that is a contract failure at the activity boundary and should generally be treated as non-retryable unless the problem is clearly a transient transport failure before a real result could be formed.

---

## 8. Temporal orchestration (streaming path)

## 8.1 Polling vs streaming

For normal poll-based external providers, `MoonMind.AgentRun` uses:

- `integration.<provider>.start`
- `integration.<provider>.status`
- `integration.<provider>.fetch_result`
- `integration.<provider>.cancel`

For OpenClaw, the stream itself **is** the run, so `MoonMind.AgentRun` takes a different branch.

## 8.2 Streaming-gateway behavior

The standard OpenClaw flow is:

1. resolve adapter metadata and confirm `execution_style="streaming_gateway"`
2. invoke `integration.openclaw.execute`
3. stream assistant output through one long-lived activity
4. emit heartbeats during the stream
5. aggregate the final assistant output
6. return a canonical `AgentRunResult`
7. continue with the normal artifact publication path

## 8.3 Registration model

OpenClaw should be registered as:

- provider identity: `openclaw`
- execution style: `streaming_gateway`

And the live activity surface should expose:

- `integration.openclaw.execute`

The normal polling activity family is not the primary execution model for OpenClaw.

## 8.4 Heartbeat and liveness policy

The execute activity should heartbeat during the stream so that:

- stalled streams can fail fast
- live surfaces can observe progress
- the system has bounded liveness expectations

The heartbeat payloads should remain compact and should not dump entire raw provider chunk objects into activity heartbeats.

---

## 9. Execute activity design

## 9.1 Purpose

`integration.openclaw.execute` is the OpenClaw runtime activity that owns:

- stream setup
- SSE consumption
- chunk aggregation
- heartbeat emission
- terminal result construction

## 9.2 Input shaping

The execute activity should receive a MoonMind-oriented request and translate it into an OpenAI-style prompt/message payload using:

- stable system instructions
- user-facing task instructions
- any prepared context bundle text
- any compact artifact-backed context that has already been materialized for external execution

This keeps OpenClaw integrated with the same broader MoonMind request-preparation model used by other external providers.

## 9.3 Output shaping

The execute activity should aggregate the final assistant text into a canonical `AgentRunResult`.

Representative fields:

- `summary`
- `output_refs[]` if artifactized outputs are created
- `diagnostics_ref` if long diagnostics are stored separately
- `metadata` for provider-specific execution details

Artifact extraction from machine-readable tagged stream segments can be added later if OpenClaw defines a stable enough format, but that is not required for the core integration model.

---

## 10. Identity and registry

OpenClaw should use one canonical provider identity:

- `openclaw`

That identity should be registered in the external adapter registry when the runtime gate passes.

Aliases should only be introduced if a real compatibility need exists.

---

## 11. Testing and verification

## 11.1 Transport tests

Test the client for:

- SSE parsing
- `[DONE]` handling
- non-2xx errors
- malformed chunk handling
- scrubbed error behavior
- disconnect/cancellation cleanup

## 11.2 Adapter tests

Test the adapter for:

- provider registration
- capability descriptor correctness
- execution-style declaration
- message translation behavior

## 11.3 Activity tests

Test `integration.openclaw.execute` for:

- canonical `AgentRunResult` return shape
- heartbeat behavior
- stream aggregation behavior
- cancellation/disconnect behavior
- malformed provider event handling

## 11.4 Integration tests

Optional environment-gated tests may verify a live OpenClaw gateway, but the core correctness boundary is:

- transport correctness
- canonical result shaping
- orchestration branch correctness

---

## 12. Design rules

The OpenClaw-specific rules are:

1. treat OpenClaw as a streaming-gateway external provider, not as a fake polling provider
2. keep transport and SSE parsing in the provider client
3. keep execution-style declaration in the provider capability descriptor
4. keep workflow orchestration provider-neutral aside from the execution-style branch
5. return canonical `AgentRunResult` from the execute activity
6. use heartbeats for liveness and live progress visibility
7. treat disconnect as the primary best-effort cancel mechanism unless a richer provider-native cancel protocol is deliberately added later
8. do not leak raw provider stream payloads into workflow code

---

## 13. Summary

OpenClaw is the **streaming-gateway** profile of MoonMind’s universal external-agent system.

The correct model is:

- one runtime gate
- one transport client for OpenAI-compatible streaming
- one provider adapter that declares `execution_style="streaming_gateway"`
- one execute activity for the full streamed run
- one canonical `AgentRunResult` crossing the workflow boundary

In short:

- OpenClaw is still an external provider in the same shared architecture
- it differs only in execution style
- the stream is the run
- canonical contract shaping still happens at the adapter/activity boundary, not in workflow code
