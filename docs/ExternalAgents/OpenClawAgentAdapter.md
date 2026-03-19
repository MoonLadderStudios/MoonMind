Here is a comprehensive technical design for integrating OpenClaw as an external agent into MoonMind using the **Direct Gateway HTTP API** approach.

Because OpenClaw acts as an autonomous agent performing complex, multi-step, and long-running tasks (such as executing terminal commands, reading codebases, or manipulating files), a standard synchronous HTTP request would quickly trigger network and Temporal activity timeouts.

To solve this, this design leverages OpenClaw's OpenAI-compatible streaming endpoint (`stream=True`) combined with **Temporal Activity Heartbeats** to keep the workflow alive, fault-tolerant, and responsive.

---

### 1. Architectural Overview
MoonMind orchestrates external agents using the adapter pattern (located in `moonmind/workflows/adapters/`). We will create an `OpenClawAgentAdapter` that implements the base adapter interface.

Instead of an asynchronous polling model (Submit -> Poll -> Retrieve), the Temporal Worker will open a long-lived HTTP Server-Sent Events (SSE) stream to the OpenClaw Gateway. As OpenClaw processes the task, it streams data back incrementally. The Temporal activity uses these chunks to issue `activity.heartbeat()` calls, keeping the workflow alive and routing real-time logs to the UI.

---

### 2. Configuration & Security Management
OpenClaw requires a Gateway URL and an authentication token. Because OpenClaw executes code, securing the token is paramount.

**Target:** `moonmind/config/settings.py`
Add an `OpenClawSettings` object to manage non-sensitive configuration.
```python
from pydantic import BaseModel, Field

class OpenClawSettings(BaseModel):
    gateway_url: str = Field(default="http://127.0.0.1:18789", description="OpenClaw Gateway URL")
    default_model: str = Field(default="openclaw-default")
    # 1 hour timeout for long tasks, bypassing standard short HTTP timeouts
    timeout_seconds: int = Field(default=3600)
```

**Target:** `moonmind/auth/providers.py`
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

    async def stream_execution(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json=payload
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_str)
                            # Extract standard OpenAI-compatible delta content
                            if delta := data_json.get("choices", [{}])[0].get("delta", {}).get("content"):
                                yield delta
                        except json.JSONDecodeError:
                            continue
```

---

### 4. Agent Adapter Implementation
**Target:** `moonmind/workflows/adapters/openclaw_agent_adapter.py`

The adapter implements the `BaseExternalAgentAdapter` contract, translating MoonMind payloads into OpenAI-compatible messages and parsing the final aggregated response.

```python
from moonmind.workflows.adapters.base_external_agent_adapter import BaseExternalAgentAdapter
from moonmind.schemas.agent_runtime_models import ExecutionResult, ExecutionStatus

class OpenClawAgentAdapter(BaseExternalAgentAdapter):
    def __init__(self, client: OpenClawHttpClient, model: str):
        self.client = client
        self.model = model

    def translate_request(self, task_payload: dict, context_files: str) -> list[dict]:
        """Maps MoonMind Task and Context to OpenClaw messages."""
        system_prompt = "You are an autonomous OpenClaw agent executing a task for the MoonMind orchestrator."
        user_prompt = f"Workspace Context:\n{context_files}\n\nTask Instructions:\n{task_payload.get('instructions')}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

    def parse_response(self, final_text: str) -> ExecutionResult:
        """Parses the raw OpenClaw output into a MoonMind ExecutionResult format."""
        return ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            output=final_text,
            artifacts=[] # Add logic to extract file diffs/artifacts if OpenClaw outputs them in specific tags
        )
```

---

### 5. Temporal Activity Integration (The Heartbeat Pattern)
**Target:** `moonmind/workflows/temporal/activities/openclaw_activities.py`

Temporal activities must handle long execution times without timing out. By coupling the HTTP stream chunks with `activity.heartbeat()`, we keep the Temporal workflow alive and provide real-time logs.

```python
from temporalio import activity
import asyncio

@activity.defn
async def execute_openclaw_activity(task_payload: dict, context_files: str) -> dict:
    # 1. Fetch credentials securely
    token = await get_secret_store().get_secret("OPENCLAW_GATEWAY_TOKEN")
    settings = get_settings().openclaw

    client = OpenClawHttpClient(settings.gateway_url, token, settings.timeout_seconds)
    adapter = OpenClawAgentAdapter(client, settings.model)

    # 2. Translate Request
    messages = adapter.translate_request(task_payload, context_files)
    full_response_chunks = []

    try:
        # 3. Consume the SSE stream
        async for chunk in client.stream_execution(messages, settings.model):
            full_response_chunks.append(chunk)

            # CRITICAL: Ping Temporal to reset the timeout timer.
            # The chunk acts as a live log for the UI (docs/specs/084-live-log-tailing).
            activity.heartbeat(chunk)

    except asyncio.CancelledError:
        # 4. Handle Workflow Cancellation
        # Triggered if a user cancels the workflow in the MoonMind Dashboard.
        # The CancelledError bubbles up, causing the async httpx stream to close.
        # The OpenClaw Gateway detects the broken TCP pipe and stops execution naturally.
        activity.logger.info("OpenClaw task cancelled by Orchestrator. Severing connection.")
        raise

    # 5. Parse and Return
    final_text = "".join(full_response_chunks)
    result = adapter.parse_response(final_text)
    return result.model_dump()
```

---

### 6. Adapter Registry Wiring
**Target:** `moonmind/workflows/adapters/external_adapter_registry.py`

Finally, register the `OpenClawAgentAdapter` alongside existing adapters like `JulesAgentAdapter` and `CodexCloudAgentAdapter` so the orchestrator routes requests where `agent_type == "openclaw"` correctly.

```python
from .openclaw_agent_adapter import OpenClawAgentAdapter

def register_adapters(registry):
    # Existing adapters
    registry.register("jules", JulesAgentAdapter)
    registry.register("codex_cloud", CodexCloudAgentAdapter)

    # Add OpenClaw
    registry.register("openclaw", OpenClawAgentAdapter)
```

### Summary of Architectural Benefits
1. **Stateless Workflows:** Streaming eliminates the need to build a complex state-machine polling loop that constantly queries a database for task status. The Temporal worker simply holds the connection open.
2. **Fault Tolerant:** Temporal heartbeats ensure the Orchestrator knows the agent is healthy during long operations. If the worker node crashes, Temporal knows exactly when it failed and can restart the activity.
3. **Implicit Cancellation:** Terminating the Temporal Activity drops the HTTP connection, natively signaling the OpenClaw Gateway to halt execution without requiring a dedicated `/cancel` webhook.