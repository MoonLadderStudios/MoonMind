# Technical Design: External Agent Integration System

Status: **Implemented in core architecture** (provider coverage and helper surfaces continue to expand)
Last updated: 2026-03-30
Related:
- [`../Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`../Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
- [`./AddingExternalProvider.md`](./AddingExternalProvider.md)
- [`../Temporal/ErrorTaxonomy.md`](../Temporal/ErrorTaxonomy.md)

---

## 1. Objective

Define one canonical architecture for MoonMind external-agent integrations.

The goal is to stop describing each provider as its own mini-architecture and instead treat providers such as Jules, Codex Cloud, and future BYOA integrations as implementations of one shared external-agent system with:

- one generic orchestration lifecycle
- one canonical `AgentAdapter` contract
- one shared external-adapter base pattern for poll-based providers
- provider-specific transport and status mapping behind that boundary
- canonical runtime contracts crossing the workflow boundary

For the full execution model covering both managed and external agents, see [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md).

---

## 2. Canonical mental model

Older docs drifted because they described different slices of the same system:

- one document described external execution as a compact stack
- provider-specific docs described transport, tooling, and orchestration as if they were separate architectures

They are not competing designs. They are different views of one system.

MoonMind should describe every external-agent integration using the same five concerns.

---

## 3. The universal external-agent stack

MoonMind should describe every external provider using the same five layers.

## 3.1 Layer 1: configuration and runtime gate

This layer answers:

> Is this provider enabled, configured, and safe to use?

Responsibilities:

- provider enablement
- endpoints
- auth references
- feature flags
- transport defaults
- timeout/retry defaults
- runtime-gate checks

Examples:

- provider settings modules
- runtime gate helpers such as `is_jules_runtime_enabled()`

This layer does **not** own workflow lifecycle semantics.

## 3.2 Layer 2: provider transport

This layer owns the provider’s native protocol.

Responsibilities:

- REST/RPC request and response shapes
- provider auth headers
- transport retries and timeout handling
- scrubbed transport errors

Examples:

- provider schema modules
- provider client code such as:
  - `jules_client.py`
  - `codex_cloud_client.py`

This layer does **not** own MoonMind lifecycle or canonical workflow contracts.

## 3.3 Layer 3: universal external agent adapter

This is the key standardization point.

Every external provider plugs into one generic adapter contract:

- `start(request: AgentExecutionRequest) -> AgentRunHandle`
- `status(run_id) -> AgentRunStatus`
- `fetch_result(run_id) -> AgentRunResult`
- `cancel(run_id) -> AgentRunStatus`

This layer owns:

- request validation for external-agent requests
- correlation metadata injection
- idempotency behavior
- provider status normalization
- canonical result shaping
- truthful cancel semantics
- compact metadata enrichment

In the current codebase, this boundary is represented by:

- `moonmind/workflows/adapters/agent_adapter.py`
- `moonmind/workflows/adapters/external_adapter_registry.py`
- `moonmind/workflows/adapters/base_external_agent_adapter.py`
- provider adapters such as:
  - `jules_agent_adapter.py`
  - `codex_cloud_agent_adapter.py`

### Canonical contract rule

The adapter boundary is where provider-native payloads become MoonMind runtime contracts.

That means provider-specific response shapes must be normalized **before** they reach workflow code.

Workflow code should receive only canonical contracts such as:

- `AgentRunHandle`
- `AgentRunStatus`
- `AgentRunResult`

Provider-specific details belong in canonical `metadata`, not provider-shaped top-level response dicts.

## 3.4 Layer 4: workflow orchestration

`MoonMind.AgentRun` owns the generic execution lifecycle for all true agent runs.

This layer owns:

- start via integration activity
- durable waiting
- callback handling
- polling fallback
- timeout handling
- cancellation cleanup
- artifact publication
- normalized result return to the parent workflow

This layer should remain provider-neutral apart from:

- adapter selection
- execution-style branching where the provider capability requires it

It should not contain provider-specific transport parsing logic.

## 3.5 Layer 5: optional tooling and operator surfaces

These surfaces are useful, but they are not the core execution architecture.

Examples:

- MCP tooling
- REST helper endpoints
- dashboard widgets
- provider-specific debugging/admin surfaces

These should consume the same transport and adapter boundaries rather than redefine provider lifecycle semantics.

---

## 4. Target shape in code

The external-agent stack is implemented as:

- shared `AgentAdapter` protocol
- `MoonMind.AgentRun` child workflow
- `ExternalAdapterRegistry` and `build_default_registry()`
- provider-specific transport clients and schemas
- `BaseExternalAgentAdapter` for poll-oriented providers
- provider-specific Temporal integration activities
- helper activity `integration.resolve_adapter_metadata` for execution-style and adapter validation

This gives MoonMind one common architecture across providers instead of a separate orchestration model per provider.

---

## 5. Universal external adapter base

`BaseExternalAgentAdapter` is the standard extension point for poll-oriented external providers.

It supplies:

- provider capability descriptor integration
- shared helpers for correlation metadata
- idempotency-oriented behavior
- normalized `AgentRunHandle` construction
- normalized `AgentRunStatus` construction
- normalized `AgentRunResult` construction
- best-effort cancel fallback when hard cancel is unavailable

## 5.1 Base class responsibilities

The base implementation should own the generic external-agent rules that repeat across providers:

1. validate that the request is an external-agent request for the expected provider
2. inject MoonMind correlation metadata consistently
3. apply stable idempotency behavior
4. centralize common metadata fields such as:
   - `providerStatus`
   - `normalizedStatus`
   - `externalUrl`
   - callback hints
5. centralize best-effort cancel semantics when the provider lacks hard cancellation
6. keep workflow-facing contracts compact and artifact-ref-based

## 5.2 Subclass responsibilities

Provider subclasses should own only the provider-specific parts:

1. translate `AgentExecutionRequest` into provider-native transport payloads
2. map provider lifecycle states into canonical MoonMind states
3. extract provider result summaries and artifact-worthy outputs
4. implement provider-specific cancel behavior
5. advertise capabilities such as:
   - callbacks
   - cancel support
   - result fetch support
   - execution style

---

## 6. Canonical runtime contract boundary

The most important rule for external integrations is:

> Normalize at the adapter or activity boundary, not in workflow code.

External integrations must present canonical lifecycle contracts to the rest of MoonMind.

## 6.1 Required contract surface

For standard poll-based providers:

- `integration.<provider>.start(...) -> AgentRunHandle`
- `integration.<provider>.status(...) -> AgentRunStatus`
- `integration.<provider>.fetch_result(...) -> AgentRunResult`
- `integration.<provider>.cancel(...) -> AgentRunStatus`

## 6.2 What is allowed in metadata

Provider-specific fields may be included inside canonical `metadata`, for example:

- provider URLs
- callback support flags
- provider tracking references
- raw provider status labels
- PR URLs
- provider-side summary identifiers

## 6.3 What is not allowed

Do not rely on provider-shaped workflow-facing top-level payloads such as:

- `{external_id, tracking_ref}`
- `{status: "provider_specific_state"}`
- arbitrary dicts that the workflow must coerce into `AgentRunStatus`
- provider-specific result dicts that the workflow must repair into `AgentRunResult`

Contract-shape enforcement belongs at the adapter or integration activity boundary.

---

## 7. Execution styles

External providers may not all execute the same way, so capability metadata must declare the execution style.

## 7.1 Polling

This is the default style.

Pattern:

1. start external run
2. wait durably
3. poll status or process callbacks
4. fetch final result
5. cancel if needed

This is the standard model for providers such as Jules and Codex Cloud.

## 7.2 Streaming gateway

Some providers may use a one-shot execution path rather than a start/status/fetch loop.

Current example:

- OpenClaw-style execution

In this style, the provider may expose a single activity path like:

- `integration.<provider>.execute(...) -> AgentRunResult`

This style still fits the same architectural model:

- transport is provider-specific
- capability declaration is provider-specific
- orchestration remains centralized
- the returned payload must still be canonical

The main difference is that the orchestration branch in `MoonMind.AgentRun` chooses an execute-style path instead of a poll loop.

---

## 8. Workflow orchestration behavior

`MoonMind.AgentRun` should remain the single lifecycle owner for external agent execution.

It owns:

- the durable wait
- callback vs polling choice
- timeout enforcement
- cancellation cleanup
- result handoff to the parent workflow

It should not own:

- provider-native request shape parsing
- provider-native status normalization
- provider-specific result schema repair

Those belong in adapters and integration activities.

### 8.1 Helper activity note

`integration.resolve_adapter_metadata` exists to keep certain nondeterministic adapter/registry inspection logic out of workflow code.

Its role is limited:

- validate adapter availability
- expose execution metadata such as execution style

It is a workflow-support helper, not a provider-lifecycle contract surface.

---

## 9. Jules in the standard model

Under this model, Jules is not “a separate architecture.”

Jules is:

- one provider configuration/runtime gate
- one provider transport client and schema set
- one provider-specific adapter subclass
- one set of integration activities
- one optional set of tooling/operator surfaces

The correct description is:

> Jules is a reference provider implementation for the universal external-agent adapter pattern.

Not:

> Jules defines its own separate execution architecture.

The same framing applies to Codex Cloud and future providers.

---

## 10. Tooling boundaries and reference integrations

Tooling surfaces such as MCP, dashboards, or provider-specific admin helpers should consume the same centralized provider boundaries rather than redefining provider behavior.

## 10.1 `ProviderCapabilityDescriptor`

`ProviderCapabilityDescriptor` describes runtime behavior such as:

- `supports_callbacks`
- `supports_cancel`
- `supports_result_fetch`
- `provider_name`
- `default_poll_hint_seconds`
- `execution_style`

This descriptor is what lets MoonMind orchestration remain mostly provider-neutral while still choosing the right execution path.

## 10.2 Poll-based providers

Poll-based providers:

- extend `BaseExternalAgentAdapter`
- use the standard start/status/fetch/cancel activity family
- normalize provider statuses into canonical MoonMind runtime states

Examples:

- Jules
- Codex Cloud

## 10.3 Streaming providers

Streaming-style providers:

- advertise `execution_style="streaming_gateway"`
- use a dedicated execute-style activity path
- still return canonical `AgentRunResult`

Example:

- OpenClaw

## 10.4 Adding a new provider

For the step-by-step provider addition flow, see [`AddingExternalProvider.md`](./AddingExternalProvider.md).

That guide covers:

- runtime gate
- client
- adapter
- activity catalog registration
- worker registration
- canonical contract requirements

---

## 11. Architectural benefits

This architecture gives MoonMind:

1. one mental model for all external agents
2. one place to define canonical runtime contracts
3. provider-specific transport isolated from workflow orchestration
4. provider addition as adapter/activity work rather than workflow redesign
5. clearer test boundaries
6. less doc drift across providers
7. better alignment with thin, replaceable adapter boundaries

---

## 12. Design rules for new external providers

When adding a new provider, the design rules are:

1. keep transport provider-specific
2. keep orchestration provider-neutral
3. return canonical runtime contracts only
4. keep provider-specific details inside canonical `metadata`
5. reject unknown provider states at the adapter/activity boundary
6. do not add workflow-side coercion glue for provider payloads
7. prefer the standard polling/callback contract family unless a true streaming-gateway path is required

---

## 13. Summary

The standard MoonMind model for external agents is:

1. configuration/runtime gate
2. provider transport
3. universal external adapter
4. `MoonMind.AgentRun` orchestration
5. optional tooling/operator surfaces

The key boundary is the adapter/activity contract boundary:

- provider-native payloads are allowed below it
- only canonical `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` are allowed above it

Jules remains the primary reference poll-based provider, while Codex Cloud, OpenClaw, and future providers follow the same core architecture rather than inventing separate execution models.
