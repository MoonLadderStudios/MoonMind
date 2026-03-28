# Implementation Plan: Proxy-First Execution Paths

**Feature**: `specs/107-proxy-first-execution`  
**Created**: 2026-03-28  

## 1. Technical Design

To fulfill Phase 5 (Proxy-First Execution Paths) with the current MoonMind component layout:

### 1.1 Proxy Token Architecture
A standard provider key like `ANTHROPIC_API_KEY` expects an Anthropic completion endpoint (`https://api.anthropic.com`). We will implement an API path living on the `api_service` acting as the proxy (e.g., `/api/v1/proxy/anthropic`).
Instead of passing `ANTHROPIC_API_KEY`, MoonMind will mint a `MOONMIND_PROXY_TOKEN` (which could be the Workflow ID or Job ID tied to the `tasks` run context, or a fast JWT secret).
When the worker runtime (e.g., `jules`, our python agent runtime) receives this:
- It configures `ANTHROPIC_BASE_URL=http://moonmind-api:8000/api/v1/proxy/anthropic`
- It configures `ANTHROPIC_API_KEY={MOONMIND_PROXY_TOKEN}`

Upon receiving the proxied request, `api_service` checks the `Authorization` header (`Bearer MOONMIND_PROXY_TOKEN`). If valid, it fetches the actual encrypted secret internally (or reuses a profile logic), and forwards the request transparently to `api.anthropic.com`.

### 1.2 Identification of Runtimes (DOC-REQ-002, DOC-REQ-004)
We will document and map runtimes that support proxy-first execution:
- **jules**: A Python-based agent fully owned by MoonMind. Can seamlessly support a changed `BASE_URL` and dummy `API_KEY`. (Supports proxy-first).
- **codex**: Uses `config.toml` and allows configuring provider endpoints and API keys. Highly likely to support proxy-first.
- **claude_code**: Can point to different base URLs using env `ANTHROPIC_BASE_URL`. Should support proxy-first.

Exceptions ("escape-hatch") will be annotated explicitly on the database schema or Provider Profile abstraction level.

### 1.3 Schema Updates
The Provider Profile model in `api_service` will be broadened. Right now it defines `runtime_materialization_mode` (e.g., `api_key_env`, `env_bundle`, `config_bundle`). We will add a `supports_proxy: bool` flag to indicate that we should map a proxy token instead of raw retrieval.

## 2. API / Interface Changes

- **Added**: `/api/v1/proxy/{provider_id}/{path:path}` endpoint handling proxy forwarding with Python's HTTPX (`httpx.AsyncClient`).
- **Modified**: `ManagedAgentLauncher` (e.g., `moonmind/workflows/tasks/adapters/managed_agent_adapter.py`) to swap out `db_encrypted:` secret resolution for a synthetic proxy token if the profile sets `supports_proxy=True`.

## 3. Deployment Steps

No special deployment steps necessary, outside of the normal `alembic` migration if we add a new column to the provider profile database table.

## 4. Work Breakdown

1. Create `proxy` FastAPI router forwarding requests.
2. Update the Agent Adapter to mint temporal `MOONMIND_PROXY_TOKEN` payloads.
3. Update `docs/Security/ProviderProfiles.md` documenting the Proxy-First paths and Escape Hatches.
