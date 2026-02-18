# Worker Retrieval Loop for MoonMind

This document captures how Codex CLI workers perform retrieval-augmented reasoning without any extra generative hops. The loop is strictly **(embed query) → (Qdrant search) → (inject retrieved text into the Codex prompt)** and leans on the direct Qdrant client for all worker reads/writes.

## Goals

- Give every worker a deterministic way to retrieve the latest relevant context while editing a repo.
- Keep latency low by running embeddings and Qdrant searches directly from the worker host.
- Preserve FastAPI as the control plane while letting the data plane be worker ↔ Qdrant.
- Provide an explicit signal in the job metadata and CLI UX so workers always know that database retrieval is available.

## Worker-Facing Flow

1. **Pre-flight**
   - Worker boots with `GOOGLE_API_KEY`, `GOOGLE_EMBEDDING_MODEL`, and `QDRANT_URL` exported.
   - `moonmind worker doctor` validates Qdrant connectivity, collection dimensions, and embedding model alignment (reuse the checks from `docs/WorkerVectorEmbedding.md`).
2. **Embed the query**
   - Use the configured embedding provider (Gemini/OpenAI/Ollama) directly inside the worker process.
   - Normalize whitespace and truncate to the embedding model's supported token limit.
3. **Search Qdrant**
   - Invoke the lean `qdrant-client` from the new `moonmind rag search` command.
   - Default search parameters: `top_k=8`, `score_threshold=0.68`, plus payload filters (`repo`, `tenant`, `run_id`).
   - Optionally issue follow-up `scroll` calls when pagination is required.
4. **Format the context pack**
   - Convert hits into a deterministic JSON block:
     ```json
     {
       "context_text": "... ready-to-paste markdown ...",
       "items": [
         {
           "score": 0.82,
           "source": "services/api_service/routes.py",
           "offset_start": 1203,
           "offset_end": 1460,
           "trust_class": "canonical"
         }
       ]
     }
     ```
   - Emit telemetry events (`qdrant_query_completed`) with counts and timings.
5. **Inject into Codex prompt**
   - Prepend the `context_text` block (plus citations) to the next Codex turn before writing any reasoning or code.
   - Cache the retrieved pack alongside artifacts so the dashboard can render “Context used”.

## Lean CLI Command (`moonmind rag search`)

- Shell entry point that Codex workers call before (and sometimes after) crafting a plan.
- Internally wires together the embedding helper and the direct `qdrant-client` without any intermediate APIs.
- Supports:
  - `--query "..."`
  - `--filters repo=my-repo tenant=moonmind`
  - `--top-k 12`
  - `--overlay include` (see overlay section below)
- Outputs both the `context_text` string (stdout) and a structured JSON blob (stderr or file) for downstream logging.
- Errors include actionable hints (e.g., collection dimension mismatch, missing credential) so workers can fix setup fast.

## Making the Capability Obvious to Workers

To guarantee workers know that database retrieval is available and required:

- **Job manifest flag**: queue API sets `job.capabilities.rag = "direct-qdrant"`. The CLI prints “RAG available: run `moonmind rag search` to pull context from the knowledge base.” during `job start`.
- **Bootstrap prompt snippet**: the default Codex warm-up message includes a bullet: “Before coding, call `moonmind rag search` to load repo knowledge from Qdrant.”
- **CLI self-check**: `moonmind worker doctor` fails hard (with clear instructions) if Qdrant credentials are missing, preventing workers from starting tasks without retrieval access.
- **UI reminder**: the task dashboard shows a `RAG ready` badge plus the last retrieval timestamp so humans reviewing runs can confirm context was fetched.
- **Run log breadcrumbs**: each successful context fetch logs `context_pack_id` and the sources, helping workers trust the mechanism and reviewers audit it.

## Guardrails (aligns with `docs/WorkerVectorEmbedding.md`)

- **Separate credentials**: Worker queue tokens remain distinct from Qdrant API keys. Rotate each independently.
- **Embedding consistency**: refuse to query collections whose vector size does not match the current embedding model; provide a `moonmind rag sync-embedding` helper to update metadata.
- **Observability**: every embedding and search emits queue events plus StatsD counters (`rag.search.latency_ms`, `rag.search.hits`).
- **Namespacing**: payload filters (`repo`, `tenant`, `resource_type`, `run_id`) ensure workers only see the data they are allowed to use.
- **Budgeting**: `moonmind rag search` enforces per-query token/latency budgets so runaway loops cannot overload Qdrant.

## Retrieval Gateway Fallback

When workers cannot reach Qdrant directly (e.g., locked-down VPCs), add a `RetrievalGateway` endpoint that exposes `retrieve_context_pack(query, filters, top_k, budget)` without any generation. The gateway can reuse the existing `QdrantRAG` wrapper and still return the same `context pack` schema, keeping the worker-side CLI identical aside from pointing at `MOONMIND_RETRIEVAL_URL`.

## Workspace Overlay Indexing

Workers often edit files that have not been embedded yet. Solve staleness by adding a run-scoped overlay:

- After writing or refactoring a file, the worker calls `moonmind rag overlay upsert path/to/file.py`.
- Overlay vectors either go into a dedicated collection (`repo__overlay__<run_id>`) or share the canonical collection with payload `{run_id, trust_class="workspace_overlay", expires_at}`.
- Queries search both canonical data and overlay data, merging hits by `(path, chunk_hash)` so the freshest version wins.

## Implementation Checklist

1. Ship the `moonmind rag search` and `moonmind rag overlay` commands (Python module that wraps `qdrant-client`).
2. Extend worker bootstrap to run `moonmind worker doctor` and block execution if RAG is unavailable.
3. Update queue/job metadata and CLI output to advertise `RAG available` plus usage instructions.
4. Emit observability events and StatsD counters for every embed/search/upsert.
5. Document the context pack schema so chat, `/context`, and workers all share the same format.
6. Add the RetrievalGateway fallback deployment for environments where direct Qdrant access is not permitted.
7. Enable optional overlay indexing to keep retrieval fresh for in-flight edits.

Following this plan keeps worker RAG simple, fast, and obvious to anyone running Codex CLI: embed locally, query Qdrant via the lean client, and inject the returned text right into the next prompt.
