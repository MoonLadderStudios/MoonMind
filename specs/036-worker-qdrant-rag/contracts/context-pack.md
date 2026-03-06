# Contract: Context Pack Retrieval Schema

## Request Payload (`RetrievalQuery`)
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
  "transport": "direct",
  "budgets": {
    "tokens": 1200,
    "latency_ms": 800
  }
}
```
- `overlay_policy`: `include` (default) or `skip`.
- `transport`: `direct` (worker ↔ Qdrant) or `gateway` (worker ↔ RetrievalGateway).

## Response Payload (`ContextPack`)
```json
{
  "context_text": "### Retrieved Context\n- [/services/api/routes.py#L120-L160] ...",
  "items": [
    {
      "score": 0.82,
      "source": "services/api/routes.py",
      "offset_start": 1180,
      "offset_end": 1423,
      "trust_class": "canonical",
      "chunk_hash": "sha256:...",
      "payload": {
        "repo": "moonmind",
        "tenant": "prod",
        "run_id": ""
      }
    },
    {
      "score": 0.77,
      "source": "specs/024-live-task-handoff/spec.md",
      "offset_start": 250,
      "offset_end": 640,
      "trust_class": "overlay",
      "chunk_hash": "sha256:...",
      "payload": {
        "repo": "moonmind",
        "tenant": "prod",
        "run_id": "job-123",
        "trust_class": "workspace_overlay"
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
  "transport": "direct",
  "retrieved_at": "2026-02-20T05:00:00Z",
  "telemetry_id": "ctx_01HZ..."
}
```

## Error Envelope (shared)
```json
{
  "error": {
    "code": "embedding_dimension_mismatch",
    "message": "Collection moonmind__repo expects dim=768 but gemini-embedding-001 outputs 3072.",
    "action": "Run moonmind rag sync-embedding --collection moonmind__repo"
  }
}
```

## Fallback & Budget Semantics
- RetrievalGateway enforces budgets server-side and returns `usage` even on partial failures.
- Workers enforce latency + token ceilings client-side; CLI aborts if budgets exceeded before Qdrant responds.
- Overlay merges always annotate `trust_class` so downstream prompts can differentiate canonical vs local chunks.
