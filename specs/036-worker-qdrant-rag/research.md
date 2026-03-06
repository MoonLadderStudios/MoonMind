# Research: Direct Worker Qdrant Retrieval Loop

## Decision 1: Embedding helper vs existing LlamaIndex Settings
- **Decision**: Build a lightweight embedding wrapper (`moonmind.rag.embedding`) that consumes shared env vars but does not require spinning up LlamaIndex services.
- **Rationale**: Worker CLI must run outside FastAPI context; LlamaIndex Settings expects pre-built indices and extra dependencies. Thin wrapper reduces cold-start time and gives tighter control over guardrails.
- **Alternatives Considered**: Reusing `moonmind.rag.retriever.QdrantRAG` (requires LlamaIndex + service settings) or proxying `/context` (adds generative hop). Rejected due to dependency overhead and violation of non-generative loop.

## Decision 2: CLI framework selection
- **Decision**: Use Typer for the new `moonmind rag` CLI (mounted under an existing `moonmind` console entrypoint).
- **Rationale**: Typer integrates with Click (already indirect dep), supports subcommands, typed options, and contextual help. Keeps CLI consistent with other MoonMind tools.
- **Alternatives Considered**: Argparse (more boilerplate, no nested subcommands) and Docopt (less maintained). Typer chosen for developer productivity and readability.

## Decision 3: Retrieval transport contract
- **Decision**: Define a `ContextPack` schema shared by the CLI and RetrievalGateway: `context_text`, `items[]`, `filters`, `budgets`, `usage`, and `telemetry` fields.
- **Rationale**: A single schema ensures parity between direct Qdrant and gateway responses, aligning with `docs/WorkerRag.md` and `docs/MemoryArchitecture.md`.
- **Alternatives Considered**: Distinct schemas per transport (risk of divergence) or embedding LlamaIndex `NodeWithScore` JSON (contains internal fields, lacks citations order). Shared schema is safer and easier to audit.

## Decision 4: Overlay storage strategy
- **Decision**: Default to per-run overlay collections named `{repo}__overlay__{run_id}` with TTL-based cleanup; optionally allow payload markers when `QDRANT_COLLECTION_MODE=shared`.
- **Rationale**: Per-run collections isolate data, make cleanup trivial, and prevent filter mistakes. Shared collection mode remains available for long-lived deployments.
- **Alternatives Considered**: Always using payload markers (simpler metrics but harder cleanup). Chosen approach balances isolation and optional shared mode.

## Decision 5: Observability sink
- **Decision**: Reuse `moonmind.workflows.orchestrator.metrics.MetricsClient` for StatsD emission and extend queue event payloads with `vector_action` records.
- **Rationale**: Avoid new dependencies, keep metrics naming consistent, and leverage existing exponential backoff behavior when StatsD unreachable.
- **Alternatives Considered**: Adding third-party telemetry clients (more deps) or silent logging (misses requirement). Existing MetricsClient best meets goals.

## Open Questions
- Retries/backoff policy for RetrievalGateway latency spikes (default: 2 retries with jitter, revisit after prototyping).
- Whether overlay TTL sweeps should run inside worker CLI or a server-side cron (plan assumes CLI handles per-run cleanup, server sweeps optional).
