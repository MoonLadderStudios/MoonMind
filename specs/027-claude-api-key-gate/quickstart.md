# Quickstart: Claude Runtime with API-Key Gating

## Prerequisites

- Configure `ANTHROPIC_API_KEY` in `.env` if you want Claude tasks.
- Add compose profile `claude` only when key is present.

## Typical flow

1. Start default services:

```bash
docker compose up -d
```

2. Start Claude worker only when key is set:

```bash
export ANTHROPIC_API_KEY=...
docker compose --profile claude up -d claude-worker
```

3. Confirm UI/queue behavior:

- Claude does not appear as a runtime option without the key.
- `targetRuntime=claude` POST requests should be rejected with the runtime-gate message until key is set.
