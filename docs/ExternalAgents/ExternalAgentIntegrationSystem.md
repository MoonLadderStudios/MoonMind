# Technical Design: External Agent Integration System

## 1. Objective

Define one canonical model for MoonMind external-agent integrations.

The goal is to stop describing each provider as its own mini-architecture and instead treat providers such as Jules, Codex Cloud, and future BYOA integrations as implementations of one shared external-agent system:

- one generic orchestration lifecycle
- one canonical `AgentAdapter` contract
- one universal external-adapter base pattern
- provider-specific transport and status mapping behind that boundary

For the full execution model covering both managed and external agents, see [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md).

## 2. Canonical Mental Model

The old docs drifted because they described different slices of the stack:

- this document compressed external execution into a "3-layer" model
- [`JulesClientAdapter.md`](./JulesClientAdapter.md) described Jules transport and MCP tooling as a "4-layer" model

Those are not actually competing architectures. They are different views of the same system. The standard model should be:

## 3. The Universal External Agent Stack

MoonMind should describe every external-agent integration using the same five concerns.

### Layer 1: Configuration and Runtime Gate

Provider enablement, endpoints, auth references, timeouts, retry controls, and feature flags.

Examples:

- `JulesSettings`
- Codex Cloud settings
- runtime gate helpers such as `is_jules_runtime_enabled()`

This layer answers: "Is this provider enabled, configured, and safe to use?"

### Layer 2: Provider Transport

Provider-specific schemas and client code that speak the provider's native protocol.

Examples:

- `moonmind/schemas/jules_models.py`
- `moonmind/workflows/adapters/jules_client.py`
- `moonmind/workflows/adapters/codex_cloud_client.py`

This layer owns:

- REST/RPC request and response shapes
- provider auth headers
- transport retries and timeout handling
- scrubbed provider error handling

This layer does **not** own MoonMind workflow lifecycle semantics.

### Layer 3: Universal External Agent Adapter

This is the key standardization point.

Every external provider should plug into one generic external-agent adapter pattern that implements the shared `AgentAdapter` contract:

- `start(request: AgentExecutionRequest) -> AgentRunHandle`
- `status(run_id) -> AgentRunStatus`
- `fetch_result(run_id) -> AgentRunResult`
- `cancel(run_id) -> AgentRunStatus`

In the current codebase, this boundary is represented by:

- `moonmind/workflows/adapters/agent_adapter.py`
- `moonmind/workflows/adapters/external_adapter_registry.py`
- provider adapters such as `jules_agent_adapter.py` and `codex_cloud_agent_adapter.py`

This layer should become the **universal external adapter base**, not just a loose convention.

It should own the generic external-agent rules that repeat across providers:

- validation that `agent_kind == "external"`
- stable idempotency handling
- MoonMind correlation metadata injection
- artifact reference preparation rules
- callback vs polling capability declaration
- normalized MoonMind run-handle/status/result construction
- truthful cancel semantics when providers lack hard cancellation

Providers should override only the provider-specific parts:

- request translation
- status normalization tables
- provider result extraction
- provider-specific cancel behavior

### Layer 4: Workflow Orchestration

`MoonMind.AgentRun` is the generic execution lifecycle owner for all true agent runs.

This layer owns:

- start via adapter
- durable waiting
- callback handling
- polling fallback
- timeouts
- cancellation cleanup
- artifact publishing
- normalized result return to `MoonMind.Run`

This layer is provider-neutral. It should not contain Jules-specific or Codex-Cloud-specific logic beyond adapter selection.

### Layer 5: Optional Tooling and Operator Surfaces

These are useful integration surfaces, but they are **not** the core execution architecture.

Examples:

- MCP tooling
- REST helper endpoints
- dashboard widgets
- provider-specific admin or debugging surfaces

For Jules, `JulesToolRegistry` belongs here.

This is the main reason the old "4-layer Jules architecture" felt inconsistent: MCP tooling is a valid integration surface, but it should be described as an optional consumer of the provider client/adapter, not as a peer of the core execution lifecycle.

## 4. Target Shape in Code

The codebase already has most of the right seams, but they are not documented as one cohesive pattern yet.

### Already Present

- shared `AgentAdapter` protocol
- `MoonMind.AgentRun` child workflow
- provider-specific adapters for Jules and Codex Cloud
- `ExternalAdapterRegistry`
- provider-specific HTTP clients

### Missing Standardization

What is still missing is a clearly named and reusable **base implementation strategy** for external adapters.

Today, `JulesAgentAdapter` and `CodexCloudAgentAdapter` follow the same pattern by convention. The plan should make that pattern explicit so future providers do not copy-paste integration logic ad hoc.

## 5. Proposed Universal External Adapter Plan

MoonMind should implement a first-class universal external adapter base in the `moonmind/workflows/adapters/` package.

Suggested shape:

- `BaseExternalAgentAdapter` or `ExternalAgentAdapterBase`
- provider descriptor/config hooks
- shared helper methods for correlation metadata, idempotency cache behavior, and normalized contract creation
- overridable provider hooks for `do_start`, `do_status`, `do_fetch_result`, and `do_cancel`

Suggested responsibilities for the base class:

1. Validate the request is an external-agent request for the expected provider.
2. Inject MoonMind correlation metadata consistently.
3. Apply stable idempotency behavior.
4. Normalize common metadata fields:
   - `providerStatus`
   - `normalizedStatus`
   - `externalUrl`
   - callback capability hints
5. Centralize best-effort cancel semantics for providers without native cancellation.
6. Keep workflow-facing contracts compact and artifact-ref-based.

Suggested responsibilities for provider subclasses:

1. Translate `AgentExecutionRequest` into provider-native transport payloads.
2. Define provider status normalization.
3. Extract provider result summaries and artifact-worthy snapshots.
4. Advertise provider capabilities:
   - callback support
   - cancel support
   - result-download richness

## 6. Jules in the Standard Model

Under this model, Jules is no longer "a separate 4-layer integration."

Jules is:

- one provider configuration profile
- one provider transport client and schema set
- one provider-specific subclass of the universal external adapter
- optional tooling surfaces such as MCP

That means the right description is:

> Jules is the reference provider implementation for the universal external-agent adapter pattern.

Not:

> Jules defines its own separate architecture.

## 7. Practical Implementation Sequence

This can be implemented incrementally without rewriting the execution model.

### Phase A: Documentation Alignment

Update external-agent docs so they all use the same vocabulary:

- "Universal External Agent Stack"
- "provider transport"
- "universal external agent adapter"
- "provider-specific adapter implementation"
- "optional tooling surface"

### Phase B: Extract Shared External Adapter Base ✅ COMPLETE

Shared logic is consolidated in `BaseExternalAgentAdapter` (`moonmind/workflows/adapters/base_external_agent_adapter.py`):

- In-memory idempotency cache handling
- Correlation metadata injection (`moonmind.correlationId`, `moonmind.idempotencyKey`)
- `AgentRunHandle` / `AgentRunStatus` / `AgentRunResult` builder methods
- Automatic `poll_hint_seconds` population from `ProviderCapabilityDescriptor`
- Best-effort cancel fallback when `supportsCancel=False`

### Phase C: Standardize Provider Capability Descriptors ✅ COMPLETE

`ProviderCapabilityDescriptor` is defined in `moonmind/schemas/agent_runtime_models.py` with:

- `supports_callbacks`
- `supports_cancel`
- `supports_result_fetch`
- `provider_name`
- `default_poll_hint_seconds`

Both Jules and Codex Cloud adapters declare their capabilities. The base class auto-populates `poll_hint_seconds` from the descriptor and provides a cancel fallback for providers that don't support cancellation.

### Phase D: Keep Tooling Surfaces Thin

MCP, REST, and dashboard integration should consume the same provider transport and adapter boundaries rather than re-defining provider semantics.

This keeps:

- status normalization in one place
- correlation rules in one place
- runtime gating in one place

### Phase E: Add the Next External Provider Using the Same Base ✅ PROVEN

Codex Cloud was integrated as the second provider using the same base class pattern:

1. settings — `moonmind/codex_cloud/settings.py`
2. schemas/client — `moonmind/workflows/adapters/codex_cloud_client.py`
3. provider adapter subclass — `CodexCloudAgentAdapter` extends `BaseExternalAgentAdapter`
4. registry registration — in `build_default_registry()`
5. Temporal activities — `moonmind/workflows/temporal/activities/codex_cloud_activities.py`

No changes to `MoonMind.Run` or `MoonMind.AgentRun` were required.

For a step-by-step guide to adding the next provider, see [`AddingExternalProvider.md`](AddingExternalProvider.md).

## 8. Architectural Benefits

1. One mental model for all external agents.
2. Jules becomes the reference implementation, not a special case.
3. Provider-specific transport and tooling stay isolated from orchestration.
4. Future integrations become adapter work, not workflow redesign.
5. The docs align with the constitution principle that MoonMind should orchestrate agents through thin, replaceable adapter boundaries.

## 9. Summary

The standard MoonMind model for external agents should be:

1. configuration/runtime gate
2. provider transport
3. universal external agent adapter
4. `MoonMind.AgentRun` orchestration
5. optional tooling surfaces

Jules should be documented and implemented as the first provider-specific implementation of that shared external-adapter system.
