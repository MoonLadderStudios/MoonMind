# Managed Agent Retrieval Loop for MoonMind

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-03-14

This document captures how Temporal Managed Agents perform retrieval-augmented reasoning without any extra generative hops upstream. The loop is strictly **(embed query) → (Qdrant search) → (inject retrieved text into the Agent's context)** and leans on direct or proxied Qdrant access from within the `temporal-worker-sandbox`.

## Status (Current vs Target)

- **Legacy implementation**:
  - Worker CLI entry point: `moonmind-codex-worker`.
  - Retrieval path: `QdrantRAG.retrieve_context()` executed directly inside a polling loop.
- **Target state described below**:
  - `RetrieveAgentContextActivity` executes the RAG search within a Temporal Workflow.
  - Alternately, the agent container exposes `moonmind rag search` and `moonmind rag overlay upsert` commands for the Agent (e.g. OpenHands) to explicitly call during its execution loop when it determines it needs more context.
  - A serialization adapter ensures agents receive a stable JSON context-pack contract.

## Goals

- Give every Managed Agent a deterministic way to retrieve the latest relevant codebase context while editing a repo.
- Keep latency low by running embeddings and Qdrant searches directly from the worker host, or via efficient local gRPC gateways.
- Preserve Temporal as the control plane while letting the data plane be worker ↔ Qdrant.
- Provide an explicit signal to the Agent (via prompt configuration) that database retrieval tools are available.

## Agent-Facing Flow

1. **Pre-flight (Temporal Activity: `PrepareWorkspaceActivity`)**
   - Sandbox boots with `GOOGLE_EMBEDDING_MODEL`, `QDRANT_HOST`, and `QDRANT_PORT` exported.
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

## Implementation Checklist

1. Ship the `moonmind rag search` and `moonmind rag overlay` bash wrappers natively inside the `temporal-worker-sandbox` image.
2. Extend `PrepareWorkspaceActivity` to validate Qdrant topology before launching the Agent container.
3. Update agent system prompts to advertise `RAG tools available`.
4. Emit observability metrics for every embed/search/upsert from the sandboxed tools.
5. Document the context pack schema.
6. Enable optional overlay indexing to keep retrieval fresh for in-flight edits.
