# Adding a New External Agent Provider

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

Status: **Guide for current runtime architecture**
Last updated: 2026-03-30
Related:
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/ActivityCatalogAndWorkerTopology.md`](../Temporal/ActivityCatalogAndWorkerTopology.md)
- [`docs/Temporal/ErrorTaxonomy.md`](../Temporal/ErrorTaxonomy.md)

---

## 1. Purpose

This guide explains how to integrate a new **external true-agent provider** into MoonMind using the current external adapter pattern.

This document covers:

- runtime gating
- client construction
- external adapter implementation
- canonical contract requirements
- registry registration
- Temporal activity registration
- activity catalog updates
- test expectations

This document is for providers that MoonMind does **not** run locally, such as:

- Jules
- Codex Cloud
- future BYOA-style delegated agents

This document does **not** cover:

- managed runtimes like Gemini CLI, Claude Code, or Codex CLI
- agent skill source-precedence resolution
- provider-profile management for managed runtimes
- generic tool/skill execution through `mm.skill.execute`

---

## 2. Core rule: adapters must return canonical contracts

New external providers must return MoonMind’s canonical runtime contracts directly.

That means:

- `start(...) -> AgentRunHandle`
- `status(...) -> AgentRunStatus`
- `fetch_result(...) -> AgentRunResult`
- `cancel(...) -> AgentRunStatus`

Do **not** return provider-shaped top-level payloads such as:

- `{external_id, tracking_ref}`
- `{status: "provider_specific_state"}`
- `{url, callback_supported, provider_status}` without wrapping them into canonical models

Provider-specific details belong in canonical `metadata` fields.

Examples of acceptable metadata:

- provider URLs
- callback support flags
- normalized provider status labels
- provider tracking refs
- PR URLs
- provider-specific diagnostics pointers

This rule exists so `MoonMind.AgentRun` does not need workflow-side coercion glue.

---

## 3. Current external execution styles

MoonMind currently supports two external execution styles.

## 3.1 Standard polling or callback provider

This is the normal provider path.

Activities:

- `integration.<provider>.start`
- `integration.<provider>.status`
- `integration.<provider>.fetch_result`
- `integration.<provider>.cancel`

Use this for most new providers.

## 3.2 Streaming gateway provider

This is a special-case path for providers that execute through one long-running activity and directly produce a terminal result.

Current example:

- `integration.openclaw.execute`

At the moment, MoonMind’s workflow path is still specialized for this style rather than fully generalized for arbitrary `integration.<provider>.execute` activity names.

That means:

- new providers should strongly prefer the standard polling/callback pattern
- adding a second streaming-gateway-style provider may require runtime generalization work in `MoonMind.AgentRun` before the provider is truly plug-and-play

---

## 4. Prerequisites

You should be familiar with:

- `moonmind/workflows/adapters/base_external_agent_adapter.py`
- `moonmind/workflows/adapters/external_adapter_registry.py`
- `moonmind/schemas/agent_runtime_models.py`
- the provider’s API surface
- the provider’s authentication model
- the provider’s lifecycle semantics for start, status, result fetch, and cancel

You need a provider API that can support at least one of these models:

- start + status + fetch_result (+ optional cancel)
- one-shot streaming gateway execution

---

## 5. Step 1: add a runtime gate

Create a small settings/runtime-gate module for the provider.

Use existing provider runtime settings files as reference.

Example:

```python id="19408"
# moonmind/<provider>/settings.py

from dataclasses import dataclass

PROVIDER_DISABLED_MESSAGE = "targetRuntime=<provider> requires ..."

@dataclass(frozen=True)
class ProviderRuntimeGate:
 enabled: bool
 missing: tuple[str, ...]

def build_provider_gate() -> ProviderRuntimeGate:
 # Check env vars or other required provider settings
 ...
````

The runtime gate should answer:

* is this provider configured and enabled
* what required settings are missing
* should the provider be omitted from registry registration

Keep this logic small and deterministic from the activity perspective. Dynamic env inspection belongs in activity code or adapter-construction code, not in workflow logic.

---

## 6. Step 2: build a thin client

Create a thin HTTP or RPC client for the provider.

Suggested file:

```python id="17053"
moonmind/workflows/adapters/<provider>_client.py
```

The client should handle:

* authentication
* request serialization
* response parsing
* retries where appropriate
* provider-specific error translation
* timeouts

The client should **not** know about Temporal workflows or MoonMind workflow state. It is just a transport wrapper.

Good client responsibilities:

* `create_run(...)`
* `get_run(...)`
* `get_result(...)`
* `cancel_run(...)`

Avoid burying MoonMind-specific contract normalization deep in the client. Keep provider transport concerns in the client and MoonMind contract shaping in the adapter.

---

## 7. Step 3: implement the adapter subclass

Extend `BaseExternalAgentAdapter`.

Suggested file:

```python id="97094"
moonmind/workflows/adapters/<provider>_agent_adapter.py
```

Example skeleton:

```python id="74484"
from moonmind.workflows.adapters.base_external_agent_adapter import (
 BaseExternalAgentAdapter,
)
from moonmind.schemas.agent_runtime_models import (
 AgentExecutionRequest,
 AgentRunHandle,
 AgentRunResult,
 AgentRunStatus,
 ProviderCapabilityDescriptor,
)

_CAPABILITY = ProviderCapabilityDescriptor(
 providerName="<provider>",
 supportsCallbacks=False,
 supportsCancel=True,
 supportsResultFetch=True,
 defaultPollHintSeconds=15,
 executionStyle="polling",
)

class ProviderAgentAdapter(BaseExternalAgentAdapter):
 def __init__(self, *, client_factory):
 super().__init__(accepted_agent_ids=frozenset({"<provider>"}))
 self._client_factory = client_factory

 @property
 def provider_capability(self) -> ProviderCapabilityDescriptor:
 return _CAPABILITY

 async def do_start(self, request, title, description, metadata) -> AgentRunHandle:
 client = self._client_factory()
 ...
 return self.build_handle(...)

 async def do_status(self, run_id: str) -> AgentRunStatus:
 client = self._client_factory()
 ...
 return self.build_status(...)

 async def do_fetch_result(self, run_id: str) -> AgentRunResult:
 client = self._client_factory()
 ...
 return self.build_result(...)

 async def do_cancel(self, run_id: str) -> AgentRunStatus:
 client = self._client_factory()
 ...
 return self.build_status(...)
```

## 7.1 What `BaseExternalAgentAdapter` gives you

The base class already handles common external-agent concerns such as:

* `agent_kind == "external"` validation
* `agent_id` membership validation
* correlation metadata injection
* idempotency-oriented scaffolding
* default `poll_hint_seconds` support
* best-effort cancel fallback when `supportsCancel=False`
* canonical builders such as:

 * `build_handle(...)`
 * `build_status(...)`
 * `build_result(...)`

Use those builders whenever possible.

## 7.2 Adapter responsibilities

Your adapter must:

* translate `AgentExecutionRequest` into provider-specific request payloads
* normalize provider states into canonical `AgentRunStatus.status` values
* keep provider-specific details inside canonical `metadata`
* return canonical models only
* preserve idempotency and retry safety
* avoid leaking provider raw payloads into workflow-facing top-level contracts

## 7.3 Status normalization rules

Map provider lifecycle states into MoonMind’s canonical states, such as:

* `queued`
* `launching`
* `running`
* `awaiting_callback`
* `awaiting_feedback`
* `awaiting_approval`
* `intervention_requested`
* `collecting_results`
* `completed`
* `failed`
* `canceled`
* `timed_out`

If the provider emits a state that MoonMind does not support, treat that as a contract error at the adapter/activity boundary. Do not pass unknown raw provider states through to workflow code.

## 7.4 Metadata rules

Use `metadata` for provider-specific information.

Good examples:

```python id="82828"
metadata = {
 "providerStatus": raw_status,
 "normalizedStatus": normalized_status,
 "externalUrl": provider_url,
 "trackingRef": provider_tracking_ref,
 "callbackSupported": True,
}
```

Do **not** invent alternate top-level fields like:

* `external_id`
* `provider_status`
* `tracking_ref`
* `callback_supported`

Those belong in canonical models or metadata, not ad hoc dicts.

---

## 8. Step 4: register the adapter

Add the provider to `build_default_registry()` in:

```python id="77289"
moonmind/workflows/adapters/external_adapter_registry.py
```

Example:

```python id="21152"
def build_default_registry() -> ExternalAdapterRegistry:
 registry = ExternalAdapterRegistry()
 # ... existing providers ...

 if is_provider_enabled():
 registry.register(
 "<provider>",
 lambda: ProviderAgentAdapter(client_factory=_build_client),
 )

 return registry
```

Registration rules:

* register only when the runtime gate says the provider is enabled
* keep the registry responsible for adapter construction
* do not instantiate provider clients directly inside workflow code

---

## 9. Step 5: add Temporal activities

For the normal external provider path, create four activities.

Suggested file:

```python id="46648"
moonmind/workflows/temporal/activities/<provider>_activities.py
```

Example:

```python id="26361"
from temporalio import activity
from moonmind.schemas.agent_runtime_models import (
 AgentExecutionRequest,
 AgentRunHandle,
 AgentRunResult,
 AgentRunStatus,
)

@activity.defn(name="integration.<provider>.start")
async def provider_start_activity(request: AgentExecutionRequest) -> AgentRunHandle:
 adapter = _build_adapter()
 return await adapter.start(request)

@activity.defn(name="integration.<provider>.status")
async def provider_status_activity(payload: dict) -> AgentRunStatus:
 adapter = _build_adapter()
 run_id = payload["external_id"] if "external_id" in payload else payload["run_id"]
 return await adapter.status(run_id)

@activity.defn(name="integration.<provider>.fetch_result")
async def provider_fetch_result_activity(payload: dict) -> AgentRunResult:
 adapter = _build_adapter()
 run_id = payload["external_id"] if "external_id" in payload else payload["run_id"]
 return await adapter.fetch_result(run_id)

@activity.defn(name="integration.<provider>.cancel")
async def provider_cancel_activity(payload: dict) -> AgentRunStatus:
 adapter = _build_adapter()
 run_id = payload["external_id"] if "external_id" in payload else payload["run_id"]
 return await adapter.cancel(run_id)
```

## 9.1 Activity contract rule

Even if the activity accepts a provider-oriented lookup payload, the **return type** must still be canonical.

That means:

* no raw provider dicts returned from activities
* no top-level provider fields outside the canonical schema
* no reliance on workflow-side repair logic

## 9.2 Optional callback or helper activities

If the provider supports additional operations, you may add narrowly scoped provider activities such as:

* `integration.<provider>.send_message`
* `integration.<provider>.list_activities`
* `integration.<provider>.answer_question`

These are provider-specific extensions, not part of the core required lifecycle set.

Only add them when the provider’s product semantics actually need them.

---

## 10. Step 6: register activity catalog entries

Update:

```python id="16990"
moonmind/workflows/temporal/activity_catalog.py
```

For a normal provider integration, add:

* `integration.<provider>.start`
* `integration.<provider>.status`
* `integration.<provider>.fetch_result`
* `integration.<provider>.cancel`

Each should be a `TemporalActivityDefinition` in the `INTEGRATIONS_FLEET` with capability class:

```text
integration:<provider>
```

Also add `"integration:<provider>"` to the integrations fleet capability list.

Use the existing provider entries as the template for:

* task queue
* timeout shape
* retry policy
* capability class naming

---

## 11. Step 7: register worker bindings

Ensure the provider’s activity handlers are actually registered into the runtime activity bindings for the integrations fleet.

The exact registration point may vary based on how the runtime currently assembles integration activities, but the important rule is:

* catalog entry alone is not enough
* the worker must also expose the concrete activity handlers

Verify that the activity bindings for the integrations fleet now include your provider’s activity types.

---

## 12. Step 8: understand when workflow changes are not required

## 12.1 Standard provider path

For a normal polling or callback provider using:

* `integration.<provider>.start`
* `integration.<provider>.status`
* `integration.<provider>.fetch_result`
* `integration.<provider>.cancel`

`MoonMind.AgentRun` should not require provider-specific workflow changes once:

* the adapter is registered
* the activity types are in the catalog
* the activity handlers are deployed

This is the preferred provider integration path.

## 12.2 Streaming gateway exception

If the provider uses a one-shot streaming gateway execution style, workflow changes may still be required unless the runtime has already been generalized for `integration.<provider>.execute`.

Today, that path is not yet fully generalized for arbitrary new providers.

So the accurate guidance is:

* **standard polling/callback providers:** no workflow changes expected
* **new streaming-gateway providers:** expect runtime generalization work unless the workflow has first been extended to support generic `integration.<provider>.execute`

---

## 13. Step 9: error handling and classification

Provider adapters and activities should classify errors at the boundary where they are understood.

Examples:

* malformed provider payloads → non-retryable contract/input errors
* unsupported provider statuses → non-retryable status-mapping errors
* transient network failures → retryable
* provider rate limits → retryable or cooldown-oriented depending on provider semantics

Do not let workflow code become the place that discovers malformed provider return shapes.

---

## 14. Step 10: testing

## 14.1 Adapter unit tests

Test:

* `do_start`
* `do_status`
* `do_fetch_result`
* `do_cancel`

Use mocked clients and assert that the adapter returns canonical `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`.

You should also test:

* provider status normalization
* unknown status rejection
* metadata population
* idempotency-sensitive behavior where relevant

## 14.2 Activity unit tests

Test each activity with a mocked adapter.

Assert that:

* the activity returns canonical models
* no provider-shaped top-level dict escapes the activity boundary
* error paths are classified correctly

## 14.3 Registry tests

Test that:

* the provider registers only when enabled
* the registry can construct the adapter
* disabled providers are omitted cleanly

## 14.4 Integration tests

Test the end-to-end provider lifecycle:

* start
* status
* fetch_result
* cancel

Verify that `MoonMind.AgentRun` can use the provider through the dynamic integration activity path without provider-specific workflow logic for the standard polling/callback model.

## 14.5 Recommended test commands

Run the adapter and provider activity test suites, for example:

```bash id="83820"
./tools/test_unit.sh tests/unit/workflows/adapters/
```

Also add or update provider-specific tests alongside the existing external adapter tests.

---

## 15. Checklist for a new provider

A provider is considered correctly integrated when all of the following are true:

* runtime gate exists
* thin client exists
* adapter subclass exists
* adapter returns canonical runtime contracts only
* adapter is registered in the external adapter registry
* four core integration activities are defined for the standard provider path
* activity catalog entries exist
* worker runtime exposes the activity handlers
* unit tests cover adapter and activity behavior
* unknown provider states are rejected at the adapter/activity boundary
* provider-specific details live in `metadata`, not ad hoc top-level response fields

---

## 16. Summary

To add a new external provider correctly in MoonMind:

1. add a runtime gate
2. add a thin provider client
3. implement a `BaseExternalAgentAdapter` subclass
4. return canonical `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` contracts
5. register the adapter
6. add `integration.<provider>.*` activities
7. register the catalog entries and worker bindings
8. test canonical return-shape behavior thoroughly

The most important rule is simple:

* **normalize at the adapter/activity boundary**
* **do not rely on workflow-side coercion**
* **keep provider-specific details in canonical metadata**
