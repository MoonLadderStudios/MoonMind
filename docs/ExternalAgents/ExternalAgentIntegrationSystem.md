# External Agent Integration System

Status: Current
Related:
- [`../Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`../Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
- [`./AddingExternalProvider.md`](./AddingExternalProvider.md)
- [`./OpenClawAgentAdapter.md`](./OpenClawAgentAdapter.md)
- [`../Temporal/ErrorTaxonomy.md`](../Temporal/ErrorTaxonomy.md)

---

## 1. Objective

Every external agent provider is an implementation of one shared system, not its own architecture:

- one generic orchestration lifecycle in `MoonMind.AgentRun`
- one canonical `AgentAdapter` contract
- one shared adapter base, `BaseExternalAgentAdapter`
- provider-specific transport and status mapping behind that boundary
- only canonical runtime contracts crossing the workflow boundary

The registered external adapters are Omnigent, MoonMind's agent backend, and OpenClaw. `build_default_registry()` in `moonmind/workflows/adapters/external_adapter_registry.py` registers each only when its runtime gate is satisfied. Omnigent's harness lifecycle is owned by the [Omnigent docs](../Omnigent/README.md); this document covers the adapter boundary they share.

---

## 2. The external-agent stack

| Layer | Owns | Does not own |
| --- | --- | --- |
| Configuration and runtime gate | Enablement, endpoints, auth references, transport and timeout defaults, gate helpers such as `is_openclaw_enabled()` | Workflow lifecycle |
| Provider transport | Native request/response shapes, auth headers, transport retries, scrubbed transport errors | MoonMind lifecycle or canonical contracts |
| External agent adapter | Request validation, correlation metadata, idempotency, status normalization, canonical result shaping, truthful cancel | Durable waiting or workflow state |
| `MoonMind.AgentRun` orchestration | Start, durable wait, callbacks, polling, timeouts, cancellation cleanup, artifact publication, result handoff | Provider payload parsing |
| Optional tooling | MCP tools, REST helpers, dashboard and debugging surfaces | Provider lifecycle semantics |

Tooling consumes the same transport and adapter boundaries rather than redefining provider behavior.

---

## 3. Adapter contract

Every external provider implements:

- `start(request: AgentExecutionRequest) -> AgentRunHandle`
- `status(run_id) -> AgentRunStatus`
- `fetch_result(run_id) -> AgentRunResult`
- `cancel(run_id) -> AgentRunStatus`

The boundary lives in `moonmind/workflows/adapters/agent_adapter.py`, `external_adapter_registry.py`, and `base_external_agent_adapter.py`.

`BaseExternalAgentAdapter` supplies the rules that repeat across providers: validating that a request targets the expected provider, injecting correlation metadata, stable idempotency, common metadata fields (`providerStatus`, `normalizedStatus`, `externalUrl`, callback hints), normalized handle/status/result construction, and best-effort cancel when the provider lacks hard cancellation.

Provider subclasses own only provider-specific work: translating `AgentExecutionRequest` into native payloads, mapping provider states to canonical states, extracting result summaries and artifact-worthy outputs, provider cancel behavior, and declaring capabilities.

### 3.1 Normalize at the boundary

Provider-native payloads become MoonMind runtime contracts at the adapter or integration activity boundary, never in workflow code. Workflows receive only `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`.

Provider details such as URLs, raw status labels, PR URLs, callback support, and tracking references belong in canonical `metadata`. Provider-shaped top-level payloads (`{external_id, tracking_ref}`, provider-specific status strings, result dicts that the workflow must repair) are rejected at the boundary; unknown provider states fail there rather than in workflow coercion glue.

### 3.2 `ProviderCapabilityDescriptor`

Each adapter declares `provider_name`, `supports_callbacks`, `supports_cancel`, `supports_result_fetch`, `default_poll_hint_seconds`, and `execution_style`. The descriptor lets `MoonMind.AgentRun` stay provider-neutral while choosing the right execution path. The helper activity `integration.resolve_adapter_metadata` performs registry inspection outside workflow code and returns this metadata; it is a workflow-support helper, not a provider lifecycle surface.

---

## 4. Execution styles

### 4.1 Streaming gateway

Omnigent and OpenClaw declare `execution_style="streaming_gateway"`. `MoonMind.AgentRun` runs one execute-style activity that returns a canonical `AgentRunResult`:

- `integration.openclaw.execute`
- `integration.omnigent.execute` and `integration.omnigent.profile_bound_execute`

### 4.2 Polling

`MoonMind.AgentRun` also keeps the generic poll lifecycle for providers that run work asynchronously behind an external id:

1. `integration.<provider>.start(...) -> AgentRunHandle`
2. durable wait on callbacks with a polling timer as fallback
3. `integration.<provider>.status(...) -> AgentRunStatus`
4. `integration.<provider>.fetch_result(...) -> AgentRunResult`
5. `integration.<provider>.cancel(...) -> AgentRunStatus` when needed

No registered provider currently uses this style. A provider that does extends `BaseExternalAgentAdapter` and registers the activity family on the integrations fleet.

### 4.3 Callback ingress

Poll-style providers that support callbacks post to `POST /api/integrations/{integrationName}/callbacks/{callbackCorrelationKey}`; `GET /api/integrations/callbacks` describes the contract. The API checks the optional shared token, payload size, and rate limit (`MOONMIND_INTEGRATION_CALLBACK_*` settings), resolves the target through a durable correlation record rather than a Visibility scan (so the target survives Continue-As-New), optionally captures the raw payload as an artifact, and signals the execution with `ExternalEvent`. Callbacks for an execution already in a terminal state are accepted without a signal. Polling remains armed as the fallback when callbacks are absent or late.

---

## 5. Design rules for new providers

1. Keep transport provider-specific and orchestration provider-neutral.
2. Return canonical runtime contracts only, with provider details in `metadata`.
3. Reject unknown provider states at the adapter or activity boundary.
4. Do not add workflow-side coercion for provider payloads.
5. Use the polling family for asynchronous external jobs and a streaming-gateway `execute` activity only when the provider completes through one long-running call.

[`AddingExternalProvider.md`](./AddingExternalProvider.md) covers the step-by-step addition: runtime gate, client, adapter, activity catalog and worker registration, and contract requirements.
