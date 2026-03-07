# Feature Specification: Direct Worker Qdrant Retrieval Loop

**Feature Branch**: `033-worker-qdrant-rag`  
**Created**: 2026-02-20  
**Status**: Draft  
**Input**: User description: "Implement the recommended direct worker-to-Qdrant retrieval path for Codex CLI workers while keeping RAG fully non-generative."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Worker pulls repo context via CLI (Priority: P1)

As a Codex CLI worker, I can run `moonmind rag search` with my query so the tool embeds it, queries Qdrant directly, and returns a ready-to-paste context block with citations before I continue coding.

**Why this priority**: Every task needs deterministic context before writing code; lacking it risks low-quality fixes and rework.

**Independent Test**: Start a worker session with valid Qdrant + embedding credentials, run `moonmind rag search --query "Update API auth"`, and verify that stdout contains formatted markdown context and stderr stays empty aside from progress notes.

**Acceptance Scenarios**:

1. **Given** valid Qdrant credentials and a repo-scoped payload filter, **When** the worker executes `moonmind rag search --query "upgrade qdrant"`, **Then** the command emits a JSON payload containing `context_text` and `items[]` along with a markdown block on stdout.
2. **Given** the worker requests `--top-k 8 --filters repo=moonmind tenant=prod`, **When** the command runs, **Then** only chunks matching those filters are included and the final list respects the requested `top_k` count after dedupe.
3. **Given** namespaced overlay entries for the active job, **When** the worker sets `--overlay include`, **Then** overlay chunks are merged ahead of canonical hits using `(path, chunk_hash)` precedence rules.

---

### User Story 2 - Guardrails block unsafe retrievals (Priority: P1)

As an operator, I need the worker CLI to enforce guardrails so misconfiguration (missing credentials, embedding dimension drift, unauthorized collection) produces actionable failures before a query executes.

**Why this priority**: Silent failures or mis-scoped retrieval leaks tenant data and wastes job time.

**Independent Test**: Run `moonmind rag search` with an embedding dimension mismatch; verify it refuses to issue the Qdrant call, prints the expected vs actual dimensions, and exits non-zero with remediation guidance.

**Acceptance Scenarios**:

1. **Given** Qdrant is unreachable or credentials fail, **When** the worker runs `moonmind rag search`, **Then** the CLI reports an auth/connectivity error, emits `embedding_aborted` telemetry, and exits non-zero without partial context output.
2. **Given** the worker uses an embedding model whose dimension differs from the target collection, **When** the command initializes, **Then** it halts before querying and instructs the worker to run the sync helper.
3. **Given** StatsD metrics host is configured, **When** embeddings/search complete, **Then** the CLI emits counters/timers (`rag.search.latency_ms`, `rag.search.hits`) and logs queue events referencing the job/run IDs.

---

### User Story 3 - RetrievalGateway fallback keeps governance centralized (Priority: P2)

As a platform admin, I can deploy a retrieval-only gateway so workers without direct Qdrant connectivity still receive the same context pack format over HTTPS without routing through the `/context` generative endpoint.

**Why this priority**: Some regulated environments cannot distribute Qdrant credentials to worker hosts but still expect deterministic retrieval loops.

**Independent Test**: Point a worker at `MOONMIND_RETRIEVAL_URL`, run `moonmind rag search --query ... --transport gateway`, and verify the CLI issues a single POST to the gateway, receives the context pack, and displays identical output.

**Acceptance Scenarios**:

1. **Given** `MOONMIND_RETRIEVAL_URL` is set, **When** a worker calls `moonmind rag search`, **Then** the CLI automatically routes via the RetrievalGateway and omits direct Qdrant auth prompts.
2. **Given** the gateway enforces repo/tenant allow-lists, **When** a worker outside scope makes a request, **Then** the response is an authorization error and the CLI displays it without leaking secrets.
3. **Given** the worker passes `--budget tokens=1200 latency_ms=800`, **When** the gateway executes, **Then** it enforces budgets and includes actual consumption details in the result payload.

---

### User Story 4 - Workspace overlay keeps context fresh (Priority: P2)

As a worker editing files that are not yet embedded, I can upsert overlay vectors for my run and have them automatically merged with canonical data so retrieval includes my recent changes without waiting for a global re-index.

**Why this priority**: Without overlays, retrieval remains stale for in-progress edits and undermines the value of the loop.

**Independent Test**: Modify a file locally, call `moonmind rag overlay upsert path/to/file.py`, then query `moonmind rag search --overlay include` and verify overlay chunks surface ahead of canonical ones referencing the prior text.

**Acceptance Scenarios**:

1. **Given** a worker uploads overlay vectors for multiple files, **When** they run `moonmind rag search`, **Then** duplicates are deduped via `(path, chunk_hash)` and overlay chunks win when not expired.
2. **Given** overlay TTL expires or `--overlay skip` is passed, **When** the worker queries, **Then** only canonical entries remain in the result order.
3. **Given** overlay and canonical hits tie on source precedence, **When** the merge occurs, **Then** ties break by score while preserving deterministic ordering for reproducibility.

### Edge Cases

- Qdrant becomes temporarily unreachable mid-query; CLI should retry with exponential backoff before failing with actionable diagnostics.
- Worker lacks `GOOGLE_API_KEY` or the embedding provider quota is exhausted; CLI should surface the missing credential or quota status.
- Collection schema drifts (dimension change or payload key rename); guardrails should prevent corrupted writes and instruct the operator to reindex.
- RetrievalGateway latency spikes; CLI should enforce the caller-provided latency budget and fall back to direct Qdrant when allowed.
- Overlay cleanup fails; TTL sweeps must keep expired overlay entries from leaking into other runs or bloating storage.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Provide a worker-facing CLI command `moonmind rag search` that computes embeddings locally, queries Qdrant (or the RetrievalGateway), deduplicates overlay/canonical hits, and returns both `context_text` and structured `items[]` with scores, sources, offsets, and `trust_class`.
- **FR-002**: Support CLI flags for `--query`, `--top-k`, `--filters key=value ...`, `--overlay {include,skip}`, `--transport {direct,gateway}`, and `--output-file <path>` so workers can control scope without editing code.
- **FR-003**: Enforce guardrails before issuing vector ops: verify Qdrant connectivity, auth, embedding model selection, and collection dimension alignment; fail fast with remediation guidance when validation fails.
- **FR-004**: Emit observability data for every embedding/search/overlay action via queue events and StatsD metrics, tagging records with job/run identifiers, repo, and latency/hit counts.
- **FR-005**: Introduce a RetrievalGateway service method `retrieve_context_pack(query, filters, top_k, budgets)` that returns the same schema as direct Qdrant searches without invoking a generative model.
- **FR-006**: Respect repository/tenant/run payload filters and credential scopes so workers can only access authorized collections, regardless of direct or gateway transport.
- **FR-007**: Provide overlay helpers (`moonmind rag overlay upsert`, `overlay clean`) that write run-scoped vectors with TTL metadata, merge results deterministically by `(path, chunk_hash)`, and prevent stale overlays from leaking between runs.
- **FR-008**: Update worker bootstrap experience (CLI banner + job manifest flag) to advertise when direct RAG is available and fail `moonmind worker doctor` when prerequisites (credentials, connectivity, collection metadata) are missing.

### Key Entities *(include if feature involves data)*

- **ContextPack**: JSON object containing `context_text`, ordered `items[]`, provenance metadata, budget usage, and retrieval timestamp.
- **RetrievalQuery**: Parameters accepted by the CLI/gateway, including query text, filters (`repo`, `tenant`, `run_id`), top-k limit, overlay policy, and budgets.
- **OverlayChunk**: Run-scoped embedding payload with `path`, `chunk_hash`, `trust_class`, TTL/expiry metadata, and pointer to raw text for dedupe merging.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `moonmind rag search` unit tests cover happy path, guardrail failures, and JSON schema validation for `context_text` + `items[]`.
- **SC-002**: Integration tests simulate both direct Qdrant and RetrievalGateway transports, asserting identical outputs for the same query and filters.
- **SC-003**: Overlay tests prove deterministic merge/dedupe, TTL expiry enforcement, and precedence ordering for canonical vs overlay hits.
- **SC-004**: Observability tests confirm StatsD counters/latencies and queue events fire for embeddings, searches, and overlay actions with expected tags.
- **SC-005**: Worker bootstrap tests verify `moonmind worker doctor` blocks execution when prerequisites are missing and surfaces actionable remediation steps.

## Assumptions

- Workers already authenticate to the queue control plane; this feature adds separate Qdrant or RetrievalGateway credentials distributed via existing secret management.
- Embedding providers (Gemini/OpenAI/Ollama) expose consistent APIs so the CLI can swap providers without altering the retrieval contract.
- Qdrant collections already exist and contain canonical repo chunks; this work focuses on retrieval, guardrails, overlay writes, and gateway fallback rather than large-scale reindexing.
- Network access between workers and Qdrant (or RetrievalGateway) is available with acceptable latency for retrieval loops (< 1 second target per query).
- Overlay storage shares the same persistence tier as canonical Qdrant and can rely on TTL sweeps or collection drops for cleanup.
