# Implementation Plan: OpenClaw streaming external agent

**Branch**: `087-openclaw-adapter` | **Spec**: [spec.md](./spec.md)

## Technical Context

- **Language**: Python 3.12
- **Key deps**: Temporal (Python SDK), httpx, Pydantic
- **Target areas**: `moonmind/openclaw/`, `moonmind/workflows/adapters/`, `moonmind/workflows/temporal/`, `moonmind/schemas/agent_runtime_models.py`

## Constitution Check

- External agents remain behind explicit gates and env configuration.
- No secrets committed; tokens read from environment at activity execution.

## Architecture

1. **Gate** — `moonmind/openclaw/settings.py` (`OPENCLAW_ENABLED`, `OPENCLAW_GATEWAY_TOKEN`, optional URL/model/timeout).
2. **Transport** — `OpenClawHttpClient` streams SSE from `/v1/chat/completions`.
3. **Translation** — `openclaw_agent_adapter.py` builds chat messages and success `AgentRunResult`.
4. **Execution** — `moonmind/openclaw/execute.py` runs stream + throttled `activity.heartbeat`.
5. **Orchestration** — `integration.external_adapter_execution_style` + `MoonMind.AgentRun` branch for `streaming_gateway`; `integration.openclaw.execute` on integrations queue.
6. **Registry** — `OpenClawExternalAdapter` registered when gate passes.

## Phases

- Phase 0: Research — [research.md](./research.md) (complete)
- Phase 1: Schema + gate + client + adapter + activity + workflow + catalog + worker wiring
- Phase 2: Tests under `tests/unit/...` and integration test updates

## Data / contracts

- Reuse `AgentExecutionRequest` / `AgentRunResult`; extend `ProviderCapabilityDescriptor` with `executionStyle`.

## Quickstart (operators)

Set `OPENCLAW_ENABLED=true`, `OPENCLAW_GATEWAY_TOKEN`, optionally `OPENCLAW_GATEWAY_URL`, `OPENCLAW_DEFAULT_MODEL`. Dispatch external agent runs with `agentId=openclaw`.
