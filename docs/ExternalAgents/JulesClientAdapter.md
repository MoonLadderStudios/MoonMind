# Technical Design: Jules Client Adapter with MCP Support

## 1. Objective

Integrate the Jules API into MoonMind, providing a robust, asynchronous Python client that interacts with Jules tasks. By exposing this client through the existing unified MCP tool surface, autonomous agents can seamlessly create, fetch, and resolve Jules tasks during their workflows.

## 2. Architecture Overview

The integration consists of four layers:

1. **Configuration Layer:** `JulesSettings(BaseSettings)` in `moonmind/config/jules_settings.py`, loaded as a typed sub-settings field on `AppSettings`.
2. **Schema Layer:** Pydantic models in `moonmind/schemas/jules_models.py` following camelCase alias conventions.
3. **Adapter Layer:** `JulesClient` in `moonmind/workflows/adapters/jules_client.py` — a long-lived `httpx.AsyncClient` wrapper with manual retry logic.
4. **MCP Tooling Layer:** `JulesToolRegistry` in `moonmind/mcp/jules_tool_registry.py` following the `QueueToolRegistry` dispatch pattern, served through the existing `/mcp/tools` endpoints alongside queue tools.

---

## 3. Detailed Component Design

### 3.1. Configuration & Secrets

**New File:** `moonmind/config/jules_settings.py`
**New File:** `moonmind/config/paths.py` — shared `ENV_FILE` path constant extracted from `settings.py` to break circular imports.
**Modified:** `moonmind/config/settings.py`, `.env-template`

Environment Variables:
- `JULES_API_URL` — Base URL for the Jules instance.
- `JULES_API_KEY` — Authentication token.
- `JULES_ENABLED` — Feature gate (default `false`).
- `JULES_TIMEOUT_SECONDS` — HTTP request timeout (default `30.0`).
- `JULES_RETRY_ATTEMPTS` — Maximum retry attempts for transient failures (default `3`).
- `JULES_RETRY_DELAY_SECONDS` — Delay between retry attempts (default `1.0`).

`JulesSettings` follows the `AnthropicSettings` pattern with `validation_alias=AliasChoices(...)` for env var flexibility. It is registered on `AppSettings` as a typed field: `jules: JulesSettings = Field(default_factory=JulesSettings)`. Both `jules_settings.py` and `settings.py` import `ENV_FILE` from `moonmind.config.paths` to avoid circular dependencies.

### 3.2. Schema Definitions

**New File:** `moonmind/schemas/jules_models.py`

Models follow `agent_queue_models.py` conventions:
- `model_config = ConfigDict(populate_by_name=True)` on every model
- camelCase `alias` on every field
- `Field(...)` for required, `Field(None, ...)` for optional
- `extra="ignore"` on response models to tolerate new fields from the Jules API without breaking deserialization

Models: `JulesCreateTaskRequest`, `JulesResolveTaskRequest`, `JulesGetTaskRequest`, `JulesTaskResponse`.

### 3.3. Jules Async Client Adapter

**New File:** `moonmind/workflows/adapters/jules_client.py`

Follows the `QueueApiClient` pattern:
- Long-lived `httpx.AsyncClient` with `base_url`, `timeout`, and auth headers.
- Optional client injection for testability (`client: httpx.AsyncClient | None`).
- `_owns_client` ownership tracking for proper `aclose()` behavior.
- Manual retry loop (NOT tenacity) matching `QueueApiClient._post_json`:
  - Retry on 5xx + 429 (rate limit, with `Retry-After` support) + `TransportError` + `TimeoutException`
  - Fail immediately on other 4xx
  - Wrap failures in `JulesClientError(RuntimeError)`
- `JulesClientError` carries structured fields (`status_code`, `request_path`) and produces a scrubbed `__str__` that never leaks secrets (API keys, bearer tokens, etc.).
- Timeout, retry attempts, and retry delay are constructor parameters wired from `JulesSettings` env vars.
- Public methods: `create_task()`, `resolve_task()`, `get_task()`
- Response parsing via `JulesTaskResponse.model_validate(data)`

### 3.4. MCP Tool Registration

**New File:** `moonmind/mcp/jules_tool_registry.py`
**Modified:** `api_service/api/routers/mcp_tools.py`

`JulesToolRegistry` follows `QueueToolRegistry` structure:
- Reuses shared types: `ToolMetadata`, `ToolNotFoundError`, `ToolArgumentsValidationError`, `_ToolDefinition`
- Own context: `JulesToolExecutionContext` frozen dataclass with `client: JulesClient`
- Registers three tools: `jules.create_task`, `jules.resolve_task`, `jules.get_task`
- Same `list_tools()` / `call_tool()` dispatch pattern

**Unified MCP surface:** Jules tools are served through the existing `/mcp/tools` endpoints by composing both registries in `mcp_tools.py`:
- `GET /mcp/tools` — merges tool lists from `QueueToolRegistry` and (when enabled) `JulesToolRegistry`
- `POST /mcp/tools/call` — dispatches `jules.*` tools to `JulesToolRegistry`, all others to `QueueToolRegistry`
- When `JULES_ENABLED=false` or credentials are missing, Jules tools are excluded from discovery and dispatch entirely

**Error strategy:** Tool handlers let `JulesClientError` propagate as an exception. The router's `_to_http_exception` maps it to structured HTTP error responses with stable `detail.code` values (e.g., `jules_request_failed`, `jules_rate_limited`), consistent with how queue tool errors are handled. This avoids the anti-pattern of returning `{"error": ...}` inside a 200 response, which would force agents to parse two different error shapes.

## 4. Error Handling & Retry Strategy

- **Timeouts:** Configurable via `JULES_TIMEOUT_SECONDS` (default 30 seconds).
- **Retries:** Manual retry loop with configurable attempts (`JULES_RETRY_ATTEMPTS`) and delay (`JULES_RETRY_DELAY_SECONDS`). Retries on 5xx, 429 (with `Retry-After` header support), `TransportError`, and `TimeoutException`. Immediate failure on other 4xx.
- **Cancellation:** The retry loop does not catch `asyncio.CancelledError` (a `BaseException`), so task cancellation propagates cleanly through long-running operations.
- **Error mapping:** `JulesClientError` carries structured metadata (`status_code`, `request_path`) and is mapped to HTTP error responses by the MCP router, matching the existing `_to_http_exception` pattern used for queue tool errors.

## 5. Testing Plan

- **Unit Tests:** `tests/unit/workflows/adapters/test_jules_client.py` using `httpx.MockTransport` (no external mocking libraries).
  - Success cases for create, resolve, and get
  - Retry on 5xx, then success
  - Retry on 429 with `Retry-After` header
  - Immediate failure on 4xx (non-429)
  - `aclose()` ownership behavior
- **Tool Registry Tests:** `tests/unit/mcp/test_jules_tool_registry.py` using fake client classes with canned responses and call tracking.
  - Tool discovery returns expected tools
  - Dispatch to correct handler
  - Unknown tool raises `ToolNotFoundError`
  - Invalid arguments raise `ToolArgumentsValidationError`
  - `JulesClientError` propagates (not caught by handler)
- **Feature Gate Tests:**
  - `GET /mcp/tools` excludes `jules.*` when `JULES_ENABLED=false`
  - `POST /mcp/tools/call` with `jules.*` returns 404 with `detail.code="tool_not_found"` when disabled
- **Secret Redaction Tests:**
  - Error messages surfaced through MCP responses do not contain `JULES_API_KEY` or bearer token values
- **Verification:** Import checks, settings load, pre-commit hooks.

## 6. Relationship to Agent Runtime Contracts

`JulesClient` is the low-level HTTP transport adapter. It is wrapped by `JulesAgentAdapter` (`moonmind/workflows/adapters/jules_agent_adapter.py`), which implements the `AgentAdapter` protocol and translates between:

- `AgentExecutionRequest` → `JulesCreateTaskRequest`
- `JulesTaskResponse` → `AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`

The `JulesAgentAdapter` uses `normalize_jules_status()` (`moonmind/schemas/jules_models.py`) to map raw Jules status strings into the bounded set (`queued`, `running`, `succeeded`, `failed`, `canceled`, `unknown`) before mapping those to canonical `AgentRunState` literals.

For the unified execution model that governs how `JulesAgentAdapter` (and all other adapters) are invoked via `MoonMind.AgentRun` child workflows, see [`ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md).
