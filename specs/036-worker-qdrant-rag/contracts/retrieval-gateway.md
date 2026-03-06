# Contract: RetrievalGateway HTTP Interface

## Endpoint
- **Method**: `POST`
- **Path**: `/retrieval/context`
- **Auth**: Worker bearer token scoped for retrieval-only actions (`capabilities.rag in ["direct-qdrant","gateway"]`). 403 when repo/tenant not allowed.
- **Content-Type**: `application/json`

## Request (`RetrievalQuery`)
```json
{
  "query": "Upgrade Qdrant data plane",
  "top_k": 8,
  "filters": {
    "repo": "moonmind",
    "tenant": "prod",
    "run_id": "job-123"
  },
  "overlay_policy": "include",
  "budgets": {
    "tokens": 1200,
    "latency_ms": 800
  }
}
```
- `query`: required, trimmed text. Empty strings rejected (400).
- `top_k`: optional override (1–50). Defaults to server-side `RAG_SIMILARITY_TOP_K`.
- `filters`: repo/tenant/run metadata forwarded to Qdrant payload filters.
- `overlay_policy`: `"include"` (default) or `"skip"` to bypass overlays entirely.
- `budgets`: optional ceilings; gateway enforces token/latency budgets and echoes actual usage.

## Response (`ContextPack`)
- Body identical to `contracts/context-pack.md`.
- Includes `transport: "gateway"` plus `usage.latency_ms` computed server-side.

Example (truncated):
```json
{
  "context_text": "### Retrieved Context\n1. services/api/routes.py ...",
  "items": [
    {
      "score": 0.82,
      "source": "services/api/routes.py",
      "trust_class": "canonical",
      "chunk_hash": "sha256:...",
      "payload": {
        "repo": "moonmind",
        "tenant": "prod"
      }
    }
  ],
  "filters": {
    "repo": "moonmind",
    "tenant": "prod",
    "run_id": "job-123"
  },
  "budgets": {
    "tokens": 1200,
    "latency_ms": 800
  },
  "usage": {
    "tokens": 640,
    "latency_ms": 420
  },
  "transport": "gateway",
  "retrieved_at": "2026-02-20T05:00:00Z",
  "telemetry_id": "ctx_01HZ..."
}
```

## Health Check
- `GET /retrieval/health` returns `{ "status": "ok" }`. Used by `moonmind worker doctor` to confirm fallback availability before contacting `/context`.

## Error Shapes
```json
{
  "error": {
    "code": "forbidden",
    "message": "Repository moonmind is not permitted for this worker token.",
    "action": "Request access in MoonMind admin."
  }
}
```
- `400 invalid_query`: empty query, overlay policy mismatch, bad filters.
- `401 unauthenticated`: missing/invalid token.
- `403 forbidden`: repo/tenant outside allow list.
- `408 latency_budget_exceeded`: retrieval aborted because measured latency exceeded `budgets.latency_ms`.
- `413 token_budget_exceeded`: server-estimated token requirement > budget.
- `500 retrieval_failed`: Qdrant unreachable or embedding provider down; message includes remediation steps but no secrets.

## Budget Semantics
- Gateway short-circuits requests predicted to exceed token budgets by comparing `top_k * chunk_chars` against `budgets.tokens`.
- When latency budget is provided, gateway aborts the request if elapsed time exceeds the budget and responds with `latency_budget_exceeded`, including actual latency.
- CLI callers may still enforce stricter client-side budgets; both sides log telemetry tags (`budget_type`, `budget_limit`, `budget_spent`).
