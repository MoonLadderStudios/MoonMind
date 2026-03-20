Here is a comprehensive technical design for integrating OpenClaw as an external agent into MoonMind using the **Direct Gateway HTTP API** approach.

Because OpenClaw acts as an autonomous agent performing complex, multi-step, and long-running tasks (such as executing terminal commands, reading codebases, or manipulating files), a standard synchronous HTTP request would quickly trigger network and Temporal activity timeouts.

To solve this, this design leverages OpenClaw's OpenAI-compatible streaming endpoint (`stream=True`) combined with **Temporal Activity Heartbeats** to keep the workflow alive, fault-tolerant, and responsive.

---

### 1. Architectural Overview

MoonMind orchestrates external agents using the universal external adapter pattern (located in `moonmind/workflows/adapters/`). We will create an `OpenClawAgentAdapter` that extends `BaseExternalAgentAdapter`.

OpenClaw requires a long-lived HTTP Server-Sent Events (SSE) stream to the OpenClaw Gateway. As OpenClaw processes the task, it streams data back incrementally. The Temporal activity uses these chunks to issue `activity.heartbeat()` calls, keeping the workflow alive and routing real-time logs to the UI. Since the universal adapter pattern splits execution into `start`, `status`, `fetch_result`, and `cancel`, the long-lived streaming connection will be managed by the `status` activity or a specialized async supervisor, while `start` merely initiates the job.

---

### 2. Configuration & Security Management

OpenClaw requires a Gateway URL and an authentication token. Because OpenClaw executes code, securing the token is paramount.

**Target:** `moonmind/openclaw/settings.py`
Add an `OpenClawSettings` object to manage non-sensitive configuration, following the runtime gate pattern.

```python
from pydantic import BaseModel, Field

class OpenClawSettings(BaseModel):
    gateway_url: str = Field(default="http://127.0.0.1:18789", description="OpenClaw Gateway URL")
    default_model: str = Field(default="openclaw-default")
    # 1 hour timeout for long tasks, bypassing standard short HTTP timeouts
    timeout_seconds: int = Field(default=3600)

def is_openclaw_enabled(env: dict | None = None) -> bool:
    # Check env vars: OPENCLAW_ENABLED, etc.
    ...
```

The Gateway token must *not* be hardcoded. It should be managed via MoonMind's `SecretStore` and retrieved dynamically at runtime (e.g., `await get_secret("OPENCLAW_GATEWAY_TOKEN")`).

---

### 3. OpenClaw Async HTTP Client

**Target:** `moonmind/workflows/adapters/openclaw_client.py`

A dedicated asynchronous HTTP client will manage the connection to the Gateway using `httpx`. We will implement an async generator to consume the SSE stream.

```python
import httpx
import json
from typing import AsyncGenerator

class OpenClawHttpClient:
    def __init__(self, base_url: str, token: str, timeout: int):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        # Disable read timeouts for long-running autonomous loops; rely on total timeout
        self.timeout = httpx.Timeout(timeout, read=None)

    async def start_execution(self, messages: list[dict], model: str) -> str:
        # Implementation to initiate the job and return a run_id
        ...

    async def stream_status(self, run_id: str) -> AsyncGenerator[str, None]:
        # Implementation to stream SSE status updates for a given run_id
        ...

    async def cancel_execution(self, run_id: str) -> None:
        # Implementation to cancel an execution
        ...
```

---

### 4. Agent Adapter Implementation

**Target:** `moonmind/workflows/adapters/openclaw_agent_adapter.py`

The adapter implements the `BaseExternalAgentAdapter` contract, translating MoonMind `AgentExecutionRequest` payloads into OpenAI-compatible messages and returning canonical `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` objects.

```python
from moonmind.workflows.adapters.base_external_agent_adapter import BaseExternalAgentAdapter
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult,
    ProviderCapabilityDescriptor
)

_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="openclaw",
    supportsCallbacks=False,
    supportsCancel=True,
    supportsResultFetch=True,
    defaultPollHintSeconds=15,
)

class OpenClawAgentAdapter(BaseExternalAgentAdapter):
    def __init__(self, *, client_factory):
        super().__init__(accepted_agent_ids=frozenset({"openclaw"}))
        self._client_factory = client_factory

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict
    ) -> AgentRunHandle:
        client = self._client_factory()
        # Translate request into messages
        messages = [
            {"role": "system", "content": "You are an autonomous OpenClaw agent..."},
            {"role": "user", "content": f"Task Instructions:\n{description}"}
        ]

        run_id = await client.start_execution(messages, "openclaw-default")

        return self.build_handle(
            run_id=run_id,
            agent_id="openclaw",
            status="queued",
            provider_status="submitted",
            normalized_status="queued"
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        # For OpenClaw, status might wrap the stream check or simply poll a summary endpoint
        ...
        return self.build_status(
            run_id=run_id,
            agent_id="openclaw",
            status="running",
            provider_status="streaming",
            normalized_status="running"
        )

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        # Fetch terminal result
        ...
        return self.build_result(
            run_id=run_id,
            provider_status="completed",
            normalized_status="completed",
            provider_name="openclaw"
        )

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        client = self._client_factory()
        await client.cancel_execution(run_id)
        return self.build_status(
            run_id=run_id,
            agent_id="openclaw",
            status="canceled",
            provider_status="canceled",
            normalized_status="canceled"
        )
```

---

### 5. Temporal Activity Integration (The Heartbeat Pattern)

**Target:** `moonmind/workflows/temporal/activities/openclaw_activities.py`

Temporal activities map directly to the 4 operations: `start`, `status`, `fetch_result`, and `cancel`.

To leverage the OpenClaw streaming capability (SSE chunks + heartbeats), the long-running stream can be processed either by an enhanced `integration.openclaw.status` activity that loops and heartbeats, or by a specialized activity.

```python
from temporalio import activity
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunHandle, AgentRunStatus, AgentRunResult

@activity.defn(name="integration.openclaw.start")
async def openclaw_start_activity(request: AgentExecutionRequest) -> AgentRunHandle:
    adapter = _build_adapter()
    return await adapter.start(request)

@activity.defn(name="integration.openclaw.status")
async def openclaw_status_activity(run_id: str) -> AgentRunStatus:
    adapter = _build_adapter()
    # If the provider exposes a long-lived stream for status tracking,
    # the activity can iterate over stream_status() and emit heartbeats:
    #
    # async for chunk in client.stream_status(run_id):
    #     activity.heartbeat(chunk)
    #
    return await adapter.status(run_id)

@activity.defn(name="integration.openclaw.fetch_result")
async def openclaw_fetch_result_activity(run_id: str) -> AgentRunResult:
    adapter = _build_adapter()
    return await adapter.fetch_result(run_id)

@activity.defn(name="integration.openclaw.cancel")
async def openclaw_cancel_activity(run_id: str) -> AgentRunStatus:
    adapter = _build_adapter()
    return await adapter.cancel(run_id)
```

---

### 6. Adapter Registry Wiring

**Target:** `moonmind/workflows/adapters/external_adapter_registry.py`

Register the `OpenClawAgentAdapter` alongside existing adapters like `JulesAgentAdapter` and `CodexCloudAgentAdapter` in `build_default_registry()` so the orchestrator routes requests correctly.

```python
from moonmind.workflows.adapters.openclaw_agent_adapter import OpenClawAgentAdapter
from moonmind.workflows.adapters.openclaw_client import OpenClawHttpClient
from moonmind.openclaw.settings import is_openclaw_enabled

def build_default_registry(*, env=None):
    registry = ExternalAdapterRegistry()
    # ... existing Jules and Codex Cloud registrations ...

    if is_openclaw_enabled(env=env):
        def _openclaw_factory():
            # Retrieve token securely here or within the client
            client = OpenClawHttpClient(base_url="...", token="...", timeout=3600)
            return OpenClawAgentAdapter(client_factory=lambda: client)

        registry.register("openclaw", _openclaw_factory)

    return registry
```

### Summary of Architectural Benefits

1. **Standardized External Adapter Pattern:** OpenClaw integrates natively into MoonMind using `BaseExternalAgentAdapter`, ensuring consistent lifecycle management (`start`, `status`, `fetch_result`, `cancel`).
2. **Stateless Workflows via Streaming:** Using Temporal heartbeats on the streaming connection allows Temporal to supervise the long-running operation natively without a complex custom state machine.
3. **Fault Tolerant:** Temporal heartbeats ensure the Orchestrator knows the agent is healthy during long operations. If the worker node crashes, Temporal knows exactly when it failed and can restart the activity.
4. **Implicit Cancellation Compatibility:** If an SSE stream disconnect natively halts OpenClaw execution, cancelling the `status` activity severs the connection cleanly, while the `cancel` activity provides an explicit cancellation signal.
