# Implementation Plan: Direct Worker Qdrant Retrieval Loop

**Branch**: `033-worker-qdrant-rag` | **Date**: 2026-02-20 | **Spec**: `specs/033-worker-qdrant-rag/spec.md`  
**Input**: Feature specification from `/specs/033-worker-qdrant-rag/spec.md`

## Summary

Codex workers need a deterministic, non-generative retrieval loop. We will extend the existing `moonmind` CLI with `rag search` and overlay helpers that compute embeddings locally, query Qdrant directly, and emit a reusable `ContextPack`. A RetrievalGateway fallback keeps governance centralized when workers cannot receive Qdrant credentials. Guardrails (dimension checks, credential validation, connectivity probes) and observability (StatsD + queue events) block unsafe executions and surface telemetry. Workspace overlays let in-progress edits influence retrieval immediately, preventing stale context.

## Technical Context

**Language/Version**: Python 3.11 (MoonMind services, CLI, tests)  
**Primary Dependencies**: `typer` for CLI UX, `qdrant-client` for vector access, `httpx` for gateway calls, `google-generativeai`/`openai`/`ollama` embeddings, StatsD client in `moonmind.workflows.orchestrator.metrics`, FastAPI for RetrievalGateway.  
**Storage**: Qdrant collections for canonical + overlay vectors, Postgres for job metadata, filesystem artifacts for CLI outputs, optional Mem0 for long-term memory (unchanged).  
**Testing**: `./tools/test_unit.sh` (pytest harness) covering `tests/unit/moonmind/rag/*`, new overlay/gateway cases, and FastAPI router tests via dependency overrides; integration smoke via dockerized Qdrant when necessary.  
**Target Platform**: Linux worker containers (Codex CLI) plus FastAPI + Celery services running under Docker Compose.  
**Project Type**: Monorepo Python service with CLI + API.  
**Performance Goals**: < 800 ms query latency per spec, < 1200 token context packs by enforcing budgets, top-k defaults from `RAG_SIMILARITY_TOP_K` (5–8) with deterministic ordering.  
**Constraints**: Retrieval must remain non-generative, guardrails block dimension drift, CLI output must be deterministic (markdown + JSON), separate credentials for queue vs Qdrant/Gateway, overlay dedupe by `(path, chunk_hash)`, fail-open design (lack of retrieval should abort gracefully).  
**Scale/Scope**: Applies to all Codex workers (tens–hundreds of concurrent runs), must support repo/tenant scoping and per-run overlays (~24h TTL) without overwhelming Qdrant control plane.

## Constitution Check

`.specify/memory/constitution.md` is still the placeholder template with unnamed principles. No actionable gates can be derived. Flag **NEEDS CLARIFICATION** for product leadership to ratify principles; proceed under existing MoonMind norms (CLI-first, test-first, observability) and re-run this gate once the constitution is populated.

## Project Structure

### Documentation (this feature)

```text
specs/033-worker-qdrant-rag/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── context-pack.md
│   └── retrieval-gateway.md
├── tasks.md
└── checklists/
```

### Source Code (repository root)

```text
moonmind/
├── cli.py                  # Typer entrypoint exposes rag + worker groups
├── agents/codex_worker/
│   └── worker.py           # Job bootstrap + doctor UX updates
├── rag/
│   ├── __init__.py
│   ├── cli.py              # Subcommand orchestration + env handling
│   ├── context_pack.py     # ContextPack + markdown formatter
│   ├── embedding.py        # Provider-agnostic embedding adapter
│   ├── guardrails.py       # Worker doctor + CLI guardrails
│   ├── overlay.py          # Overlay chunking/upserts
│   ├── overlay_cleanup.py  # TTL cleanup helpers
│   ├── qdrant_client.py    # Search + merge + guardrails
│   ├── service.py          # Shared retrieval orchestration + telemetry
│   ├── settings.py         # Env normalization + filter metadata
│   └── telemetry.py        # StatsD + structured events
api_service/api/routers/
└── retrieval_gateway.py    # FastAPI router exposing /retrieval/context

tests/
├── unit/moonmind/rag/      # CLI, guardrail, overlay, service tests
└── integration/...         # Gateway + CLI parity scenarios (future)

docs/
├── WorkerVectorEmbedding.md
├── WorkerRag.md
└── MemoryArchitecture.md

tools/test_unit.sh          # Required test harness
```

**Structure Decision**: Keep all runtime logic inside the existing `moonmind` package so CLI, worker doctor, and RetrievalGateway share one implementation. FastAPI router depends on `ContextRetrievalService`, preventing divergence between transports. Tests live beside other MoonMind unit suites to inherit fixtures.

## Phase 0 – Research Summary

See `specs/033-worker-qdrant-rag/research.md` for detailed rationale. Key resolved items:
1. **Embedding strategy**: lightweight `moonmind.rag.embedding` wrapper keeps worker installs lean while supporting Google/OpenAI/Ollama without LlamaIndex overhead.  
2. **CLI framework**: Typer is standard across MoonMind CLI and keeps nested commands ergonomic.  
3. **Context schema**: Shared `ContextPack` contract ensures CLI, RetrievalGateway, and dashboards reuse identical payloads.  
4. **Overlay storage**: Default per-run collections (`{collection}__overlay__{run_id}`) satisfy isolation + cleanup. Shared payload markers remain configurable.  
5. **Observability**: Reuse `moonmind.workflows.orchestrator.metrics` StatsD helper and extend queue events for `embedding_*`, `qdrant_*`, and overlay actions.  
Open follow-ups (retry policy, overlay sweep ownership) are addressed in the implementation strategy below.

## Phase 1 – Design Outputs

- **Data Model** (`data-model.md`): defines `ContextPack`, `ContextItem`, `RetrievalQuery`, `OverlayChunk`, telemetry events, and `RetrievalGatewayPolicy`. These entities drive serialization, dedupe keys, and payload filters.  
- **Contracts**:
  - `contracts/context-pack.md`: canonical JSON schema + error envelope for CLI and gateway responses.
  - `contracts/retrieval-gateway.md`: HTTP interface for `/retrieval/context`, covering auth scopes, budgets, and failure codes.
- **Quickstart** (`quickstart.md`): ready-to-run commands for direct vs gateway retrieval, overlay workflows, worker doctor, and `./tools/test_unit.sh` guidance.  
- **No DOC-REQ items** were found in the spec, so a requirements-traceability matrix is not required for this feature.

## Implementation Strategy

### US1 – Worker pulls repo context via CLI (FR-001/FR-002/SC-001)
- Finalize CLI surface in `moonmind/cli.py` + `moonmind/rag/cli.py`: add `--filter key=value` (repeatable), `--top-k`, `--overlay {include,skip}`, `--transport`, `--output-file`, and new `--budget tokens=... latency_ms=...` option that feeds into the existing `budgets` dict so scenario 3.3 is satisfied.  
- `RagRuntimeSettings` already normalizes defaults; ensure `as_filter_metadata()` merges `job_id`/`run_id` with user filters before calling `ContextRetrievalService`.  
- `ContextRetrievalService.retrieve()` remains the single orchestration path. Add deterministic formatting via `ContextPack.build_context_text` (already implemented) and keep `pack.to_json()` stable for `--output-file`.  
- Update CLI UX to print markdown to stdout, warnings to stderr, and to exit non-zero on `CliError` (invalid filters, empty query, etc.).  
- Expand unit coverage in `tests/unit/moonmind/rag/test_cli_utils.py` (filter parsing, budget flag), `test_service.py` (transport selection), and `test_context_pack.py` (markdown rendering, truncation).  
- Tooling: `./tools/test_unit.sh -k rag` must pass locally before PRs.

### US2 – Guardrails block unsafe retrievals (FR-003/FR-004/FR-006/SC-004/SC-005)
- `moonmind/rag/guardrails.py`: enforce Qdrant connectivity + embedding dimension match via `RagQdrantClient.ensure_collection_ready`. Add explicit credential checks and actionable remediation text (e.g., "export GOOGLE_API_KEY"). Cache results for CLI reuse when desired.  
- `moonmind/rag/telemetry.py`: continue emitting StatsD counters/timings; add queue-event publisher (hook into existing orchestrator metrics or event bus) that tags job/run IDs, repo, hits, and budgets.  
- `ContextRetrievalService` should emit telemetry for embedding/search plus overlay actions via context manager `timer`. Introduce retry/backoff loops (two attempts with exponential jitter) around Qdrant search/gateway HTTP calls to satisfy edge-case requirements.  
- `moonmind/agents/codex_worker/worker.py`: surface `job.capabilities.rag` flag and ensure `moonmind worker doctor` runs before job claim, surfacing the same guardrail messaging to humans.  
- Tests: add `tests/unit/moonmind/rag/test_guardrails.py` covering dimension mismatch, missing credentials, and gateway health errors. Add telemetry assertions via `monkeypatch` of StatsD client to confirm counters fire.

### US3 – RetrievalGateway fallback (FR-005/FR-006/SC-002)
- `api_service/api/routers/retrieval_gateway.py`: finalize Pydantic models mirroring the `contracts/retrieval-gateway.md` schema. Enforce repo/tenant allow-lists and optional auth scopes (reuse existing dependency-injected security layer). Return `ContextPack.to_dict()` directly for parity.  
- `ContextRetrievalService._retrieve_via_gateway`: keep HTTP POST to `/retrieval/context`; add latency budget enforcement (`budgets["latency_ms"]`) and friendly error handling (structured CLI errors on HTTP 403/5xx).  
- CLI chooses transport via `settings.resolved_transport()` but allows explicit override `--transport`. Add fallback logic: if gateway call fails with retriable error and direct credentials exist, optionally retry direct (respecting spec’s fail-open guidance).  
- Document gateway behavior + budgets in `docs/MemoryArchitecture.md` and `docs/WorkerRag.md`, referencing the new contract.  
- Tests: create FastAPI router tests using `TestClient` verifying health endpoint, happy path, budget enforcement, and authorization errors. For CLI, add unit test that stubs `_retrieve_via_gateway` to ensure identical context_text. Integration tests (docker compose) remain optional but listed in SC-002.

### US4 – Workspace overlay keeps context fresh (FR-001/FR-007/SC-003)
- `moonmind/rag/overlay.py`: ensure chunking respects `RagRuntimeSettings` chunk size/overlap; include `trust_class="workspace_overlay"` and TTL fields, storing `run_id`.  
- `moonmind/rag/qdrant_client.py`: `_merge_results` already dedupes by `(source, chunk_hash)` with overlay precedence. Extend to respect TTL (skip expired payloads) and allow overlay-specific scoring adjustments if needed.  
- CLI commands `moonmind rag overlay upsert`/`overlay clean` already exist; add job metadata integration (`run_id` detection, error paths) and StatsD events. Provide `--overlay skip` default for automation contexts.  
- Provide overlay cleanup fallback via `overlay_cleanup.py` (drop collection) plus documentation telling workers to run cleanup at job end. Add `moonmind rag overlay list` only if required (not currently).  
- Tests: extend `tests/unit/moonmind/rag/test_overlay.py` (chunking, dedupe, TTL) and `tests/unit/moonmind/rag/test_service.py` to ensure overlay collections are considered when `overlay_policy="include"`.

### Cross-Cutting Deliverables
- **Docs**: update `docs/WorkerRag.md`, `docs/WorkerVectorEmbedding.md`, and `docs/MemoryArchitecture.md` to reflect CLI command set, guardrail requirements, overlay flows, and RetrievalGateway contract references.  
- **Quickstart**: already drafted; keep examples current when CLI flags change (e.g., new `--budget`).  
- **Telemetry & Metrics**: align event names with spec (`embedding_aborted`, `rag.search.latency_ms`, `rag.search.hits`). Provide sample payloads in docs for observability onboarding.  
- **Testing & CI**: baseline unit coverage via `./tools/test_unit.sh`. Future integration/regression coverage (SC-002) will use dockerized Qdrant + FastAPI gateway, triggered manually until GH Actions workflow is added.

## Risks & Mitigations
- **Embedding dimension drift**: Mitigated via guardrails and `moonmind worker doctor`; add runbook link in error message.  
- **Gateway latency spikes**: Implement configurable retry/backoff and budgets; CLI surfaces fallback guidance.  
- **Overlay bloat**: Use per-run collections + TTL plus CLI cleanup command; consider server-side cron sweep if overlays accumulate (tracked in research open question).  
- **Credential exposure**: keep worker tokens separate from Qdrant API keys; RetrievalGateway uses scoped bearer tokens validated server-side.

## Next Steps

1. Finish updating CLI + rag modules per sections above.  
2. Write/refresh unit tests via `./tools/test_unit.sh -k rag`.  
3. Update docs + quickstart to match final UX.  
4. Re-run constitution gate once `.specify/memory/constitution.md` is populated.

