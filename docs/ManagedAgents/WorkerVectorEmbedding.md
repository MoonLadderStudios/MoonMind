# Managed Agent Vector Embedding Workflow

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-03-14

## Objective

Define the best way for Temporal Managed Agents (e.g., OpenHands workers) to read/write MoonMind's vector database (Qdrant) while keeping security, observability, and performance sane.

## Current Baseline in MoonMind

- Workflow coordination and worker lifecycle are managed by **Temporal**:
  - Temporal Server orchestrates all `AgentTaskWorkflows`.
  - Artifact uploads are recorded via Temporal Activity results or directly to artifact blob stores.
- Vector storage is Qdrant (Docker service `qdrant`, default port `6333`).
- Embedding provider defaults to Google `gemini-embedding-001` in app settings.
- Embeddings are created in-process by MoonMind API Activities or directly by tools within the sandbox.

## Do All Vector Requests Need to Go Through the API Server?

No. They do not all need to go through the FastAPI control plane.

Two workflows are feasible:

1. Temporal Activity-proxied vector access:
   - Worker Sandbox asks Temporal to schedule an `EmbedAndUpsertActivity` (executed by a trusted API worker).
   - Strongest central policy and audit.
   - Highest Activity invocation overhead for very large batches.

2. Direct sandbox-to-Qdrant access:
   - Managed Agent sandbox runs tools that talk to Qdrant directly using `qdrant-client` targeting the host proxy.
   - Worker generates embeddings directly using an injected embedding model URL/key.
   - Lower latency and removes the API/Temporal Server as a data-plane bottleneck.

## Recommended Workflow (Best Fit)

Use a hybrid split:

- Temporal Server as control plane:
  - Workflow creation, activity scheduling, cancellation, retries, policy routing.
- Worker sandbox direct data plane for vector ops:
  - Embed content via sandboxed scripts.
  - Query/upsert directly to Qdrant via the internal Docker network or `docker-proxy`.

This gives you:

- High throughput for codebase retrieval/indexing workloads.
- Lower Temporal history overhead (no base64 embedding vectors saved into the Workflow History).
- Existing approval/audit model stays intact at the workflow boundaries.

## Guardrails for Direct Vector Access

1. Keep worker auth separate from user auth:
   - Use dedicated Qdrant credential scopes for agent sandbox vector operations.

2. Enforce embedding consistency:
   - Every worker must use the same embedding model and dimensions for a target collection.
   - Validate collection vector size before first upsert/query, handled upstream in `PrepareWorkspaceActivity`.

3. Keep observability centralized:
   - Sandbox scripts emit Temporal Activity heartbeats with progress details for key vector actions:
     - `embedding_started`
     - `qdrant_upsert_completed`
     - `qdrant_query_completed`
   - Include counts/timings in heartbeat payloads.

4. Namespace data:
   - Use collection-per-domain or metadata filters (e.g., repository, tenant, run_id).
   - Avoid unscoped global writes from workers.

## When API-Mediated Vector Access Is Better (Temporal Activities)

Use a dedicated Python/Node.js Temporal worker (running outside the sandbox) to execute `EmbedActivity` when you need:

- Strict tenant isolation and row-level policy enforcement in one place.
- Provider secrets never exposed to the agent sandboxes.
- Centralized request shaping, quotas, and governance.
- *This is the default for centralized Manifest Data Ingestion jobs.*

## Minimal Implementation Plan

1. Keep current Temporal workflow scheduling unchanged.
2. Add a sandbox-side vector client module:
   - `moonmind rag search` CLI wrapper for direct reads/writes.
   - Shared embedding config injected via sandbox env.
3. Add startup validation in `PrepareWorkspaceActivity`:
   - Confirm Qdrant reachable.
   - Confirm collection vector size matches embedding dimensions.
4. Emit Activity heartbeats for vector actions and include summary metrics.

## Suggested Runtime Environment for Sandboxes

- `GOOGLE_API_KEY` (or equivalent provider key) mapped for embedding calls.
- `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
- `QDRANT_HOST` / `QDRANT_PORT` (or `QDRANT_URL`) set to reachable internal endpoint.
- Existing Temporal Worker configuration to stream progress.
