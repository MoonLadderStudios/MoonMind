# Workflow RAG – Agent Retrieval System

**Implementation tracking:** [`docs/tmp/remaining-work/Rag-WorkflowRag.md`](../tmp/remaining-work/Rag-WorkflowRag.md)

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-03-20

> **See also:** [ManifestIngestDesign.md](ManifestIngestDesign.md) (Temporal workflow architecture & implementation for ingesting data into Qdrant), [LlamaIndexManifestSystem.md](LlamaIndexManifestSystem.md) (manifest schema, data sources, and indexing pipeline)

This document captures how Temporal Managed Agents perform retrieval-augmented reasoning without any extra generative hops upstream. The loop is strictly **(embed query) → (Qdrant search) → (inject retrieved text into the Agent's context)** and leans on direct or proxied Qdrant access from within the `temporal-worker-sandbox`.

## Data Flow: Ingest → Retrieval

The RAG system has two distinct phases with separate code paths:

1. **Ingest (write path)** — Manifest YAML → LlamaIndex readers fetch documents → chunk/transform → embed via LlamaIndex → upsert vectors into Qdrant. Orchestrated by `MoonMind.ManifestIngest` Temporal workflow. Code: `moonmind/indexers/*`, `moonmind/manifest/*`.
2. **Retrieval (read path)** — Agent query → embed via `EmbeddingClient` (Google/OpenAI/Ollama) → Qdrant vector search → format `ContextPack` → inject into agent prompt. Code: `moonmind/rag/*`.

The retrieval path does **not** use LlamaIndex — it uses the lean `moonmind/rag/` layer with direct `qdrant-client` access. No LLM/generative calls are made; only embedding API calls (fractions of a cent per query).

## Status (Current vs Target)

- **Implemented**:
  - `moonmind rag search` CLI tool: fully implemented in `moonmind/rag/cli.py` and `moonmind/rag/service.py`.
  - `ContextRetrievalService`: embed → Qdrant search → `ContextPack` formatting.
  - `moonmind rag overlay upsert` and `moonmind rag overlay clean`: workspace overlay indexing.
  - `moonmind rag sync-embedding`: collection dimension validation.
  - Budgeting: per-query token and latency limits enforced.
  - Two transport modes: `direct` (worker → Qdrant) and `gateway` (worker → RetrievalGateway HTTP → Qdrant).
- **Legacy (deprecated)**:
  - Worker CLI entry point: `moonmind-codex-worker`.
  - Retrieval path: `QdrantRAG.retrieve_context()` executed directly inside a polling loop.
- **Not yet implemented**:
  - `RetrieveAgentContextActivity`: Temporal Activity wrapper around `ContextRetrievalService.retrieve()` for workflow-initiated pre-fetch.
  - Shipping `moonmind rag` tools natively inside the `temporal-worker-sandbox` Docker image.
  - Agent system prompt injection advertising RAG tool availability.

## Goals

- Give every Managed Agent a deterministic way to retrieve the latest relevant codebase context while editing a repo.
- Keep latency low by running embeddings and Qdrant searches directly from the worker host, or via efficient local gRPC gateways.
- Preserve Temporal as the control plane while letting the data plane be worker ↔ Qdrant.
- Provide an explicit signal to the Agent (via prompt configuration) that database retrieval tools are available.

## Agent-Facing Flow

1. **Pre-flight (Temporal Activity: `PrepareWorkspaceActivity`)**
   - Sandbox boots with `DEFAULT_EMBEDDING_PROVIDER`, `GOOGLE_EMBEDDING_MODEL` (or `OPENAI_EMBEDDING_MODEL`), `QDRANT_HOST`, and `QDRANT_PORT` exported.
   - Validation checks Qdrant connectivity, collection dimensions, and embedding model alignment.
2. **Embed the query**
   - Use the configured embedding provider directly from the sandbox or via a local embedding proxy.
   - Normalize whitespace and truncate to the embedding model's supported token limit.
3. **Search Qdrant**
   - Invoke the lean `qdrant-client` from the `moonmind rag search` tool.
   - Default search parameters should follow runtime settings: `similarity_top_k` defaults to `5` via `RAG_SIMILARITY_TOP_K`, plus payload filters (`repo`, `tenant`).
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
   - `offset_start` and `offset_end` are 0-based character offsets in the source file text.
5. **Inject into Agent Prompt**
   - Prepend the `context_text` block (plus citations) to the next LLM turn before the agent writes any reasoning or code.
   - Cache the retrieved pack alongside Temporal artifacts.

## Lean CLI Tool (`moonmind rag search`)

- Shell entry point that Agents can call explicitly while creating a plan.
- Internally wires together the embedding helper and the direct `qdrant-client` without any intermediate APIs.
- Supports:
  - `--query "..."`
  - `--filters repo=my-repo tenant=moonmind`
  - `--top-k 12`
  - `--overlay include` (see overlay section below)
- Outputs `context_text` to stdout, supports `--json` for full `ContextPack` output.

## Making the Capability Obvious to Agents

To guarantee the agent knows that database retrieval is available:

- **Bootstrap prompt snippet**: The system prompt injected into the OpenHands/Agent container includes: “You have access to a semantic search tool: `moonmind rag search`. Use it to find relevant code patterns and definitions before making structural changes.”
- **Mission Control Visibility**: The UI shows a `RAG operations` badge plus the retrieval timestamps gathered from Temporal Activity heartbeats, so humans reviewing workflows can confirm context was fetched.

## Guardrails

- **Separate credentials**: Worker auth tokens remain distinct from Qdrant API keys.
- **Embedding consistency**: Refuse to query collections whose vector size does not match the current embedding model.
- **Observability**: Every embedding and search emits Temporal events plus StatsD counters (`rag.search.latency_ms`, `rag.search.hits`).
- **Namespacing**: Payload filters ensure workers only see the data belonging to the specific `repo` mapped to the Temporal workflow.
- **Budgeting**: `moonmind rag search` enforces per-query token/latency budgets so runaway agent loops cannot overload Qdrant.

## Workspace Overlay Indexing

Agents often edit files that have not been embedded in the canonical index yet. Solve staleness by adding a run-scoped overlay:

- After writing or refactoring a file, the agent's tool layer calls `moonmind rag overlay upsert path/to/file.py`.
- Overlay vectors share the canonical collection with payload `{run_id, trust_class="workspace_overlay", expires_at}`.
- Queries search both canonical data and overlay data, merging hits by `(path, chunk_hash)` so the freshest version wins.

### Overlay Storage Trade-Offs

| Option | Strengths | Weaknesses | Suggested Use |
|---|---|---|---|
| Dedicated overlay collection per run (`repo__overlay__<run_id>`) | Fast cleanup via collection drop; simple TTL boundaries; minimal filter complexity | More collections to manage; potential control-plane overhead at high run volume | Default for isolated CI-style worker runs |
| Shared canonical collection + overlay payload markers | Fewer collections; easier cross-run analytics in one place | Harder cleanup (`expires_at` sweeps); more complex filters; higher risk of stale overlay bleed if filters are wrong | Long-lived deployments that optimize for operational simplicity |

### Deterministic Merge Logic (Canonical + Overlay)

1. Build dedup key as `(path, chunk_hash)`.
2. For each key, prefer a non-expired overlay hit over canonical hit.
3. Sort final merged list by `(source_precedence, score desc)` where `source_precedence` is overlay first, then canonical.

## Worker integration

CLI tools (`moonmind rag search`, overlay upsert/clean, sync-embedding) and `ContextRetrievalService` budgeting exist. **Target:** the same capabilities are available inside sandbox workers via packaged tooling, a `RetrieveAgentContextActivity`, workspace prep validation against Qdrant, prompt hints, metrics, and published `ContextPack` documentation. Remaining steps are in [`docs/tmp/remaining-work/Rag-WorkflowRag.md`](../tmp/remaining-work/Rag-WorkflowRag.md).

## Environment Variables

| Variable | Required | Description |
|----------|:--------:|-------------|
| `DEFAULT_EMBEDDING_PROVIDER` | ✅ | `google`, `openai`, or `ollama` |
| `GOOGLE_EMBEDDING_MODEL` | If Google | e.g. `models/text-embedding-004` |
| `OPENAI_EMBEDDING_MODEL` | If OpenAI | e.g. `text-embedding-3-large` |
| `GOOGLE_API_KEY` | If Google | API key for Google embeddings |
| `OPENAI_API_KEY` | If OpenAI | API key for OpenAI embeddings |
| `QDRANT_HOST` | ✅ | Qdrant hostname (default: `qdrant`) |
| `QDRANT_PORT` | | Qdrant port (default: `6333`) |
| `QDRANT_API_KEY` | | Qdrant auth key (if using cloud) |
| `VECTOR_STORE_COLLECTION_NAME` | | Collection name (default from app settings) |
| `RAG_SIMILARITY_TOP_K` | | Default top-k for search (default: `5`) |
| `RAG_OVERLAY_MODE` | | `collection` (default) |
| `MOONMIND_RETRIEVAL_URL` | | RetrievalGateway URL for `gateway` transport |
| `MOONMIND_RUN_ID` | | Run ID for overlay scoping |
