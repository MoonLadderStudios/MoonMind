# Worker Vector Embedding Workflow for MoonMind

## Objective

Define the best way for Codex CLI workers to read/write MoonMind's vector database (Qdrant) while keeping security, observability, and performance sane.

## Current Baseline in MoonMind

- Queue coordination and worker lifecycle are already API-driven:
  - Worker claims and updates jobs via `/api/queue/...`.
  - Worker uploads artifacts via `/api/queue/jobs/{jobId}/artifacts/upload`.
- Vector storage is Qdrant (Docker service `qdrant`, default port `6333`).
- Embedding provider defaults to Google in app settings.
- Embeddings are created in-process by MoonMind using `build_embed_model(...)` and `GoogleGenAIEmbedding`.

## Do All Vector Requests Need to Go Through FastAPI?

No. They do not all need to go through FastAPI.

Two workflows are feasible:

1. API-proxied vector access:
   - Worker asks FastAPI to embed/query/upsert.
   - Strongest central policy and audit.
   - Highest API load and latency.

2. Direct worker-to-Qdrant access:
   - Worker talks to Qdrant directly using `qdrant-client`.
   - Worker generates embeddings directly using Gemini API key.
   - Lower latency and removes FastAPI as a data-plane bottleneck.

## Recommended Workflow (Best Fit)

Use a hybrid split:

- FastAPI as control plane:
  - Job creation, claiming, lease heartbeat, completion/failure, artifacts, policy checks.
- Worker direct data plane for vector ops:
  - Embed content with Gemini.
  - Query/upsert directly to Qdrant.

This gives you:

- High throughput for retrieval/indexing workloads.
- Lower FastAPI overhead.
- Existing queue/approval/audit model stays intact.

## Guardrails for Direct Vector Access

1. Keep worker auth separate from user auth:
   - Continue using worker token for queue APIs.
   - Use dedicated Qdrant credential scope for worker vector operations.

2. Enforce embedding consistency:
   - Every worker must use the same embedding model and dimensions for a target collection.
   - Validate collection vector size before first upsert/query for a job.

3. Keep observability centralized:
   - Worker emits queue events for key vector actions:
     - `embedding_started`
     - `embedding_completed`
     - `qdrant_upsert_completed`
     - `qdrant_query_completed`
   - Include counts/timings in event payloads.

4. Namespace data:
   - Use collection-per-domain or metadata filters (e.g., repository, tenant, run_id).
   - Avoid unscoped global writes from workers.

## When API-Mediated Vector Access Is Better

Use FastAPI as the middleman when you need:

- Strict tenant isolation and row-level policy enforcement in one place.
- Secrets never exposed to workers.
- Centralized request shaping, quotas, and governance.

## Minimal Implementation Plan

1. Keep current queue workflow unchanged.
2. Add a worker-side vector client module:
   - `qdrant-client` for reads/writes.
   - Shared embedding config from environment.
3. Add startup validation in worker:
   - Confirm Qdrant reachable.
   - Confirm collection vector size matches embedding dimensions.
4. Emit queue events for vector actions and include summary metrics.
5. Keep API fallback path for environments where direct Qdrant connectivity is blocked.

## Default Embedding Model Decision

MoonMind should default to:

- `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`

This default is now aligned in:

- `moonmind/config/settings.py`
- `.env-template`
- `.env`

## Suggested Runtime Environment for Workers

- `GOOGLE_API_KEY` set for embedding calls.
- `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
- `QDRANT_HOST` / `QDRANT_PORT` (or `QDRANT_URL`) set to reachable Qdrant endpoint.
- `MOONMIND_RAG_AUTO_CONTEXT=true` to auto-inject retrieved context into worker prompts (`false` disables prompt injection while keeping CLI retrieval available).
- Existing `MOONMIND_URL` + worker token for queue control-plane calls.
