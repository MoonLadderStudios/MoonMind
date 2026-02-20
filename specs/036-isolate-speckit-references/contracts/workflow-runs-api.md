# Contract: Workflow Runs API Canonical + Legacy Alias

## Canonical Routes

- `POST /api/workflows/runs`
- `GET /api/workflows/runs`
- `GET /api/workflows/runs/{run_id}`
- `GET /api/workflows/runs/{run_id}/tasks`
- `GET /api/workflows/runs/{run_id}/artifacts`
- `POST /api/workflows/runs/{run_id}/retry`
- `GET /api/workflows/codex/shards`
- `POST /api/workflows/runs/{run_id}/codex/preflight`

## Legacy Compatibility Routes (Deprecated)

- Existing `/api/workflows/speckit/*` routes remain available with identical request/response payload contracts.

## Deprecation Behavior

For legacy `/api/workflows/speckit/*` requests, responses include:

- `Deprecation: true`
- `X-MoonMind-Deprecated-Route: /api/workflows/speckit`
- `X-MoonMind-Canonical-Route: /api/workflows/...`

and the server logs a structured alias-usage event.

## Backward Compatibility Rule

Canonical and legacy route variants MUST produce equivalent business behavior and payload shapes for the same operation.
