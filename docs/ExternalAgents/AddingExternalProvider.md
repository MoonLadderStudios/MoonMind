# Adding a New External Agent Provider

**Implementation tracking:** [`docs/tmp/remaining-work/ExternalAgents-AddingExternalProvider.md`](../tmp/remaining-work/ExternalAgents-AddingExternalProvider.md)

This guide walks through integrating a new external AI agent provider with MoonMind's universal external adapter pattern.

## Prerequisites

- Familiarity with `moonmind/workflows/adapters/base_external_agent_adapter.py`
- A functioning provider API with HTTP transport

## Step 1: Settings Module (Runtime Gate)

Create a settings module with a runtime gate function. Use `moonmind/codex_cloud/settings.py` or `moonmind/jules/runtime.py` as reference.

```python
# moonmind/<provider>/settings.py

PROVIDER_DISABLED_MESSAGE = "targetRuntime=<provider> requires ..."

@dataclass(frozen=True)
class ProviderRuntimeGate:
    enabled: bool
    missing: tuple[str, ...]

def build_provider_gate() -> ProviderRuntimeGate:
    # Check env vars: PROVIDER_ENABLED, PROVIDER_API_URL, PROVIDER_API_KEY
    ...
```

## Step 2: Client (HTTP Transport)

Create a thin HTTP client that wraps the provider's REST API. See `moonmind/workflows/adapters/jules_client.py` or `moonmind/workflows/adapters/codex_cloud_client.py` for reference.

The client should handle:
- Authentication (API key, OAuth, etc.)
- Request/response serialization
- Error handling and retries

## Step 3: Adapter Subclass

Extend `BaseExternalAgentAdapter` and implement the 4 provider hooks plus the capability descriptor.

```python
# moonmind/workflows/adapters/<provider>_agent_adapter.py

from moonmind.workflows.adapters.base_external_agent_adapter import (
    BaseExternalAgentAdapter,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest, AgentRunHandle, AgentRunResult,
    AgentRunStatus, ProviderCapabilityDescriptor,
)

_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="<provider>",
    supportsCallbacks=False,
    supportsCancel=True,
    supportsResultFetch=True,
    defaultPollHintSeconds=15,
)

class ProviderAgentAdapter(BaseExternalAgentAdapter):
    def __init__(self, *, client_factory):
        super().__init__(accepted_agent_ids=frozenset({"<provider>"}))
        self._client_factory = client_factory

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _CAPABILITY

    async def do_start(self, request, title, description, metadata):
        client = self._client_factory()
        # Call provider API, return self.build_handle(...)
        ...

    async def do_status(self, run_id):
        client = self._client_factory()
        # Poll provider API, return self.build_status(...)
        ...

    async def do_fetch_result(self, run_id):
        client = self._client_factory()
        # Fetch result, return self.build_result(...)
        ...

    async def do_cancel(self, run_id):
        client = self._client_factory()
        # Cancel via provider API, return self.build_status(...)
        ...
```

**What you get for free from `BaseExternalAgentAdapter`:**
- `agent_kind == "external"` validation
- `agent_id` membership validation
- Correlation metadata injection (`moonmind.correlationId`, `moonmind.idempotencyKey`)
- In-memory idempotency caching (per activity attempt)
- Automatic `poll_hint_seconds` population from `defaultPollHintSeconds`
- Best-effort cancel fallback when `supportsCancel=False`
- Builder methods: `build_handle()`, `build_status()`, `build_result()`

## Step 4: Registry Registration

Add the provider to `build_default_registry()` in `moonmind/workflows/adapters/external_adapter_registry.py`:

```python
def build_default_registry() -> ExternalAdapterRegistry:
    registry = ExternalAdapterRegistry()
    # ... existing registrations ...

    if is_provider_enabled():
        registry.register(
            "<provider>",
            lambda: ProviderAgentAdapter(client_factory=_build_client),
        )

    return registry
```

## Step 5: Temporal Activities

Create 4 Temporal activities following the pattern in `moonmind/workflows/temporal/activities/jules_activities.py`:

```python
# moonmind/workflows/temporal/activities/<provider>_activities.py

@activity.defn(name="integration.<provider>.start")
async def provider_start_activity(request: AgentExecutionRequest) -> AgentRunHandle:
    adapter = _build_adapter()
    return await adapter.start(request)

@activity.defn(name="integration.<provider>.status")
async def provider_status_activity(run_id: str) -> AgentRunStatus:
    adapter = _build_adapter()
    return await adapter.status(run_id)

@activity.defn(name="integration.<provider>.fetch_result")
async def provider_fetch_result_activity(run_id: str) -> AgentRunResult:
    adapter = _build_adapter()
    return await adapter.fetch_result(run_id)

@activity.defn(name="integration.<provider>.cancel")
async def provider_cancel_activity(run_id: str) -> AgentRunStatus:
    adapter = _build_adapter()
    return await adapter.cancel(run_id)
```

## Step 6: Activity Catalog Registration

Register the 4 activities in `moonmind/workflows/temporal/activity_catalog.py`:

1. Add 4 `TemporalActivityDefinition` entries with `integration.<provider>.*` activity types
2. Add `"integration:<provider>"` to the `INTEGRATIONS_FLEET` capabilities tuple

## Step 7: No Workflow Changes Required

The `MoonMind.AgentRun` workflow uses dynamic activity names (`integration.{agent_id}.start/status/fetch_result/cancel`), so no changes to the workflow are needed. The new provider will be automatically available once registered and the activities are deployed.

## Testing

1. **Adapter unit tests**: Test `do_start`, `do_status`, `do_fetch_result`, `do_cancel` with mocked clients. See `tests/unit/workflows/adapters/test_base_external_agent_adapter.py` for the base class test patterns.
2. **Activity unit tests**: Test each activity with a mocked adapter. See `tests/unit/workflows/adapters/test_codex_cloud_activities.py`.
3. **Run tests**: `./tools/test_unit.sh tests/unit/workflows/adapters/`
