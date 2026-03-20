# Research: OpenClaw integration

## Decision

Use OpenAI-compatible `POST /v1/chat/completions` with `stream: true` and SSE `data:` lines; map `choices[0].delta.content` into aggregated assistant text.

## Rationale

Matches `docs/ExternalAgents/OpenClawAgentAdapter.md` and minimizes gateway-specific code.

## Alternatives considered

- **Polling synthetic run id** — rejected (misleading lifecycle, poor cancellation).
- **Separate OpenClaw SDK** — deferred until a stable published client exists.
